"""Domain guessing for entities WITHOUT hjemmeside. Accept only if target orgnr appears on the site.
Usage: uv run --with httpx --with selectolax --with idna python web_domain_guess.py work/sample_without_site.json work/domain_guess.json
"""
import asyncio, json, re, sys, socket, pathlib, unicodedata, time
from urllib.parse import urlsplit
import httpx
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from web_extract import extract_page, reg_domain
from web_fetch_study import fetch, pick_subpages, client_side_target, UA, HTML_DIR

FREEMAIL = {"gmail.com", "hotmail.com", "hotmail.no", "online.no", "yahoo.com", "yahoo.no", "outlook.com", "live.no",
            "live.com", "icloud.com", "me.com", "msn.com", "frisurf.no", "broadpark.no", "start.no", "c2i.net",
            "getmail.no", "lyse.net", "altibox.no", "telia.no", "bluezone.no", "mail.com", "googlemail.com",
            "epost.no", "sensewave.com", "tele2.no", "chello.no", "nextgentel.com", "ebnett.no", "haugnett.no",
            "loqal.no", "netcom.no", "hotmail.co.uk", "aol.com", "protonmail.com", "proton.me", "outlook.no",
            "yahoo.co.uk", "gmx.com", "gmx.net", "mail.ru", "tdcspace.dk", "frisurf.net", "enivest.net",
            "tussa.com", "tdc.no", "monsternett.no", "nordnet.no", "kvinnherad.net"}
SUFFIX_RX = re.compile(r"\b(asa|as|enk|nuf|sa|da|ans|ba|ks|iks|kf|sti|stiftelse|i likvidasjon|under avvikling|"
                       r"under tvangsavvikling|konkursbo)\b\.?", re.I)

def translit(s, variant=0):
    m = [{"æ": "ae", "ø": "o", "å": "a", "ä": "a", "ö": "o", "ü": "u", "é": "e"},
         {"æ": "ae", "ø": "oe", "å": "aa", "ä": "ae", "ö": "oe", "ü": "u", "é": "e"}][variant]
    return "".join(m.get(c, c) for c in s)

def name_slugs(name):
    """Return dict of slug variants for a legal name."""
    n = unicodedata.normalize("NFKC", name.lower())
    n = re.split(r"\bv/|\bavd\.?\b|\bc/o\b", n)[0]
    n = SUFFIX_RX.sub(" ", n)
    n = n.replace("&", " og ").replace("+", " ")
    toks = [t for t in re.findall(r"[a-z0-9æøåäöüé]+", n) if t]
    if not toks: return {}
    t0 = [translit(t, 0) for t in toks]
    t1 = [translit(t, 1) for t in toks]
    out = {"joined": "".join(t0), "hyphen": "-".join(t0) if len(t0) > 1 else None,
           "joined_alt": "".join(t1) if t1 != t0 else None, "hyphen_alt": None, "idn": None}
    if any(c in "".join(toks) for c in "æøåäöü"):
        try:
            out["idn"] = "".join(toks).encode("idna").decode("ascii")
        except Exception:
            pass
    return {k: v for k, v in out.items() if v and len(v) <= 63}

def candidates(ent, max_cands=6):
    c = []
    em = ent.get("epostadresse")
    if em and "@" in em:
        d = em.split("@", 1)[1].lower().strip()
        if d not in FREEMAIL:
            c.append(("email_domain", d))
    sl = name_slugs(ent["navn"])
    for key, tld in (("joined", ".no"), ("hyphen", ".no"), ("idn", ".no"), ("joined", ".com"),
                     ("joined_alt", ".no"), ("hyphen", ".com")):
        if sl.get(key):
            c.append(("name_" + key, sl[key] + tld))
    seen, res = set(), []
    for k, d in c:
        if d not in seen:
            seen.add(d); res.append((k, d))
    return res[:max_cands]

async def resolves(host):
    loop = asyncio.get_running_loop()
    for h in (host, "www." + host):
        try:
            await asyncio.wait_for(loop.getaddrinfo(h, 443, type=socket.SOCK_STREAM), 5)
            return h
        except Exception:
            continue
    return None

async def study(client, ent, sem):
    async with sem:
        org = ent["organisasjonsnummer"]
        rec = {"orgnr": org, "navn": ent["navn"], "orgform": ent["orgform"], "brreg_email": ent.get("epostadresse"),
               "requests": 0, "dns_lookups": 0, "cands": []}
        for kind, dom in candidates(ent):
            cr = {"kind": kind, "domain": dom}
            rec["dns_lookups"] += 1
            h = await resolves(dom)
            cr["resolves"] = bool(h)
            if not h:
                rec["cands"].append(cr); continue
            p, html = await fetch(client, f"https://{h}/", rec, "home")
            if html is None:
                p, html = await fetch(client, f"http://{h}/", rec, "home_http")
            cr.update(status=p.get("status"), final_url=p.get("final_url"), error=p.get("error"))
            if html:
                cs = client_side_target(html, p.get("final_url") or dom)
                if cs:
                    p2, h2 = await fetch(client, cs, rec, "home2")
                    if h2: html, p = h2, p2
                    cr["client_redirect"] = cs
                x = extract_page(html, p.get("final_url") or dom, org, ent["navn"])
                fn = f"guess_{org}__{dom}.html"
                (HTML_DIR / fn).write_text(html)
                cr.update(file=fn, title=x["title"], target_home=x["target_in_text"] > 0 or x["target_in_html"] > 0,
                          other_orgnrs=x["other_orgnrs_kw"], name_match=x.get("name_match"),
                          name_full=x.get("name_full_phrase"), placeholder=x["placeholder_signals"],
                          final_domain=reg_domain(urlsplit(p.get("final_url") or "").hostname or dom))
                cr["target_sub"] = False
                # look on up to 2 subpages if not found on home and page isn't parked
                if not cr["target_home"] and not x["placeholder_signals"]:
                    for kind2, u, _ in pick_subpages(html, p.get("final_url") or f"https://{dom}/")[:2]:
                        p3, h3 = await fetch(client, u, rec, kind2)
                        if h3:
                            y = extract_page(h3, u, org, ent["navn"])
                            cr["other_orgnrs"] = sorted(set(cr["other_orgnrs"]) | set(y["other_orgnrs_kw"]))
                            if y["target_in_text"] or y["target_in_html"]:
                                cr["target_sub"] = kind2
                                break
            rec["cands"].append(cr)
            if cr.get("target_home") or cr.get("target_sub"):
                rec["hit"] = dom
                break
        return rec

async def main(inp, outp):
    ents = json.loads(pathlib.Path(inp).read_text())
    async with httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.5"},
                                 follow_redirects=True, timeout=httpx.Timeout(10.0), max_redirects=8) as client:
        sem = asyncio.Semaphore(6)
        res = await asyncio.gather(*(study(client, e, sem) for e in ents))
    pathlib.Path(outp).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("done", len(res), "http", sum(r["requests"] for r in res), "dns", sum(r["dns_lookups"] for r in res),
          "hits", sum(1 for r in res if r.get("hit")))

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
