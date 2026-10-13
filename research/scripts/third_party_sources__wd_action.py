import json, urllib.request, urllib.parse, sys, time
UA = "nordic-company-agent/0.1 (research prototype)"
API = "https://www.wikidata.org/w/api.php"
def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    t=time.time()
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r), url, time.time()-t
def search(orgs):
    q = "haswbstatement:" + "|".join(f"P2333={o}" for o in orgs)
    j, url, dt = get({"action":"query","list":"search","srsearch":q,"srlimit":"max","format":"json","srprop":"","srnamespace":"0"})
    return [s["title"] for s in j["query"]["search"]], j["query"]["searchinfo"]["totalhits"], len(url), dt
def entities(qids):
    out = {}
    for i in range(0, len(qids), 50):
        j, url, dt = get({"action":"wbgetentities","ids":"|".join(qids[i:i+50]),"props":"labels|claims|sitelinks|descriptions","languages":"nb|nn|en","format":"json"})
        out.update(j["entities"])
    return out
if __name__ == "__main__":
    orgs = json.load(open(sys.argv[1])); orgs = [o if isinstance(o,str) else o["orgnr"] for o in orgs][:100]
    qids, tot, ulen, dt = search(orgs)
    print("search hits", tot, "url_len", ulen, "secs", round(dt,2))
    ents = entities(qids) if qids else {}
    for q,e in ents.items():
        p = [c["mainsnak"]["datavalue"]["value"] for c in e["claims"].get("P2333",[]) if "datavalue" in c["mainsnak"]]
        print(q, p, e["labels"].get("nb",e["labels"].get("en",{})).get("value"), list(e.get("sitelinks",{}).keys())[:5])
    if len(sys.argv)>2: json.dump(ents, open(sys.argv[2],"w"), ensure_ascii=False)
