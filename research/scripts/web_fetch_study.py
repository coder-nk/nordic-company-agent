"""Fetch robots.txt + homepage + up to 3 contact/about/privacy/terms pages per entity.

Raw HTML is cached under research/work/html/ so analysis can be re-run offline.
Usage: uv run --with httpx --with selectolax python web_fetch_study.py work/sample_with_site.json work/fetch_with_site.json
"""
import asyncio, json, re, sys, time, pathlib, urllib.robotparser
from urllib.parse import urljoin, urlsplit
import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

UA = "nordic-company-agent/0.1 (research prototype)"
ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML_DIR = ROOT / "work" / "html"
HTML_DIR.mkdir(parents=True, exist_ok=True)
HOST_DELAY = 0.6
MAX_BYTES = 3_000_000

SUBPAGE_PATTERNS = [  # (kind, regex on href path + anchor text, base score)
    ("contact", re.compile(r"kontakt|contact|finn[- ]oss|find[- ]us|ta[- ]kontakt", re.I), 10),
    ("about", re.compile(r"om[- _]?oss|about|hvem[- ]er[- ]vi|om[- ]firma|om[- ]selskapet|selskapet|company|bedriften|om-[a-z]+$", re.I), 8),
    ("privacy", re.compile(r"personvern|privacy|cookie|gdpr", re.I), 5),
    ("terms", re.compile(r"vilk[aå]r|betingelser|kj[oø]psvilk|salgsvilk|terms|conditions|handelsbeting", re.I), 6),
    ("impressum", re.compile(r"impressum|imprint|juridisk|legal", re.I), 6),
]

def decode(content: bytes, ctype: str) -> tuple[str, str]:
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    cands = []
    if m: cands.append(m.group(1))
    m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", content[:4000], re.I)
    if m2: cands.append(m2.group(1).decode("ascii", "ignore"))
    cands += ["utf-8", "cp1252"]
    for c in cands:
        try:
            return content.decode(c), c
        except Exception:
            continue
    return content.decode("utf-8", "replace"), "utf-8-replace"

def norm_start_url(h: str) -> str:
    h = h.strip()
    if not re.match(r"https?://", h, re.I):
        h = "https://" + h
    return h

class HostGate:
    def __init__(self):
        self.last: dict[str, float] = {}
        self.locks: dict[str, asyncio.Lock] = {}
    async def wait(self, host):
        lock = self.locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            t = self.last.get(host, 0) + HOST_DELAY - now
            if t > 0: await asyncio.sleep(t)
            self.last[host] = time.monotonic()

gate = HostGate()

async def fetch(client, url, rec, label):
    host = urlsplit(url).hostname or ""
    await gate.wait(host)
    t0 = time.monotonic()
    rec["requests"] += 1
    out = {"label": label, "url": url}
    try:
        r = await client.get(url)
        hist = [(str(h.url), h.status_code) for h in r.history]
        rec["requests"] += len(r.history)  # each redirect hop is an outbound request
        content = r.content[:MAX_BYTES]
        text, enc = decode(content, r.headers.get("content-type", ""))
        out.update(status=r.status_code, final_url=str(r.url), redirects=hist,
                   ctype=r.headers.get("content-type"), bytes=len(r.content), encoding=enc,
                   server=r.headers.get("server"), x_powered=r.headers.get("x-powered-by"),
                   elapsed=round(time.monotonic() - t0, 2))
        return out, text
    except Exception as e:
        out.update(error=f"{type(e).__name__}: {e}"[:300], elapsed=round(time.monotonic() - t0, 2))
        return out, None

META_REFRESH_RX = re.compile(r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?\s*\d+\s*;\s*url\s*=\s*['\"]?([^'\">\s]+)", re.I)
JS_LOC_RX = re.compile(r"(?:window\.|document\.|top\.|self\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]|location\.replace\(\s*['\"]([^'\"]+)['\"]", re.I)
FRAME_RX = re.compile(r"<(?:frame|iframe)[^>]+src=[\"']?([^\"'>\s]+)", re.I)

def client_side_target(html, base):
    small = len(html) < 6000
    m = META_REFRESH_RX.search(html[:20000])
    if m:
        return urljoin(base, m.group(1))
    if small:
        m = JS_LOC_RX.search(html)
        if m:
            return urljoin(base, m.group(1) or m.group(2))
    if re.search(r"<frameset", html, re.I) or (small and re.search(r"<iframe", html, re.I)):
        m = FRAME_RX.search(html)
        if m and not re.search(r"youtube|vimeo|google|facebook", m.group(1), re.I):
            return urljoin(base, m.group(1))
    return None

def pick_subpages(html, base_url, k=3):
    tree = HTMLParser(html)
    base_host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    best = {}
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith(("mailto:", "tel:", "javascript:", "#")): continue
        u = urljoin(base_url, href).split("#")[0]
        sp = urlsplit(u)
        if sp.scheme not in ("http", "https"): continue
        h = (sp.hostname or "").removeprefix("www.")
        if h != base_host: continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)$", sp.path, re.I): continue
        txt = (a.text(separator=" ", strip=True) or "")[:60]
        hay = sp.path + " " + txt
        for kind, rx, score in SUBPAGE_PATTERNS:
            if rx.search(hay):
                s = score - sp.path.count("/") * 0.5 - (2 if sp.query else 0)
                if kind not in best or s > best[kind][0]:
                    best[kind] = (s, u, txt)
                break
    order = ["contact", "about", "terms", "privacy", "impressum"]
    return [(k_, best[k_][1], best[k_][2]) for k_ in order if k_ in best]

async def study(client, ent, sem):
    async with sem:
        org = ent["organisasjonsnummer"]
        rec = {"orgnr": org, "navn": ent["navn"], "orgform": ent["orgform"],
               "hjemmeside": ent.get("hjemmeside"), "requests": 0, "pages": []}
        start = norm_start_url(ent["hjemmeside"])
        sp = urlsplit(start)
        # robots.txt
        rurl = f"{sp.scheme}://{sp.netloc}/robots.txt"
        rr, rtext = await fetch(client, rurl, rec, "robots")
        if rr.get("error") and sp.scheme == "https":
            # retry once on http (many old sites have broken TLS)
            rurl = f"http://{sp.netloc}/robots.txt"
            rr2, rtext2 = await fetch(client, rurl, rec, "robots_http")
            rec["robots_https_error"] = rr["error"]
            rr, rtext = rr2, rtext2
            start = "http://" + start.split("://", 1)[1]
        rec["robots"] = {k: rr.get(k) for k in ("url", "status", "final_url", "error")}
        allowed = True
        if rtext is not None and rr.get("status") == 200 and "text/html" not in (rr.get("ctype") or ""):
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(rtext.splitlines())
            allowed = rp.can_fetch(UA, start)
            rec["robots"]["crawl_delay"] = rp.crawl_delay(UA)
            rec["robots"]["n_lines"] = len(rtext.splitlines())
            (HTML_DIR / f"{org}__robots.txt").write_text(rtext[:20000])
        rec["robots"]["allowed"] = allowed
        if rr.get("error") and rr["error"].startswith(("ConnectError", "ConnectTimeout")) and "Name or service" in rr["error"]:
            rec["dns_fail"] = True
        if not allowed:
            rec["skipped"] = "robots_disallow"
            return rec
        hp, html = await fetch(client, start, rec, "home")
        rec["pages"].append(hp)
        if html is None and start.startswith("https://"):
            alt = "http://" + start.split("://", 1)[1]
            hp2, html = await fetch(client, alt, rec, "home_http")
            rec["pages"].append(hp2)
            hp = hp2
        if html is None:
            return rec
        (HTML_DIR / f"{org}__home.html").write_text(html)
        hp["file"] = f"{org}__home.html"
        final = hp.get("final_url") or start
        # client-side redirect / frameset (common on old Norwegian small-business sites)
        cs = client_side_target(html, final)
        if cs:
            rec["client_redirect"] = cs
            p2, h2 = await fetch(client, cs, rec, "home_clientredirect")
            rec["pages"].append(p2)
            if h2:
                (HTML_DIR / f"{org}__home2.html").write_text(h2)
                p2["file"] = f"{org}__home2.html"
                html, hp, final = h2, p2, p2.get("final_url") or cs
        if hp.get("status") != 200 or "html" not in (hp.get("ctype") or "html"):
            return rec
        subs = pick_subpages(html, final)
        rec["subpage_candidates"] = subs
        fetched = 0
        for kind, u, txt in subs:
            if fetched >= 3: break
            if u.rstrip("/") == final.rstrip("/"): continue
            p, h = await fetch(client, u, rec, kind)
            fetched += 1
            rec["pages"].append(p)
            if h:
                fn = f"{org}__{kind}.html"
                (HTML_DIR / fn).write_text(h)
                p["file"] = fn
        return rec

async def main(inp, outp):
    ents = json.loads(pathlib.Path(inp).read_text())
    limits = httpx.Limits(max_connections=20)
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                                          "Accept-Language": "nb-NO,nb;q=0.9,no;q=0.8,en;q=0.5"},
                                 follow_redirects=True, timeout=httpx.Timeout(10.0), limits=limits,
                                 max_redirects=8) as client:
        sem = asyncio.Semaphore(8)
        res = await asyncio.gather(*(study(client, e, sem) for e in ents))
    pathlib.Path(outp).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("done", len(res), "requests", sum(r["requests"] for r in res))

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
