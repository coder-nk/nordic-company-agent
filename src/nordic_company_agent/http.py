"""Budgeted, polite async HTTP client.

Every outbound HTTP request (each redirect hop, each retry, each robots.txt fetch)
is counted against a hard per-run budget, so a run can never exceed the challenge's
outbound-request limit. Requests are also bounded by a run deadline, per-host
concurrency and a per-host minimum interval.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import ipaddress
import json
import socket
import time
import urllib.robotparser
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from . import __version__

USER_AGENT = (
    f"nordic-company-agent/{__version__} "
    "(+https://data.brreg.no open-data client; deterministic company profiler)"
)
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


# Per-task request counter: set by callers that need exact attribution under concurrency.
REQUEST_COUNTER: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("nca_request_counter", default=None)


class BudgetExceeded(RuntimeError):
    """The run-level outbound request budget is exhausted."""


class DeadlineExceeded(RuntimeError):
    """The run-level wall-clock deadline has passed."""


class RobotsDisallowed(RuntimeError):
    pass


class UnsafeURL(RuntimeError):
    """URL scheme/host is not allowed (non-http(s), private/loopback/link-local address)."""


_SAFE_HOST_CACHE: dict[str, bool] = {}


async def assert_public_url(url: str) -> None:
    """SSRF guard: only http(s) to hosts whose every resolved address is globally routable."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL(f"unsupported URL {url!r}")
    if parts.username or parts.password:
        raise UnsafeURL("credentials in URL are not allowed")
    host = parts.hostname.lower().rstrip(".")
    cached = _SAFE_HOST_CACHE.get(host)
    if cached is None:
        try:
            ip = ipaddress.ip_address(host)
            addrs = [ip]
        except ValueError:
            loop = asyncio.get_running_loop()
            try:
                infos = await asyncio.wait_for(loop.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM), 8)
            except Exception as exc:  # DNS failure is a transport error for the caller
                raise httpx.ConnectError(f"DNS resolution failed for {host}: {exc}") from exc
            addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
        cached = bool(addrs) and all(a.is_global and not a.is_multicast for a in addrs)
        _SAFE_HOST_CACHE[host] = cached
    if not cached:
        raise UnsafeURL(f"host {host} resolves to a non-public address")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class CallRecord:
    source: str
    method: str
    url: str
    status: int | None
    started_at: str
    elapsed_ms: int
    bytes: int
    error: str | None = None
    attempt: int = 1
    redirect_hop: int = 0


@dataclass
class FetchResult:
    url: str  # URL originally requested (with query)
    final_url: str  # URL after redirects
    status: int
    headers: dict[str, str]
    content: bytes
    retrieved_at: datetime
    redirects: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def text(self) -> str:
        enc = None
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            enc = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"')
        for candidate in (enc, "utf-8", "cp1252", "latin-1"):
            if not candidate:
                continue
            try:
                return self.content.decode(candidate)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


class _NoCookies(httpx.Cookies):
    def set_cookie_header(self, request):  # pragma: no cover - httpx internal hook
        return None

    def extract_cookies(self, response):  # pragma: no cover - httpx internal hook
        return None


class _HostGate:
    """Per-host concurrency + minimum spacing between request starts."""

    def __init__(self, concurrency: int, min_interval: float):
        self.sem = asyncio.Semaphore(concurrency)
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def __aenter__(self):
        await self.sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = self._next_start - now
            self._next_start = max(now, self._next_start) + self.min_interval
        if wait > 0:
            await asyncio.sleep(wait)
        return self

    async def __aexit__(self, *exc):
        self.sem.release()


# Official APIs that are designed for automated access get higher, but still polite, limits.
HOST_POLICY: dict[str, tuple[int, float]] = {
    "data.brreg.no": (6, 0.08),
    "api.gleif.org": (2, 0.5),
    "query.wikidata.org": (1, 1.0),
    "www.wikidata.org": (1, 0.5),
    "ws.geonorge.no": (2, 0.25),
}
DEFAULT_HOST_POLICY = (2, 0.5)


class BudgetedClient:
    def __init__(
        self,
        max_requests: int = 2000,
        deadline: float | None = None,  # absolute time.monotonic() value
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        max_body_bytes: int = 3_000_000,
        max_connections: int = 128,
        record_calls: bool = True,
    ):
        self.max_requests = max_requests
        self.deadline = deadline
        self.max_body_bytes = max_body_bytes
        self.record_calls = record_calls
        self._used = 0
        self._count_lock = asyncio.Lock()
        self._gates: dict[str, _HostGate] = {}
        self.calls: list[CallRecord] = []
        self.error_count = 0
        self.by_source: Counter[str] = Counter()
        self.by_host: Counter[str] = Counter()
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._robots_reason: dict[str, str] = {}
        self._client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 8.0)),
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,nn;q=0.7,en;q=0.6",
            },
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections // 2),
            cookies=None,
            # Bind to IPv4: some networks resolve AAAA first without a working IPv6 route.
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0,
                                               limits=httpx.Limits(max_connections=max_connections,
                                                                   max_keepalive_connections=max_connections // 2)),
        )
        # Never send or keep cookies: every request is anonymous and reproducible.
        self._client.cookies = _NoCookies()

    # ------------------------------------------------------------------ budget
    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self._used)

    def time_left(self) -> float:
        if self.deadline is None:
            return float("inf")
        return self.deadline - time.monotonic()

    async def _reserve(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise DeadlineExceeded("run deadline reached")
        async with self._count_lock:
            if self._used >= self.max_requests:
                raise BudgetExceeded(f"request budget of {self.max_requests} exhausted")
            self._used += 1
        counter = REQUEST_COUNTER.get()
        if counter is not None:
            counter[0] += 1

    def _gate(self, host: str) -> _HostGate:
        gate = self._gates.get(host)
        if gate is None:
            conc, interval = HOST_POLICY.get(host, DEFAULT_HOST_POLICY)
            gate = self._gates[host] = _HostGate(conc, interval)
        return gate

    # ----------------------------------------------------------------- request
    async def get(
        self,
        url: str,
        *,
        source: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_redirects: int = 5,
        retries: int = 1,
        respect_robots: bool = False,
        max_bytes: int | None = None,
        public_only: bool = False,
        timeout: float | None = None,
    ) -> FetchResult:
        return await self.request(
            "GET", url, source=source, params=params, headers=headers,
            max_redirects=max_redirects, retries=retries,
            respect_robots=respect_robots, max_bytes=max_bytes, public_only=public_only, timeout=timeout,
        )

    async def post(self, url: str, *, source: str, data: Any = None,
                   headers: dict[str, str] | None = None, retries: int = 1,
                   timeout: float | None = None) -> FetchResult:
        return await self.request("POST", url, source=source, data=data, headers=headers,
                                  max_redirects=0, retries=retries, timeout=timeout)

    async def request(
        self,
        method: str,
        url: str,
        *,
        source: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
        max_redirects: int = 5,
        retries: int = 1,
        respect_robots: bool = False,
        max_bytes: int | None = None,
        public_only: bool = False,
        timeout: float | None = None,
    ) -> FetchResult:
        """`public_only=True` (use for any company-controlled URL) enforces the SSRF guard on
        every hop, never sends cookies, and caps redirects/body size."""
        if params:
            url = str(httpx.URL(url, params=params))
        original = url
        redirects: list[str] = []
        current = url
        hop = 0
        while True:
            if public_only:
                await assert_public_url(current)
            if respect_robots and not await self.allowed_by_robots(current, source=source):
                parts = urlsplit(current)
                reason = self._robots_reason.get(f"{parts.scheme}://{parts.netloc}", "disallowed by robots.txt")
                raise RobotsDisallowed(f"{current} ({reason})")
            res = await self._send_with_retries(method, current, source=source, headers=headers,
                                                data=data, retries=retries, hop=hop,
                                                max_bytes=max_bytes or self.max_body_bytes, timeout=timeout)
            if res.status in (301, 302, 303, 307, 308) and "location" in res.headers and hop < max_redirects:
                nxt = urljoin(current, res.headers["location"])
                if not nxt.startswith(("http://", "https://")):
                    break
                redirects.append(current)
                current = nxt
                hop += 1
                if res.status == 303:
                    method, data = "GET", None
                continue
            break
        res.url = original
        res.final_url = current
        res.redirects = redirects
        return res

    async def _send_with_retries(self, method, url, *, source, headers, data, retries, hop, max_bytes,
                                 timeout: float | None = None) -> FetchResult:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= retries:
            attempt += 1
            await self._reserve()
            host = urlsplit(url).hostname or ""
            self.by_source[source] += 1
            self.by_host[host] += 1
            started = utcnow()
            t0 = time.monotonic()
            try:
                async with self._gate(host):
                    res = await self._send_once(method, url, headers=headers, data=data, max_bytes=max_bytes,
                                                timeout=timeout)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                self._record(CallRecord(source, method, url, None, started.isoformat(),
                                             int((time.monotonic() - t0) * 1000), 0,
                                             error=type(exc).__name__, attempt=attempt, redirect_hop=hop))
                if attempt <= retries and self.remaining > 0 and self.time_left() > 5:
                    await asyncio.sleep(1.0 * attempt)
                    continue
                raise
            self._record(CallRecord(source, method, url, res.status, started.isoformat(),
                                         int((time.monotonic() - t0) * 1000), len(res.content),
                                         attempt=attempt, redirect_hop=hop))
            if res.status in (429, 502, 503, 504) and attempt <= retries and self.remaining > 0:
                delay = 2.0 * attempt
                ra = res.headers.get("retry-after", "")
                if ra.isdigit():
                    delay = min(float(ra), 20.0)
                if self.time_left() > delay + 5:
                    await asyncio.sleep(delay)
                    continue
            return res
        raise last_exc or RuntimeError("unreachable")

    async def download(self, url: str, path, *, source: str, headers: dict[str, str] | None = None,
                       max_redirects: int = 3, read_timeout: float = 120.0) -> dict[str, Any]:
        """Stream a (large) official bulk file to `path`. Counts one request per hop, honours the
        run deadline between chunks. Returns {status, final_url, bytes, sha256, headers, retrieved_at}."""
        current = url
        for hop in range(max_redirects + 1):
            await self._reserve()
            host = urlsplit(current).hostname or ""
            self.by_source[source] += 1
            self.by_host[host] += 1
            started = utcnow()
            t0 = time.monotonic()
            h = hashlib.sha256()
            size = 0
            async with self._client.stream("GET", current, headers=headers or {},
                                           timeout=httpx.Timeout(read_timeout, connect=10.0)) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    self._record(CallRecord(source, "GET", current, resp.status_code, started.isoformat(),
                                            int((time.monotonic() - t0) * 1000), 0, redirect_hop=hop))
                    current = urljoin(current, resp.headers["location"])
                    continue
                with open(path, "wb") as fh:
                    async for chunk in resp.aiter_raw():
                        fh.write(chunk)
                        h.update(chunk)
                        size += len(chunk)
                        if self.deadline is not None and time.monotonic() >= self.deadline:
                            raise DeadlineExceeded("run deadline reached during bulk download")
                self._record(CallRecord(source, "GET", current, resp.status_code, started.isoformat(),
                                        int((time.monotonic() - t0) * 1000), size, redirect_hop=hop))
                return {"status": resp.status_code, "final_url": current, "bytes": size, "sha256": h.hexdigest(),
                        "headers": {k.lower(): v for k, v in resp.headers.items()}, "retrieved_at": utcnow()}
        raise RuntimeError("too many redirects")

    def _record(self, rec: CallRecord) -> None:
        if self.record_calls:
            self.calls.append(rec)
        elif rec.error or (rec.status or 0) >= 500:
            self.error_count += 1

    async def _send_once(self, method, url, *, headers, data, max_bytes, timeout: float | None = None) -> FetchResult:
        req_headers = dict(headers or {})
        extra = {"timeout": httpx.Timeout(timeout, connect=min(timeout, 8.0))} if timeout else {}
        async with self._client.stream(method, url, headers=req_headers, **extra,
                                       content=data if isinstance(data, (bytes, str)) else None,
                                       data=data if isinstance(data, dict) else None) as resp:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    truncated = True
                    break
            content = b"".join(chunks)[:max_bytes]
            return FetchResult(
                url=url, final_url=url, status=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                content=content, retrieved_at=utcnow(), truncated=truncated,
            )

    # ------------------------------------------------------------------ robots
    async def allowed_by_robots(self, url: str, *, source: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_locks[origin]:
            if origin not in self._robots:
                parser: urllib.robotparser.RobotFileParser | None = None
                try:
                    try:
                        res = await self._send_with_retries("GET", origin + "/robots.txt", source=source,
                                                            headers={"Accept": "text/plain,*/*"}, data=None,
                                                            retries=0, hop=0, max_bytes=500_000)
                    except httpx.TimeoutException:
                        # One more (counted) attempt before treating the whole origin as unreachable.
                        await asyncio.sleep(1.0)
                        res = await self._send_with_retries("GET", origin + "/robots.txt", source=source,
                                                            headers={"Accept": "text/plain,*/*"}, data=None,
                                                            retries=0, hop=0, max_bytes=500_000, timeout=20.0)
                    if res.status in (301, 302, 307, 308) and "location" in res.headers:
                        # One counted hop to follow a robots.txt redirect (e.g. http -> https).
                        loc = urljoin(origin + "/robots.txt", res.headers["location"])
                        await assert_public_url(loc)
                        res = await self._send_with_retries("GET", loc, source=source,
                                                            headers={"Accept": "text/plain,*/*"}, data=None,
                                                            retries=0, hop=1, max_bytes=500_000)
                    ctype = res.headers.get("content-type", "")
                    if res.status == 200 and "html" not in ctype.lower():
                        parser = urllib.robotparser.RobotFileParser()
                        parser.parse(res.text.splitlines())
                    elif res.status == 200:
                        parser = None  # an HTML page served as robots.txt: no rules
                    elif res.status in (401, 403) or res.status >= 500 or res.status == 429:
                        # RFC 9309 §2.3.1.3/§2.3.1.4: server errors mean "assume complete disallow".
                        parser = urllib.robotparser.RobotFileParser()
                        parser.disallow_all = True
                        self._robots_reason[origin] = f"robots.txt returned HTTP {res.status}: treated as disallow"
                except (BudgetExceeded, DeadlineExceeded):
                    raise
                except httpx.TimeoutException:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.disallow_all = True  # unreachable robots.txt -> do not crawl this run
                    self._robots_reason[origin] = "robots.txt timed out: site unreachable this run"
                except Exception:
                    parser = None  # e.g. TLS/connection refused: the page fetch itself will fail
                self._robots[origin] = parser
        parser = self._robots[origin]
        if parser is None:
            return True
        if getattr(parser, "disallow_all", False):
            return False
        return parser.can_fetch("nordic-company-agent", url)

    def robots_crawl_delay(self, url: str) -> float | None:
        parts = urlsplit(url)
        parser = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        if parser is None:
            return None
        try:
            d = parser.crawl_delay("nordic-company-agent")
            return float(d) if d is not None else None
        except Exception:
            return None

    def stats(self) -> dict[str, Any]:
        return {
            "requests_used": self._used,
            "requests_limit": self.max_requests,
            "by_source": dict(self.by_source),
            "by_host": dict(self.by_host.most_common(25)),
            "errors": self.error_count + sum(1 for c in self.calls if c.error or (c.status or 0) >= 500),
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
