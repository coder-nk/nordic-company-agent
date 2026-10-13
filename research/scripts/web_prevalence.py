"""Estimate share of entities with hjemmeside / epostadresse in Enhetsregisteret (random registration windows, all orgforms)."""
import json, random, time, urllib.request, urllib.parse, datetime, collections
UA = "nordic-company-agent/0.1 (research prototype)"
random.seed(4242)
C = collections.Counter(); H = collections.Counter(); E = collections.Counter(); ED = collections.Counter(); FB = 0
n = 0
for i in range(12):
    a = datetime.date(1995, 1, 1) + datetime.timedelta(days=random.randint(0, 11400))
    b = a + datetime.timedelta(days=4)
    q = {"size": 200, "fraRegistreringsdatoEnhetsregisteret": a.isoformat(), "tilRegistreringsdatoEnhetsregisteret": b.isoformat()}
    url = "https://data.brreg.no/enhetsregisteret/api/enheter?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
        ents = json.load(r).get("_embedded", {}).get("enheter", [])
    time.sleep(0.7)
    for e in ents:
        f = e["organisasjonsform"]["kode"]; C[f] += 1; n += 1
        if e.get("hjemmeside"):
            H[f] += 1
            if "facebook.com" in e["hjemmeside"].lower(): FB += 1
        em = e.get("epostadresse")
        if em:
            E[f] += 1
            d = em.split("@")[-1].lower()
            ED["freemail" if d in {"gmail.com","hotmail.com","online.no","yahoo.com","outlook.com","live.no","icloud.com","hotmail.no","yahoo.no","me.com","live.com","msn.com"} else "own_domain"] += 1
print("N", n, "with hjemmeside", sum(H.values()), "facebook hjemmeside", FB, "with email", sum(E.values()), dict(ED))
for f, c in C.most_common():
    print(f"{f:6} n={c:4} hjemmeside={H[f]:3} ({100*H[f]/c:4.1f}%) email={E[f]:3} ({100*E[f]/c:4.1f}%)")
