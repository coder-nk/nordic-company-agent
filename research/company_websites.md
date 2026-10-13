# Company websites: an empirical study of fact extraction with strict same-company verification

Date: 2026-10-13. Author: research subagent ("company_websites" topic).
Every number below comes from live fetches made today with User-Agent `nordic-company-agent/0.1 (research prototype)`. Limits were ≤2 req/s per host, a 10 s timeout, and robots.txt honoured.

Scripts are in `research/scripts/web_*.py`. Cached raw HTML is in `research/work/html/` (about 500 files, not for commit). Trimmed test fixtures are in `research/samples/company_websites__*.html`, indexed by `research/samples/company_websites__index.json`.

---

## 0. TL;DR

* **Only ~11–15 % of active entities have `hjemmeside` in Enhetsregisteret.**
  * Our own sample: 2,148 random active entities, 14.9 %.
  * By form: AS 13 %, ENK 11 %, FLI 29 %.
  * In the Builderr universe: AS 8.8 % (see `spec_and_fields.md`).
* **Of entities that do have `hjemmeside` (n=161):**
  * 81 % return a homepage with HTTP 200.
  * 8 % are dead domains (DNS failure).
  * 5 % sit behind bot walls (Cloudflare or Vercel).
  * 2.5 % are robots-disallowed: Facebook URLs stored as `hjemmeside`, or a Shopify store with `Disallow: /`.
  * About 11 % of the reachable ones are parked, placeholder or hijacked.
* **The target org number appears on the site for only 33/161 (20 %).** These are pages fetched within ≤5 requests (home plus ≤3 subpages).
  * Where it was first found: home 23, contact 4, about 4, terms 1, privacy 1.
  * Home alone finds 70 % of hits. Home + contact finds 82 %. Home + contact + about finds 94 %.
* **The rest are more often another company's site than you would expect.** Of the 131 reachable sites, 14 (11 %) show another valid org number in an "Org.nr" context and *not* the target. Examples:
  * group or parent sites
  * sister companies
  * property-SPV → manager
  * club → main club
  * local chapter → national organisation
  * franchise → chain
  * a hosting company's parking page
* **Recommended policy v2** (simulated in `web_policy.py`, early stopping) publishes a verified website for 77/161 entities (48 %):
  * L1, orgnr on site: 33 (20 %).
  * L2, registry `hjemmeside` + prominent name + ≥1 registry corroboration: 44 (27 %).
  * Average cost: **2.94 HTTP requests per company with `hjemmeside`** (max 5), counting robots.txt and redirect hops.
* **Domain guessing for entities *without* `hjemmeside`** (n=115):
  * Strict orgnr-on-site hits: 7 (6 %).
  * With strict "name + registry corroboration" (phone, email or address match) on top: 13 (11 %).
  * Name-only acceptance would add about the same number of **false positives**: German GmbH, US salon, domain broker, UK school, etc.
  * The `epostadresse` domain is a strong *candidate generator* but a weak *proof*. 16 of 25 own-domain e-mails pointed to the employer, accountant, property manager, ISP, parent organisation or a typo-squat (unipet.se → unibet.se).
* **No usable third-party shortcut:**
  * Norid RDAP shows only registrar and tech contact, not the holder's orgnr. Its terms prohibit commercial use, and Builderr's policy excludes it.
  * Wikidata (P2333→P856) matched 13/276 sampled entities. All 13 already had `hjemmeside`; it found 0 of the 115 without one.

---

## 1. Samples and method

| Set | n | How drawn | File |
|---|---|---|---|
| With `hjemmeside`, stratified | 42 | random registration-date windows per orgform (AS 20, ENK 6, FLI 6, SA 3, ASA/NUF/STI/ANS/DA/KOMM/BRL 1 each), `konkurs=false&underAvvikling=false` | `work/sample_with_site.json` |
| With `hjemmeside`, larger | 119 | same, seed 777 (AS 70, ENK 15, FLI 20, SA 5, NUF 4, STI 3, ASA/DA/ANS/KOMM/ORGL/BRL 2, STAT 1) | `work/sample_large_with_site.json` |
| Without `hjemmeside` A | 30 | AS with `fraAntallAnsatte=1` (15), random AS (5), ENK (5), FLI (3), SA (2) | `work/sample_without_site.json` |
| Without `hjemmeside` B | 85 | 60 natural random (AS 24, ENK 21, FLI 8, …) + 25 with own-domain `epostadresse` | `work/sample_without_site2.json` |
| Prevalence | 2,148 | 12 random 4-day registration windows, all forms | `work/prevalence.txt` |

Sampling gotcha: `/enhetsregisteret/api/enheter` sorts by name. Paging alone yields only "A…" names, and `(page+1)*size ≤ 10000`. Random `fraRegistreringsdatoEnhetsregisteret`/`tilRegistreringsdatoEnhetsregisteret` windows give a far better spread.

Pipeline per entity (`web_fetch_study.py`):

1. Normalise `hjemmeside`: it **never** has a scheme (161/161), often has `www.`, sometimes uppercase (`Www.balab.no`) or a path (`norian.no/eiendomsforvaltning`, Facebook URLs).
2. Fetch `https://host/robots.txt`. On a connect or TLS error, retry over `http://`.
3. Fetch the homepage, following redirects (max 8).
4. Follow client-side redirects: meta refresh, `window.location=` on pages <6 KB, frameset/iframe.
5. Pick ≤3 same-site subpages (contact > about > terms > privacy > impressum) and fetch them.
6. Run the extractor (`web_extract.py`).
7. Look up any other org numbers found via the Brreg API (research only).

The study fetched 3 subpages for every site. `web_policy.py` then simulates the recommended early-stopping order on the cached pages.

---

## 2. Reachability, redirects, TLS, robots

### Homepage outcome (n=161 with `hjemmeside`)

| Outcome | n | % |
|---|---|---|
| HTTP 200 | 131 | 81.4 |
| DNS failure (domain gone) | 13 | 8.1 |
| HTTP 403 (Cloudflare challenge or plain 403) | 6 | 3.7 |
| robots.txt disallows (3 Facebook URLs + 1 Shopify `Disallow: /`) | 4 | 2.5 |
| Connection error, timeout or protocol error | 4 | 2.5 |
| HTTP 429 (Vercel Security Checkpoint) | 1 | 0.6 |
| HTTP 500 / 404 | 2 | 1.2 |

### Redirects (of 146 with a final URL)

* 62 had at least one 3xx: http→https, www toggle or path.
* 42 ended on a different host of the same registrable domain (www toggle).
* **15 (9 %) ended on a different registrable domain.** Examples:
  * `nordvestrevisjon.no→robust.biz` (rebrand)
  * `andebuapotek.no→gokstadapotek.no` (chain)
  * `yogainordmarka.no→centrumyoga.no` (someone else)
  * `nyenor.no→webhuset.no/parkering` (parked)
  * `hopetravel.com→hugedomains.com` (for sale)
  * `de4.no→habberstad.no` (rebrand)
  * `haman.no→hamantravel.com`
  * `2hd.no→2hd.uk`
  * `brittmark.no→brittmarkseljebo.no`
* 5 sites only work over plain http.

### Client-side redirects

* `lbl.as` is a 107-byte page containing `window.location="http://www.lesja-bulldozerlag.no/"`. That site shows **another** orgnr: LESJA BULLDOZERLAG AS, 913860349, not BULLGRUPPEN AS.
* `sakarina.com` serves 403 and then a JS redirect to a Chinese spam site (expired-domain hijack).

### TLS

* 25/161 had an https failure on the first request:
  * DNS 12
  * TLS (`CERTIFICATE_VERIFY_FAILED`, `TLSV1_UNRECOGNIZED_NAME`) 6
  * connect 6
  * timeout 1
* Falling back to `http://` recovered 8 (4 TLS, 4 connect).
* Recommendation: try https first, then http once. Do **not** disable certificate verification. Record `transport: "http"` as evidence quality.

### robots.txt

* Status counts: 200 110, 404 25, 403 6, 400 1, 500 1, 429 1, unreachable 17.
* Crawl-delay seen on 4 sites (5, 5, 5 and 10 s).
* Rules (RFC 9309):
  * 2xx → parse.
  * 4xx (incl. 403) → allow all.
  * 5xx, timeout or 429 → treat as disallow for this run.
* Fetch once per host per run; it counts as one request.
* `facebook.com/robots.txt` disallows unknown agents, so a Facebook `hjemmeside` should become a `social.facebook` fact (registry-sourced), not a website to crawl.

### Latency

* 348 page fetches: p50 0.47 s, p90 1.67 s, p95 2.26 s, max 10 s (timeout).
* 119 sites took 63 s at 8-way concurrency.

### Encoding

* Decode with the charset from the HTTP header, else `<meta charset>`, else utf-8, else cp1252.
* Old Norwegian sites are often ISO-8859-1/cp1252 without a header charset (e.g. an Adobe GoLive 4 site).

---

## 3. Parked, placeholder and hijacked detection

Seen among the 131 reachable sites:

| Kind | Example | Signal |
|---|---|---|
| Host parking page (with the HOST's orgnr, phone and socials!) | nyenor.no → `webhuset.no/parkering`, title "Domene er parkert \| Webhuset", footer "Org.nr 981 532 848" | URL contains `/parkering`, title regex; another orgnr in keyword context |
| For-sale broker | hopetravel.com → HugeDomains | host in parking list; "This domain is for sale" |
| Parked (generic) | "www.haukinvest.no is parked", "Parkert hos Loopia", "Hosted by one.com", "Domain Registered at Safenames" | title/text regex |
| Inactive notice | filias.no "Denne siden er ikke aktiv. Se: www.irenreppen.no" | regex |
| Under construction / maintenance | aqm.no "Under construction", bondal.com "is under maintenance", thecard.no "Midlertidig stengt", egelikunst.no "(offline)" | regex |
| Default server page | innovationbox.no "Index of /", olaflarsen.no "New Page 1" (FrontPage), cypher.no "My WordPress Site" (default WP, 361 chars) | regex; "My WordPress Site" title |
| Account suspended | haukomanlegg.no → `/cgi-sys/suspendedpage.cgi` | URL + "Account Suspended" |
| Expired-domain hijack | sakarina.com → Chinese video spam | >50 CJK characters, `lang` mismatch, name absent |
| Bot wall (NOT dead) | tent.no, regjeringen.no "Just a moment..." (403); askermonument.no "Attention Required! \| Cloudflare"; norwegianproperty.no "Vercel Security Checkpoint" (429); badgeland.no "Bekrefter at du ikke er en bot!" | regex; set availability `blocked` and keep the last known value |
| JS app shell / image-only | blanktjern.no (emergent.sh React, `<div id="root"></div>`), hynano.no, greatbygg.no (background image only), dvinachildhood.no (One.com editor) | visible text < 200 chars; `id="root\|__next\|app"` |

Regexes are in `web_extract.py` (`PARKING_HOSTS`, `PARKED_TEXT_RX`, `BOT_WALL_RX`, `CJK_RX`). Lessons:

* **Weak phrases cause false positives on real sites.** "Coming soon" fired on anteroz.com (a real site with a coming-soon product) and on a HitecVision subpage. Apply weak phrases (coming soon, kommer snart, under construction) only to `<title>` or to pages with <1,500 chars of text. Strong phrases (for sale, parked, suspended, "Index of /", default page) can match the first 3,000 chars of text.
* **Parking pages carry the host's identity.** They have the host's orgnr, phone, socials and JSON-LD Organization (Webhuset AS). Without detection that is a direct wrong-company publication.
* A parked or dead registry website is itself a useful, dated fact: `website.status = parked|dead|blocked`, observed on a given date. It is also a refresh signal.

---

## 4. Org number on the site

### Where it was found

Of 33 L1 entities:

* First found on: home 23, contact 4, about 4, terms 1, privacy 1.
* Pages with a hit (multi-count): home 23, contact 15, about 11, privacy 8, terms 5.
* 16 sites had it in a footer-classed element.
* 7 entities had extra occurrences in raw HTML beyond the visible text (hrefs, JSON-LD, hidden elements). For 1 of them (2hd) the orgnr was **only** in an `href`.

By orgform:

* L1 is common for KOMM 3/3, STI 4/4, SA 3/8, FLI 5/26.
* It is rare for AS 13/79 and ENK 3/21.

### Formats seen

118 valid orgnr occurrences in visible text, all org numbers:

| Format | n |
|---|---|
| `923 609 016` (3-3-3, space or nbsp) | 88 |
| `923609016` plain | 19 |
| `923 609 016 MVA` | 8 |
| `923609016MVA` / `991761624 mva` | 2 |
| `NO 923 609 016 MVA` | 1 |

Labels seen: `Org.nr`, `Org. nr.`, `Org nr:`, `Orgnr:`, `Orgnr.`, `org:`, `Organisasjonsnummer`, `Organisasjonsnr.:`, `Organisasjonsnr:`, `Foretaksnr.`, `MVA/VAT no.`, `Org. no.`, `org.nr.` inside "Faktura ønskes som EHF (org.nr.975 576 205)", EHF/PEPPOL `0192:820852982`. Also "opphavsrett BRA TRE AS 922362491" with no label at all.

60/61 target occurrences in text had an org keyword within 45 chars before the number (or an NO/MVA wrapper).

### Regexes (Python `re`)

```python
SEP = r"[    .\-]"                     # space, nbsp, thin space, narrow nbsp, dot, hyphen
ORGNR_RX = re.compile(r"(?<!\d)(?P<pre>NO" + SEP + r"?)?(?P<num>[89]\d{2}(?:" + SEP + r"?\d{3}){2})(?P<mva>" + SEP + r"?MVA)?(?!\d)", re.I)
# then: digits = re.sub(r"\D","",num); require mod11_ok(digits)
def mod11_ok(n):  # weights 3,2,7,6,5,4,3,2 ; r = 11 - sum%11 ; r==11 -> 0 ; r==10 -> invalid
    ...
def target_rx(org):      # tolerant search for ONE known orgnr
    return re.compile(r"(?<!\d)" + (SEP + "?").join(org) + r"(?!\d)")
ORG_KEYWORD_RX = r"(org(?:anisasjons)?[\s.\-]*(?:n(?:umme)?r|no|number)\b\.?|orgnr|foretaksregisteret|organisasjonsnummer|reg(?:istration)?\.?\s*(?:no|nr|number)|business\s*(?:id|reg)|\bvat\b|\bmva\b|foretaksnr|enhetsregisteret)"
```

How to apply them:

* Search the target with `target_rx` in **both** visible text and raw HTML.
* Raw HTML also catches attributes, JSON-LD and links to registries. Example: 2hd.uk "About" only has `https://virksomhet.brreg.no/nb/oppslag/enheter/932973855` in an `href`.
* Also extract `BRREG_LINK_RX = (brreg.no|proff.no|purehelp.no|forvalt.no)…([89]\d{8})`. A company linking to its own registry page is strong self-identification. Do not fetch those targets.
* JSON-LD `taxID` and `identifier{propertyID:"orgnr"}` appeared on 2 sites, and both were **another** company: atticum.no → ATTICUM INCUBATOR AS, fabritius.no → FABRITIUS GRUPPEN AS. Treat it like a footer orgnr (it identifies the site owner).

### "Org-number block" for multi-company pages (`orgnr_block()` in `web_extract.py`)

1. Find the smallest DOM element whose text contains the target.
2. Grow it towards ancestors while the text stays ≤700 chars **and contains no other valid orgnr**.
3. E-mails, phones and postcode/place lines inside this block can be attributed to the target even on shared pages.

It worked on all 33 L1 cases. The key example is the advokateneiba47.no footer, which lists 3 law firms, each with address, phone, e-mail and orgnr. Only "Os Advokater DA … Telefon: 450 20 113 E-post: thomas@advokat-os.no Org.nr: 920 209 319" is attributed. The block is flagged `isolated=False` when the smallest element already holds several orgnrs (e.g. Polarfakta's privacy page with 4 data-processor orgnrs). In that case, do not take contact facts from the block.

---

## 5. How sites end up showing a different company (wrong-company taxonomy)

Seen in this study: 14/131 reachable registry sites show only a foreign orgnr, and 3 more show the target plus others.

| Pattern | Example (target → site shows) | Rule that catches it |
|---|---|---|
| Hosting parking page | NYE NOR AS → WEBHUSET AS 981532848 | parked detection; foreign orgnr |
| Franchise / chain site | BUTIKKDRIFT ANITA CHRISTIANSEN AS → narvesen.no; ANDEBU APOTEK AS → gokstadapotek.no (lists the pharmacy's address!) | name not prominent; redirect to another domain without a title or og:site_name match |
| Group / parent | ESPIRA VANNVERKSDAMMEN AS → Espira Gruppen AS 991926577; GARDERMOEN HOLDING AS → Fabritius Gruppen AS 983728340; GER NOR AS → "Norrein Gruppen" | foreign orgnr (group context); name not prominent |
| Sister company | ATTICUM NETTLØSNINGER AS → ATTICUM INCUBATOR AS 982574544 (footer + JSON-LD taxID); SAKURA CAFETERIE AS → SAKURA STRØMMEN AS 915465242; KANELSNURREN AS → also shows BRØD BAKERNE AS 990834490 | foreign orgnr, or L1-multi |
| Property SPV → manager or tenant | NÆRINGSPARKVEGEN 2 AS (c/o Malling & Co) → malling.no; HASLE EIENDOM AS → STUB EIENDOM AS; NIKKIHOMPEN 52 AS → altenlodge.no (privacy names BO DRIFT AS); OLLEBUKTA 2 AS e-mail → seng.no, a shop *located at* Ollebukta 2 sharing the phone | address-named entity regex; `c/o <company>` in registry address; foreign orgnr |
| Club / chapter → umbrella | TERTNES FOTBALL DAMER → TERTNES IDRETTSLAG ALLIANSE 981303059 (**both** declare `www.tertnes.no` in Brreg); FAGFORBUNDET BERGEN AVD. 56 → FAGFORBUNDET 971075252; BALESTRAND OG HØYANGER TURLAG on dnt.no (OK: own orgnr on its subpage); BLODKREFTFORENINGEN INNLANDET → national site; SØR-ØST POLITIDISTRIKT → politiet.no | foreign orgnr; name not prominent |
| Shared office site | ADVOKATFIRMAET MARIA OFTEDAL AS → Eikesdal Administrasjon AS 997144481; OS ADVOKATER DA + 2 other firms | foreign orgnr / orgnr block |
| ENK → successor AS | LANDSTRYKER BRINCKER (ENK) → "TBRC AS" 933432351 | foreign orgnr; never transfer to a different legal entity |
| Same name, different entity | 2HD ARCHITECTURE WORKSHOP AS 932973855 vs **2HD ARCHITECTURE WORKSHOP NUF** 997447727 (both linked on the same page); JSON-LD there has the UK sister's phone | only an exact orgnr disambiguates; drop JSON-LD whose name/legalName doesn't match |
| Web agency credit | "Design og utvikling: ACOS", "built by Rima Manage", "Utviklet av Hjemmesidehuset", "Bilder levert av Pexels" | `CREDIT_RX` context; never take socials or orgnrs from credit blocks |
| Data processors in privacy policy | Polarfakta lists Telemagic Group AS, Easyfact AS, Din CRM AS, SpareBank 1 Regnskapshuset | page-type `privacy`: other orgnrs are informational only |
| Sponsor links | Finnsnes IL → facebook.com/exploenergi | social handle must match the company name or domain |
| Site-builder socials | nmg.no (Wix) → linkedin.com/company/wix-com, facebook.com/wix | platform blacklist |
| Typo-squat or expired | unipet.se → unibet.se (gambling); sakarina.com → spam | name absent; CJK |

Rules that follow from this:

* An org number on the site is **necessary** for strong (L1) attribution.
* Even with the target orgnr present, other orgnrs force block-level attribution.
* Registry `hjemmeside` can be shared by several org numbers (club/allianse, borettslag/boligbyggelag, chain shops). If the registry snapshot shows the same domain declared by more than one orgnr, L2 is **not** allowed. Build a `domain → [orgnr]` index once from the bulk file `enheter/lastned` (1 request), or from the evaluator snapshot.

---

## 6. Contact facts

Measured on the 131 reachable sites, all fetched pages.

### E-mail

* Present on 90/131 as text and 79/131 as `mailto:`.
* Brreg `epostadresse` appears literally on the site for 54/122.
* The Brreg e-mail domain equals the site's registrable domain for 86/122.

Problems:

* **Placeholder e-mails:** 8 sites had bad or placeholder mailtos, e.g. `info@example.com` in an Elementor theme on bitfixhelse.no, or `mailto:polarfakta.no`. Filter with `PLACEHOLDER_EMAIL_RX` (example.*, domain.*, yourdomain, dittdomene, wixpress, sentry, `name@`…).
* **Obfuscation:**
  * Cloudflare `data-cfemail` on 4 sites. Decode: key = first byte, `chr(byte ^ key)`.
  * `[at]`/`(at)` on 3 sites.
  * **CSS-reversed** e-mail on elektrikerservice.no: `<a style="unicode-bidi:bidi-override;direction:rtl">on.ecivresrekirtkele@tsop</a>`. Reverse the text of any element with `direction:rtl`.
* **Personal e-mails:** prefer role addresses (post@, info@, kontakt@, firmapost@, salg@, faktura@). The spec says to skip personal staff e-mails.
* `dtf@faktura.poweroffice.net` and `faktura@…` are invoice-scanning addresses. Label them `invoice_email`, not contact.

### Phone

* `tel:` links on 46/131. A keyworded phone in text ("Tlf", "Telefon", "Mob", "Ring", "Sentralbord") on 65/131.
* Brreg `telefon`/`mobil` found on the site for 55/114.
* Formats seen: `22 33 44 55`, `915 00 000`, `91500000`, `+47 915 00 000`, `(+47) 22 33 44 55`, `(+47)48 84 38 69`, `0047…`, `9003-6660`, `004793813450` (Brreg itself).
* Regex used:

  ```python
  PHONE_RX = r"(?<![\w+])(?P<cc>(?:\+|00)[  ]?47[  .\-]?|\(\+47\)[  ]?)?(?P<num>[2-9]\d(?:[  .\-]?\d{2}){3}|[2-9]\d{2}[  .\-]?\d{2}[  .\-]?\d{3}|[2-9]\d{3}[  .\-]?\d{4})(?!\d|[,.]\d)"
  ```

  Then require exactly 8 digits. Reject when preceded by `org`, `kr`, `NOK` or followed by `,-`. Normalise with `phonenumbers` (already in deps) to E.164.
* Accept text phones only when keyworded, inside the orgnr block, or equal to a Brreg number. Sports clubs and pharmacies list many unrelated numbers (e.g. 116111/116117).

### Address

* The registry is authoritative.
* Use the site address only as corroboration: postcode plus the first street word within 120 chars. Ignore `Postboks` unless the box number matches.
* The quick postcode regex `(\d{4})\s+([A-ZÆØÅ]…)` also catches years ("2026 Ekom AS"). Validate against the Posten/Bring postnummer register (free file) if you need it.

---

## 7. Structured data and meta

On 131 reachable sites:

* Any JSON-LD: 49. On the homepage: 46.
* Org-like JSON-LD (Organization, LocalBusiness, LegalService, Restaurant…): 31. Microdata: 24.
* Types: WebSite 38, Organization 24, BreadcrumbList 23, WebPage 22, ImageObject 14, LocalBusiness 9, Article 6.
* Fields in the first org object: name 27, url 27, @id 17, logo 17, address 14, sameAs 14, telephone 7, email 6, legalName 5, description 5, contactPoint 4, taxID 1, identifier 1, foundingDate 1, openingHours 1.
* Yoast/RankMath `@graph` dominates: Organization with name, logo and sameAs.

Caveats:

* **JSON-LD often describes someone else:**
  * the parking host (Webhuset)
  * the group (Fabritius Gruppen with orgnr; Espira)
  * the shared office (Eikesdal)
  * a UK sister (2hD UK with a +44 phone)
  * a Madrid address (Anteroz)
  * an empty LocalBusiness with `address:""` (La-Sa Marine)
* Use JSON-LD only on L1/L2 sites, only when `name` or `legalName` matches the target's name tokens (or `taxID` = target), and only non-empty values.
* Parse defensively. Strip `<!--`, `CDATA` and trailing commas; walk `@graph` and lists. `@type` may be a list (`["Person","Organization"]`).

Meta on 80 verified (L1/L2) homepages:

| Field | Present |
|---|---|
| title | 80 |
| meta description | 49 |
| og:title | 50 |
| og:description | 42 |
| og:site_name | 39 |
| "©" copyright line | 43 |

`html lang`: nb 30, no 26, en 20, nn/da/ru 1 each. Some titles are useless: "Hjem", "Forside", "Framside", "Home", "New Page 1", "My WordPress Site". Prefer og:site_name, then JSON-LD name, then title minus " - Hjem".

Generators seen: WordPress/Elementor/wp-content about 33, unknown 42, One.com 6, Wix 6, Squarespace 6, HubSpot 4, Next.js 4, Sanity 3, Webflow 2, Framer 2, Webnode 2, Weebly 2, Drupal 2, Joomla 2, Umbraco 1, TYPO3 1, Adobe GoLive 4 (!) 1. Wix and Squarespace render server-side, so plain HTTP is fine.

---

## 8. Social links

Pages from reachable sites, after URL regexes:

| Platform | Sites |
|---|---|
| Facebook | 63 |
| Instagram | 37 |
| LinkedIn company/school | 21 |
| YouTube | 17 |
| X/Twitter | 10 |
| TikTok | 4 |
| LinkedIn person | 3 |

Regexes are in `SOCIAL_RX`. Exclude:

* `sharer`, `share`, `dialog`, `plugins`, `tr`, `l.php`, `ads`, `business`, `watch`, `events/`, `groups/`
* Facebook `p/<slug>`, `pg/<slug>` and `pages/<…>` are page paths; keep the next segment.
* X: `intent`, `share`, `home`, `search`, `en`/`no` language paths (`x.com/en` was seen).

Problems seen:

* site-builder socials (Wix: linkedin wix-com, facebook wix, twitter wix, youtube Wix)
* theme demo links (`facebook.com/useformat`)
* sponsor links (exploenergi)
* the parking host's socials
* umbrella-organisation socials (turistforeningen, fagforbundet)
* personal profiles (`facebook.com/hanne.fevik`, `linkedin.com/in/...`)

Rule (`social_matches_company()`):

1. Drop the platform blacklist (`PLATFORM_SOCIAL_RX`).
2. Accept if the handle contains a name token (≥4 chars, æøå-transliterated) or the domain label, or vice versa.
3. Opaque handles (`profile.php?id=…`, numeric LinkedIn IDs, YouTube `channel/UC…`) are accepted only on L1 sites, only when exactly one per platform appears, and only outside credit or sponsor blocks. JSON-LD `sameAs` on a name-matched Organization also qualifies.

On 161 sites this accepted 121 candidate links and rejected 34. Most rejections were correct: platform, sponsor, umbrella, personal. A few correct opaque IDs were also rejected and are recoverable by rule 3.

Never fetch the platform pages: LinkedIn and Meta are excluded by Builderr policy and by robots.

---

## 9. Domain guessing for entities without `hjemmeside`

Candidates (`web_domain_guess.py`), at most 6 per entity, in this order:

1. `epostadresse` domain, if not freemail or an ISP.
2. `<joined>.no`
3. `<hyphen>.no`
4. IDN `.no` (punycode, only if æøå)
5. `<joined>.com`
6. `<oe/aa variant>.no`
7. `<hyphen>.com`

Slugging:

* NFKC, lowercase.
* Cut at `v/`, `avd.`, `c/o`.
* Strip orgform tokens (`AS|ASA|ENK|NUF|SA|DA|ANS|BA|KS|IKS|KF|STI|STIFTELSE`).
* `&` → `og`.
* æ→ae, ø→o, å→a; variant: ø→oe, å→aa.
* IDN via `"…".encode("idna")`.

Resolve DNS first (`getaddrinfo`, host or `www.`). Fetch HTTP only for names that resolve.

Freemail/ISP list (extend it):

* gmail.com, hotmail.com/.no, online.no, yahoo.com/.no, outlook.com/.no, live.no/.com, icloud.com, me.com, msn.com
* frisurf.no, broadpark.no, start.no, c2i.net, getmail.no, lyse.net, altibox.no, telia.no, bluezone.no, mail.com, epost.no
* **ntebb.no, portalen.no, knett.no**, tele2.no, nextgentel.com, ebnett.no, haugnett.no, enivest.net, tussa.com

### Results

| Set | n | Strict hits (target orgnr on home + ≤2 subpages) | + name & registry corroboration | HTTP req/entity | DNS lookups/entity |
|---|---|---|---|---|---|
| A (AS with employees mostly) | 30 | 4 (kanelsnurren.no, gourmetprodukter.no, metik.no→jobbsmart.no, kongsbergbilskade.no) | +2 (autokjemi.no via e-mail domain + legal name; noctua.no via registry phone) | 1.6 | 3.8 |
| B random (no own e-mail) | 60 | 1 (egersundisentrum.no) | +2 (asfaltsor.no: legal name + postcode/street; dbbil.no: phone + address) | 1.9 (B overall) | 4.4 (B overall) |
| B own-domain e-mail | 25 | 2 (lillandsport.no for BIKESHOP AS, trenddesign.net) | +2 (eldreakademiet.no, finansco.no) | (incl. above) | |
| **Total** | 115 | **7 (6 %)** | **13 (11 %)** | | |

Corroboration on name-matched guesses cost about 1.4 extra requests each (44 for 31 candidates).

### False positives that a name-only rule would publish

All were rejected by the rule "orgnr, or name + registry phone/e-mail/address":

* coreinternational.com and core-international.com ("Core International GmbH")
* oxygenhair.com (US salon)
* bolix.com (domain broker)
* allright.com (English school for kids)
* deltaengineering.com / delta-engineering.com
* helium-3.com
* camillalunden.com ("Camilla Lundén Art")
* dj4you.no ("ARTIST4YOU")
* knarren.no
* airfrance.no is correct for AIR FRANCE SA (a foreign entity's branch) but unprovable.

### E-mail domain is not proof

Of 25 own-domain e-mails, 16 point to someone else's site:

* employer: hummel.no, atv-stiftelsen.no, causa.no
* cleaning firm: iclean.no
* accountant or management tool: alletall.no, styrerommet.no (OBOS)
* ISP: nte.no via ntebb.no, portalen.no
* board member's company: bergevvs.no, dragebygg.no, lysglimt.net
* national organisation: dys.no → dysleksinorge.no
* parent municipality: bergen.kommune.no
* same-owner shop: seng.no
* typo-squat: unipet.se → unibet.se

**The OLLEBUKTA 2 AS trap:** seng.no shares the phone and the address with the registry record. This is because the company is named after the address of the shop's building. Never accept property-SPV-style names (`^WORDS \d+ (AS|BRL|…)$`) without the orgnr.

### Recommendation

* Run guessing for all non-`hjemmeside` entities only if budget allows. It costs about 2 HTTP requests per entity, plus DNS.
* Otherwise prioritise:
  * own-domain e-mail (11.4 % of no-site entities have one)
  * AS/ASA/SA with registered employees
  * distinctive names (not "Holding/Eiendom/Invest + common word")
* Publish only L1 or L2g (see §10). Show other candidates as "unverified candidate" in the explanation, without facts.

---

## 10. Verification policy (levels) and confidence

### L1 — org number on site (confidence about 0.97)

* The target orgnr is found on a page of the site: visible text, raw HTML (href, JSON-LD taxID/identifier, brreg link) or the orgnr block.
* The page is HTTP 200 and not parked, bot-walled or hijacked.
* **L1-multi** (other valid orgnrs in keyword context on the same page): only facts inside an *isolated* orgnr block, plus name-matched socials. Never take JSON-LD unless taxID = target.
* Evidence: `claim_span` = orgnr block text (≤300 chars), page URL, `retrieved_at`, `content_sha256`.

### L2 — registry-linked (confidence about 0.80–0.90)

All of these must hold:

1. The site comes from the registry `hjemmeside`.
2. No foreign orgnr in keyword context on any fetched page.
3. Not parked or dead, and visible text ≥200 chars.
4. A *prominent* name match: every distinctive name token (minus orgform and generic words such as holding, eiendom, bygg, regnskap, gruppen, invest, consulting, borettslag, forening…) appears in title, og:site_name, JSON-LD name, logo alt, H1, copyright or footer.
5. If the site redirected to another registrable domain, the match must be in title, og:site_name or JSON-LD name (this kills andebuapotek→gokstadapotek).
6. ≥1 registry corroboration: Brreg phone on the site, Brreg e-mail on the site, Brreg e-mail domain = site domain, registry postcode + street on the site, or the full legal name ("Bilagent AS") on the site.
7. Not an address-named SPV.
8. The registry address is not `c/o <company>` whose name is the site's brand. Do not apply this to `c/o <person>`: capturo.no was a false reject.
9. The `hjemmeside` domain is not declared by more than one orgnr in the registry.

Publishable facts at L2:

* website URL
* title/description (labelled company-reported)
* e-mails on the site's own domain or equal to Brreg
* phones equal to Brreg (others only if keyworded and on the contact page)
* socials that match the name or domain

Confidence 0.8 with one corroboration (e.g. only an e-mail-domain match: pr-o.no, aksa, enterprise.no), and 0.9 with two or more.

### L2g — guessed domain (confidence about 0.80)

* Name candidate or e-mail domain, with no orgnr found.
* The full legal name or a prominent distinctive name, **and** ≥1 of: registry phone, exact registry e-mail, or postcode + street (not postboks).
* No foreign orgnr, not an address-named SPV.
* An e-mail-domain match alone is not enough.

### L0 / ambiguous — do not publish facts

* Store the candidate URL and reason in the explanation, e.g. "registered website www.vbbl.no belongs to Vansjø Boligbyggelag (manager), not RAVINEN BORETTSLAG".
* For dead, parked or blocked registry sites, publish `website.status` with the date. That is a fact about the registry value, not about a different company.

### Simulation (policy v2, 161 entities with `hjemmeside`; `work/policy_v2_results.json`)

| Level | n |
|---|---|
| L1 | 30 |
| L1-multi | 3 |
| L2 | 44 |
| L0 | 84 |

L0 reasons:

| Reason | n |
|---|---|
| unreachable / robots | 21 |
| foreign orgnr | 14 |
| name not prominent | 13 |
| dead / parked / hijacked (incl. 2 HTTP 404/500, 2 JS shells) | 16 |
| blocked | 8 |
| no content (JS / image-only) | 6 |
| redirect without strong name | 4 |
| no corroboration | 1 |
| c/o | 1 |

Per orgform:

| Orgform | L1 | L2 | L0 |
|---|---|---|---|
| AS (n=79) | 13 | 33 | 33 |
| FLI (n=26) | 5 | 2 | 19 |
| ENK (n=21) | 3 | 3 | 15 |
| STI | 4/4 | | |
| KOMM | 3/3 | | |
| BRL | | | 3/3 |

Fact families published for the 77 L1+L2 companies:

| Fact family | Companies |
|---|---|
| website | 77 |
| e-mail | 62 |
| description | 49 |
| Facebook | 36 |
| phone | 35 |
| Instagram | 24 |
| LinkedIn | 11 |
| YouTube | 5 |
| X | 3 |
| TikTok | 2 |

A manual review of the L2 list found no clear wrong-company case after the v2 rules. The borderline one is ENTERPRISE SOLUTIONS AS → enterprise.no (Enterprise Rent-A-Car Norway, probably the licensee). It has a single e-mail-domain corroboration and is the only case to consider downgrading.

---

## 11. Extraction rules and request budget

### Per company with a registry `hjemmeside`

* robots.txt (1, cached per host).
* Homepage (1 + redirect hops). Follow at most one meta-refresh, `window.location` or frameset hop.
* If the target orgnr is not on the homepage, or no contact facts were found:
  * fetch the **contact** page
  * then **about**
  * then **terms** (kjøps-/salgs-/leievilkår; webshops must show their orgnr there)
  * then **privacy** (personvernerklæring names the "behandlingsansvarlig" with orgnr)
* **Stop** as soon as the target is found and the contact page (if any) was fetched.
* Max 5 requests. Measured average 2.94 (p100 5).

### Link heuristics

Pick links on the same registrable domain whose path or anchor text matches:

| Kind | Regex |
|---|---|
| contact | `(?<![a-z])kontakt\|contact\|finn[- ]oss\|find[- ]us` (word boundary needed: "stikkontakt" matched) |
| about | `om[-_ ]?oss\|about\|hvem[- ]er[- ]vi\|om[- ]firma\|om[- ]selskapet\|selskapet\|bedriften` |
| terms | `vilk[aå]r\|betingelser\|kj[oø]psvilk\|salgsvilk\|terms\|conditions` |
| privacy | `personvern\|privacy\|gdpr\|cookie` |

* Score: shorter paths first; no query strings; skip pdf/images; skip `mailto:`, `tel:`, `#`.
* Most common paths: `/kontakt` (28), `/kontakt-oss` (17), `/contact` (11), `/om-oss` (26), `/about` (8), `/omoss`, `/about-us`, `/personvern` (12), `/personvernerklaering` (6), `/privacy-policy` (6).
* 33/131 homepages had no such link: single-page sites, JS menus or image links.
* Don't guess `/kontakt` on SPA sites; they return the same shell for every path.

### Daily test of 100 (about 92 AS)

* About 9–15 have `hjemmeside`, at about 3 requests each, so ≈ 30–45 requests.
* Guessing for about 85–90 without one costs ≈ 1.6–1.9 HTTP each (plus about 4 DNS lookups), so ≈ 150–170 requests. It yields about 5–10 more verified sites.
* Website total ≈ 200 of the 2,000-request cap.
* Wall time is negligible with per-host serialisation (≥0.5 s gap; honour Crawl-delay ≤10 s) and 8–16 hosts in parallel.

### Freshness (refresh is scored)

* Store per page: URL, final URL, status, ETag/Last-Modified, `content_sha256`, `retrieved_at`.
* On refresh, use a conditional GET; a 304 still counts as a request.
* Re-crawl when the registry `hjemmeside` or `epostadresse` changes (oppdateringer feed).
* Website status transitions (live→parked/dead/foreign-orgnr) are material changes.
* On fetch failure or bot wall, keep the last supported value with a `last_verified_at` date. Don't delete it.

---

## 12. Sources considered and rejected

* **Norid RDAP** (`https://rdap.norid.no/domain/<d>`): returns registrar (with orgnr) and tech contact only. No holder identity. Notice: "Any commercial use of information from the service … is prohibited". Port-43 whois is unreachable from this host. Excluded (also in Builderr's exclusion list).
* **Wikidata SPARQL**: 10,338 items with P2333 (orgnr), 7,344 of them with P856 (website). For our 276 sampled entities: 13 hits, all already with `hjemmeside`; 0/115 for those without. One query per batch is cheap and gives a second confirmation plus sometimes better URLs (politiet sub-page, regjeringen NFD page). It does not help discovery for random AS.
* **proff.no, purehelp.no, forvalt.no, 1881.no, gulesider.no**: terms or robots prohibit this access. We only *recognise* links to them on company sites (`BRREG_LINK_RX`) and never fetch them.
* **Search engines** (Google, Bing, DDG HTML): not used. Builderr's policy needs official or licensed API access, and the user chose no paid APIs.

---

## 13. Test fixtures

18 trimmed HTML files are in `research/samples/company_websites__<case>__<orgnr>.html`, totalling 596 KB, the largest 64 KB. Trimming removes scripts (except JSON-LD and tiny redirect scripts), styles, svg, mega-menus (>40 `<li>`), `data-*`, `x-*` and `aria-*` attributes. It keeps footer/contact markup, JSON-LD, `data-cfemail` and `style="direction:rtl"`. Orgnr signals are verified identical before and after trimming (`trim_preserves_orgnr_signals: true`).

`company_websites__index.json` holds, per file:

* `orgnr`, `legal_name`, `url`, `page_kind`, `notes`
* `expected` (machine output of `extract_page` + `orgnr_block`: target found in text/html, formats, other orgnrs, brreg links, mailto/emails/rtl/cf emails, tel links, socials, JSON-LD org summary, placeholder signals, block emails/phones)
* `expected_verdict` (hand-written: level, publishable facts, must_not_publish)

| Case file | What it tests |
|---|---|
| `L1_footer_orgnr_simple__912342344` | `Organisasjonsnummer 912 342 344` in footer with address and e-mail |
| `L1_plain_orgnr_MVA_nospace__928913406` | `Orgnr:928913406MVA` |
| `L1_contact_page_NO_MVA_format__917387079` | `Org.nr.: NO 917 387 079 MVA` only on the contact page |
| `L1_multi_company_footer__920209319` | 3 firms in one footer; block isolation |
| `L1_privacy_with_processor_orgnrs__985946698` | target + 4 processor orgnrs; non-isolated block |
| `L0_parked_host_page_with_host_orgnr__982182107` | Webhuset parking page |
| `L0_sister_company_orgnr_and_jsonld_taxid__982322553` | sister company footer + JSON-LD taxID |
| `L1_orgnr_only_in_brreg_link_href__932973855` | orgnr only in a virksomhet.brreg.no href; AS vs NUF twin |
| `L1_plain_orgnr_rtl_reversed_email__975971554` | CSS rtl-reversed e-mail |
| `L1_FLI_footer_ehf_and_socials__975576205` | `(org.nr.975 576 205)` in EHF text; 4 company socials |
| `redirect_js_window_location__929237617` | `window.location` redirect page |
| `L0_hijacked_expired_domain_cjk__976939395` | expired-domain spam |
| `L0_parked_for_sale_hugedomains__984421664` | for-sale broker page |
| `L0_cloudflare_bot_wall__882912272` | "Just a moment..." challenge |
| `L1_sports_club_with_sponsor_social__979448708` | sponsor Facebook link to reject |
| `L2_wix_platform_socials__879887682` | Wix's own socials to reject |
| `L1_ENK_vat_no_jsonld__976913019` | `MVA/VAT no. 976913019` + JSON-LD Organization |
| `guess_false_positive_similar_name_gmbh__992137452` | guessed domain of a German namesake |

---

## 14. Gotchas for implementers

* `selectolax>=1.0` removed the Modest backend. `from selectolax.parser import HTMLParser` raises ImportError; use `from selectolax.lexbor import LexborHTMLParser`. `node.text(strip=True)` concatenates without spaces ("Kontaktoss"); use `text(separator=" ", strip=True)`.
* `uv run` inside the project dir uses the project `.venv` (py3.12). Plain `python3` is 3.14 with stdlib only.
* Count every redirect hop (`len(response.history)`) and robots.txt as outbound requests.
* `httpx` default decoding guesses utf-8. Decode yourself (header, then meta, then utf-8, then cp1252).
* Don't treat a 403/429 bot wall as a dead site. Don't retry it in the same run.
* Don't scan the whole page for weak "coming soon"-type phrases.
* For `kristiansand.kommune.no`, a naive "last two labels" gives `kommune.no` as the registrable domain. Use the Public Suffix List (e.g. `tldextract` with its bundled snapshot and network fetch disabled). Norway has many second-level suffixes: kommune.no, priv.no, museum.no, stat.no, dep.no, mil.no, idrett.no, vgs.no, fhs.no, folkebibl.no, plus geographic names.
* Registry `hjemmeside` can be stale (ENK→AS successor), a Facebook URL, a sub-path on a manager's site (`norian.no/eiendomsforvaltning`), or shared by several org numbers.
* Name-token matching on *all* text is misleading. The chain site gokstadapotek.no mentions "Andebu" and "apotek"; seng.no contains "Ollebukta" in its address. Match in prominent fields only, and drop tokens that are just the street name.
* ENK names mix a business name with the owner's name ("KOOLART John Martin Sanvik"). Require the business tokens; person tokens are optional.
* NUF websites are often the foreign parent's domain (powercare.dk, logstrup.com, hydrogroup.no). The NUF is the same legal person, so the site is acceptable. Mark the country and don't map foreign phone numbers to Norwegian ones.

---

## 15. Scripts (`research/scripts/`)

| Script | Purpose |
|---|---|
| `web_sample_entities.py`, `web_sample_large.py`, `web_prevalence.py` | sampling and prevalence |
| `web_fetch_study.py` | async fetcher: robots, https→http fallback, client-side redirects, subpage picker, per-host gate |
| `web_extract.py` | **prototype extractor**: orgnr / phone / e-mail / social / JSON-LD / parked / bot-wall regexes, `orgnr_block()`, `brreg_links()`, `social_matches_company()` |
| `web_analyze.py`, `web_stats.py` | per-page analysis and aggregate statistics |
| `web_domain_guess.py`, `web_guess_corroborate.py` | domain guessing and corroboration |
| `web_corroborate.py` | Brreg-record ↔ site corroboration signals |
| `web_policy.py` | policy v2 simulation with early stopping and request accounting |
| `web_make_samples.py` | fixture trimming and index |
