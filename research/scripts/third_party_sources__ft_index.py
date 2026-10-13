import json, urllib.request, urllib.parse, time, collections
UA = "nordic-company-agent/0.1 (research prototype)"
B = "https://api.finanstilsynet.no/registry/v2/legal-entities/filter"
idx = {}; page = 1; total = None; reqs = 0
while True:
    url = B + "?" + urllib.parse.urlencode({"query": "", "entityType": "Company", "countryCodes": "NOR", "pageSize": 50, "page": page})
    j = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)); reqs += 1
    total = j["total"]
    for e in j["legalEntities"]:
        if e.get("organisationNumber"):
            idx[e["organisationNumber"]] = {"id": e["legalEntityId"], "name": e["name"], "lei": e.get("leiCode"), "licences": sorted({l["licenceType"]["code"] for l in (e.get("licences") or []) if (l.get("licensedEntity") or {}).get("legalEntityId") == e["legalEntityId"]})}
    if page == 1: json.dump(j, open("/home/boss/Documents/nordic-company-agent/research/samples/third_party_sources__finanstilsynet_filter_page1.json","w"), ensure_ascii=False, indent=1)
    if page * 50 >= total or not j["legalEntities"]: break
    page += 1; time.sleep(0.5)
print("total", total, "indexed", len(idx), "requests", reqs)
json.dump(idx, open("/tmp/ncr/ft_index.json","w"), ensure_ascii=False)
c = collections.Counter(code for v in idx.values() for code in v["licences"])
print(c.most_common(25))
