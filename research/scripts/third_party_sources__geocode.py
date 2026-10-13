import json, re, urllib.request, urllib.parse, time, collections, sys
UA = "nordic-company-agent/0.1 (research prototype)"
BASE = "https://ws.geonorge.no/adresser/v1/sok"
NREQ = 0
def get(params):
    global NREQ
    NREQ += 1
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.load(r)
    time.sleep(0.5)
    return j, url
SKIP = re.compile(r"^(c/o|co |postboks|pb\.?\s|boks |p\.?b\.?\s|postuttak)", re.I)
ADDR = re.compile(r"^(?P<street>.+?)\s+(?P<nr>\d+)\s*(?P<letter>[A-Za-zÆØÅæøå])?(?:\s*[-–]\s*\d+[A-Za-z]?)?(?:\s*,.*)?$")
def pick_line(lines):
    for l in reversed(lines or []):
        l = l.strip()
        if not l or SKIP.match(l): continue
        if ADDR.match(l): return l
    return None
FIELDS = "adresser.adressetekst,adresser.adressenavn,adresser.nummer,adresser.bokstav,adresser.postnummer,adresser.kommunenummer,adresser.representasjonspunkt,adresser.objtype,adresser.oppdateringsdato"
def geocode(addr):
    if not addr or addr.get("landkode","NO") != "NO": return {"status":"not_applicable","reason":"foreign or missing address"}
    line = pick_line(addr.get("adresse"))
    pn = addr.get("postnummer"); kn = addr.get("kommunenummer")
    if not line: return {"status":"not_available","reason":"no street-number line (PO box / c/o / missing)"}
    m = ADDR.match(line)
    street, nr, letter = m.group("street"), m.group("nr"), (m.group("letter") or "")
    # 1 structured exact
    p = {"adressenavn": street, "nummer": nr, "postnummer": pn, "treffPerSide": 5, "utkoordsys": 4258, "filtrer": FIELDS}
    if letter: p["bokstav"] = letter.upper()
    j, url = get(p)
    hits = j.get("adresser", [])
    if len(hits) == 1: return {"status":"available","method":"structured_exact","url":url,"hit":hits[0]}
    # 2 free text within kommune, non fuzzy
    j, url = get({"sok": f"{street} {nr}{letter}", "kommunenummer": kn, "treffPerSide": 5, "utkoordsys": 4258, "filtrer": FIELDS})
    hits = j.get("adresser", [])
    if len(hits) == 1: return {"status":"available","method":"freetext_kommune","url":url,"hit":hits[0]}
    if len(hits) > 1:
        same_pn = [h for h in hits if h["postnummer"] == pn]
        if len(same_pn) == 1: return {"status":"available","method":"freetext_kommune_pn","url":url,"hit":same_pn[0]}
        return {"status":"ambiguous","n":len(hits),"url":url}
    # 3 fuzzy
    j, url = get({"sok": f"{street} {nr}{letter}", "kommunenummer": kn, "fuzzy": "true", "treffPerSide": 3, "utkoordsys": 4258, "filtrer": FIELDS})
    hits = j.get("adresser", [])
    if hits and hits[0]["nummer"] == int(nr): return {"status":"available","method":"fuzzy_low_confidence","url":url,"hit":hits[0], "input": line}
    return {"status":"not_found","input":line}
if __name__ == "__main__":
    ents = json.load(open("/tmp/ncr/brreg_rand100.json"))
    res = []; c = collections.Counter()
    for e in ents:
        r = geocode(e.get("forretningsadresse") or e.get("postadresse"))
        r["orgnr"] = e["organisasjonsnummer"]; r["addr"] = (e.get("forretningsadresse") or e.get("postadresse") or {}).get("adresse")
        res.append(r); c[(r["status"], r.get("method"))] += 1
    print(c, "requests", NREQ)
    for r in res:
        if r["status"] != "available" or r.get("method") != "structured_exact": print(r["orgnr"], r["status"], r.get("method"), r["addr"], r.get("hit",{}).get("adressetekst"), r.get("reason",""))
    json.dump(res, open("/tmp/ncr/geo_res.json","w"), ensure_ascii=False, indent=1)
