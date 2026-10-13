import json, urllib.request, urllib.parse, time, sys, re
UA = "nordic-company-agent/0.1 (research prototype)"
def gleif_batch(orgnrs):
    vals = []
    for o in orgnrs:
        vals += [o, f"{o[:3]} {o[3:6]} {o[6:]}"]
    q = urllib.parse.urlencode({"filter[entity.registeredAs]": ",".join(vals), "page[size]": "200"})
    url = "https://api.gleif.org/api/v1/lei-records?" + q
    t = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.api+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.load(r)
    return url, j, time.time()-t
if __name__ == "__main__":
    orgs = json.load(open(sys.argv[1]))
    orgs = [o if isinstance(o,str) else o["orgnr"] for o in orgs][:int(sys.argv[2]) if len(sys.argv)>2 else 100]
    url, j, dt = gleif_batch(orgs)
    print("url_len", len(url), "secs", round(dt,2), "total", j["meta"]["pagination"]["total"])
    for d in j["data"]:
        a = d["attributes"]; e = a["entity"]
        print(d["id"], e["registeredAs"], e["registeredAt"]["id"], a["registration"]["status"], e["status"], e["legalName"]["name"], e["jurisdiction"])
    if len(sys.argv)>3: json.dump(j, open(sys.argv[3],"w"), ensure_ascii=False, indent=1)
