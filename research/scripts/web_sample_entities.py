"""Sample random-ish entities from Enhetsregisteret for the website study.

Uses random registration-date windows per organisasjonsform (the API sorts
alphabetically, so page-walking alone gives "A..." names only).
Output: research/work/sample_with_site.json, sample_without_site.json
"""
import json, random, time, urllib.request, urllib.parse, datetime, pathlib

UA = "nordic-company-agent/0.1 (research prototype)"
BASE = "https://data.brreg.no/enhetsregisteret/api/enheter"
OUT = pathlib.Path(__file__).resolve().parent.parent / "work"
random.seed(20261013)

def get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    time.sleep(0.6)
    return d.get("_embedded", {}).get("enheter", []), url

def rand_window(start_year=1995, days=30):
    a = datetime.date(start_year, 1, 1)
    b = datetime.date(2026, 8, 1)
    d0 = a + datetime.timedelta(days=random.randint(0, (b - a).days))
    return d0.isoformat(), (d0 + datetime.timedelta(days=days)).isoformat()

def collect(form, n, want_site, extra=None, per_page=2, days=30, max_tries=25):
    got, tries = [], 0
    while len(got) < n and tries < max_tries:
        tries += 1
        f, t = rand_window(days=days)
        p = {"organisasjonsform": form, "size": 200, "konkurs": "false",
             "underAvvikling": "false",
             "fraRegistreringsdatoEnhetsregisteret": f,
             "tilRegistreringsdatoEnhetsregisteret": t}
        if extra: p.update(extra)
        try:
            ents, url = get(p)
        except Exception as e:
            print("ERR", form, e); continue
        cands = [e for e in ents if bool(e.get("hjemmeside")) == want_site
                 and not e.get("slettedato")]
        random.shuffle(cands)
        for e in cands[:per_page]:
            if len(got) >= n: break
            got.append({k: e.get(k) for k in ("organisasjonsnummer", "navn", "hjemmeside",
                        "epostadresse", "telefon", "mobil", "antallAnsatte",
                        "registreringsdatoEnhetsregisteret")} | {
                "orgform": form,
                "naering": (e.get("naeringskode1") or {}).get("beskrivelse"),
                "poststed": (e.get("forretningsadresse") or e.get("postadresse") or {}).get("poststed"),
                "query": url})
        print(form, want_site, len(got), "/", n, "window", f, "hits", len(cands), "of", len(ents))
    return got

def main():
    with_site = []
    for form, n, days in [("AS", 20, 20), ("ENK", 6, 20), ("FLI", 6, 60), ("SA", 3, 365),
                          ("ASA", 1, 2000), ("NUF", 1, 60), ("STI", 1, 365), ("ANS", 1, 365),
                          ("DA", 1, 365), ("KOMM", 1, 9000), ("BRL", 1, 365)]:
        with_site += collect(form, n, True, days=days)
    (OUT / "sample_with_site.json").write_text(json.dumps(with_site, ensure_ascii=False, indent=1))

    without = []
    without += collect("AS", 15, False, extra={"fraAntallAnsatte": 1}, days=30)
    without += collect("AS", 5, False, days=10)
    without += collect("ENK", 5, False, extra={"fraAntallAnsatte": 1}, days=60)
    without += collect("FLI", 3, False, days=60)
    without += collect("SA", 2, False, days=365)
    (OUT / "sample_without_site.json").write_text(json.dumps(without, ensure_ascii=False, indent=1))
    print(len(with_site), len(without))

if __name__ == "__main__":
    main()
