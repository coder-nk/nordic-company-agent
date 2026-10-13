import json, urllib.request, urllib.parse, sys, time
UA = "nordic-company-agent/0.1 (research prototype)"
def build(orgs):
    vals = " ".join(f'"{o}"' for o in orgs)
    return """SELECT ?orgnr ?item ?itemLabel ?itemDescription
 (GROUP_CONCAT(DISTINCT ?website; separator="|") AS ?websites)
 (SAMPLE(?inception) AS ?inception1) (SAMPLE(?dissolved) AS ?dissolved1)
 (GROUP_CONCAT(DISTINCT ?hqLabel; separator="|") AS ?hq)
 (GROUP_CONCAT(DISTINCT ?parentOrg; separator="|") AS ?parentOrgnrs)
 (GROUP_CONCAT(DISTINCT ?parentLabel; separator="|") AS ?parents)
 (GROUP_CONCAT(DISTINCT ?ceoLabel; separator="|") AS ?ceos)
 (GROUP_CONCAT(DISTINCT ?industryLabel; separator="|") AS ?industries)
 (GROUP_CONCAT(DISTINCT ?ticker; separator="|") AS ?tickers)
 (GROUP_CONCAT(DISTINCT ?isin; separator="|") AS ?isins)
 (SAMPLE(?lei) AS ?lei1) (SAMPLE(?logo) AS ?logo1)
 (GROUP_CONCAT(DISTINCT ?fb; separator="|") AS ?facebook)
 (GROUP_CONCAT(DISTINCT ?ig; separator="|") AS ?instagram)
 (GROUP_CONCAT(DISTINCT ?li; separator="|") AS ?linkedin)
 (GROUP_CONCAT(DISTINCT ?tw; separator="|") AS ?twitter)
 (GROUP_CONCAT(DISTINCT ?yt; separator="|") AS ?youtube)
 (SAMPLE(?nowiki) AS ?nowiki1) (SAMPLE(?enwiki) AS ?enwiki1)
WHERE {
  VALUES ?orgnr { %s }
  ?item wdt:P2333 ?orgnr .
  OPTIONAL { ?item wdt:P856 ?website }
  OPTIONAL { ?item wdt:P571 ?inception }
  OPTIONAL { ?item wdt:P576 ?dissolved }
  OPTIONAL { ?item wdt:P159 ?hqI . ?hqI rdfs:label ?hqLabel FILTER(LANG(?hqLabel)="nb") }
  OPTIONAL { ?item wdt:P749 ?parentI . ?parentI rdfs:label ?parentLabel FILTER(LANG(?parentLabel) IN ("nb","en")) OPTIONAL { ?parentI wdt:P2333 ?parentOrg } }
  OPTIONAL { ?item wdt:P169 ?ceoI . ?ceoI rdfs:label ?ceoLabel FILTER(LANG(?ceoLabel)="nb") }
  OPTIONAL { ?item wdt:P452 ?indI . ?indI rdfs:label ?industryLabel FILTER(LANG(?industryLabel)="nb") }
  OPTIONAL { ?item p:P414 ?ex . ?ex pq:P249 ?ticker }
  OPTIONAL { ?item wdt:P946 ?isin }
  OPTIONAL { ?item wdt:P1278 ?lei }
  OPTIONAL { ?item wdt:P154 ?logo }
  OPTIONAL { ?item wdt:P2013 ?fb }
  OPTIONAL { ?item wdt:P2003 ?ig }
  OPTIONAL { ?item wdt:P4264 ?li }
  OPTIONAL { ?item wdt:P2002 ?tw }
  OPTIONAL { ?item wdt:P2397 ?yt }
  OPTIONAL { ?nowiki schema:about ?item ; schema:isPartOf <https://no.wikipedia.org/> }
  OPTIONAL { ?enwiki schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "nb,nn,en". }
} GROUP BY ?orgnr ?item ?itemLabel ?itemDescription""" % vals
if __name__ == "__main__":
    orgs = json.load(open(sys.argv[1])); orgs = [o if isinstance(o,str) else o["orgnr"] for o in orgs][:100]
    q = build(orgs)
    endpoint = sys.argv[3] if len(sys.argv)>3 else "https://query.wikidata.org/sparql"
    t=time.time()
    req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({"query": q}).encode(), headers={"User-Agent": UA, "Accept": "application/sparql-results+json", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = urllib.request.urlopen(req, timeout=90); j = json.load(r)
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.headers.get("retry-after")); sys.exit(1)
    print("secs", round(time.time()-t,2), "rows", len(j["results"]["bindings"]), "query chars", len(q))
    for b in j["results"]["bindings"]:
        print({k: v["value"][:80] for k,v in b.items() if v["value"]})
    if len(sys.argv)>2: json.dump(j, open(sys.argv[2],"w"), ensure_ascii=False, indent=1)
