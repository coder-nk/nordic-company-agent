import json, random, time, urllib.request, datetime, sys
UA = "nordic-company-agent/0.1 (research prototype)"
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
random.seed(int(sys.argv[2]) if len(sys.argv)>2 else 42)
n = int(sys.argv[1]) if len(sys.argv)>1 else 100
start = datetime.date(1995,1,1); end = datetime.date(2026,10,1)
out = []
while len(out) < n:
    d = start + datetime.timedelta(days=random.randrange((end-start).days))
    url = f"https://data.brreg.no/enhetsregisteret/api/enheter?fraRegistreringsdatoEnhetsregisteret={d}&tilRegistreringsdatoEnhetsregisteret={d}&size=1"
    j = get(url); time.sleep(0.4)
    tot = j.get("page",{}).get("totalElements",0)
    if tot == 0: continue
    i = random.randrange(min(tot, 9999))
    j = get(url.replace("size=1", f"size=1&page={i}")); time.sleep(0.4)
    e = j["_embedded"]["enheter"][0]
    out.append({"orgnr": e["organisasjonsnummer"], "navn": e["navn"], "form": e["organisasjonsform"]["kode"], "ansatte": e.get("antallAnsatte"), "web": e.get("hjemmeside"), "konkurs": e.get("konkurs"), "avvikling": e.get("underAvvikling"), "naering": (e.get("naeringskode1") or {}).get("kode"), "regdato": e.get("registreringsdatoEnhetsregisteret"), "kommune": (e.get("forretningsadresse") or e.get("postadresse") or {}).get("kommune")})
    print(len(out), out[-1]["orgnr"], out[-1]["form"], out[-1]["navn"], file=sys.stderr)
json.dump(out, open("/tmp/ncr/random_sample.json","w"), ensure_ascii=False, indent=1)
