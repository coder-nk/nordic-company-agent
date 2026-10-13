# Identity resolution and publication gates

The organisation number is the only identity anchor. A fact is published only when the source is keyed by,
or proves, that exact number. Abstaining (`ambiguous` / `not_available`) is always preferred to a
wrong-company publication.

| source | gate |
|---|---|
| Enhetsregisteret (live API, batch API, bulk files) | record `organisasjonsnummer` must echo the input. Deleted entities: only name, form and deletion date. HTTP 410 (removed for legal reasons): nothing. Sub-unit numbers: labelled `relation: subunit`, parent org number given. |
| Roles, sub-units, group structure, signature/prokura | keyed by the org number in the URL; payload echo checked. Sub-units must have `overordnetEnhet == input`. Role holders that are entities are `relation: related`; parents are `relation: parent/ultimate_parent`. |
| Regnskapsregisteret | `virksomhet.organisasjonsnummer` must equal the input. Foreign-currency NUF filings (possibly the foreign head office) are `ambiguous`. A balance sheet off by more than 10% is withheld as `ambiguous`. |
| GLEIF | digits of `entity.registeredAs` == org number and legal address country NO (RA000472). |
| Wikidata | P2333 (Norwegian org number) == org number. Several items for one number → only the item ids, with a note. Ended statements are dropped. Social handles are kept only if the handle matches the legal name with no unexplained residue (drops e.g. `telenorsverige`). No financials from Wikidata. |
| NAV jobs | employer org number from the ad entry equals the company or one of its registered sub-units. Name matching only selects which entries to fetch. |
| Company websites | see below |

## Website gate (`web/verify.py`)

Leads come from the declared discovery cache, the registry-filed `hjemmeside`, Wikidata P856, and live
candidates (own-domain e-mail, legal-name slugs; DNS check, at most 2 fetched). A lead is never evidence.
The site is re-fetched live (robots.txt honoured, SSRF-safe) and classified:

* **L1, confidence 0.97:** the exact org number is printed on a fetched page of the site (text, HTML, or a
  link to its registry page). The page is live and not parked, for sale, a bot wall or hijacked. If the site
  also prints other org numbers (group, agency, processor), contacts come only from the smallest DOM block
  containing the target number and no other, and descriptive facts need the company's name in the title,
  og:site_name or JSON-LD.
* **L2, confidence 0.85:** the registry-filed website, with no other company's org number on it, every
  distinctive name token prominent, at least one independent registry corroboration (phone, e-mail, e-mail
  domain, postcode + street, full legal name), not an address-named property SPV, not a c/o manager's site,
  not a host shared by several org numbers (`cache/registry_website_domains.json.gz`), and no cross-domain
  redirect to a parent or brand site.
* Otherwise `official_website` is `ambiguous` (a site was seen but not proven) or `not_available` /
  `blocked` / `failed`, with the reason in `note`. Nothing from such a site is published.

Facts from a verified site: official URL, verification proof (URL + verbatim span), title, description,
brand, logo, language, address, generic role mailboxes or the registry e-mail (never personal-looking
addresses), phones (tel: links or labelled, isolated when shared), company-linked social profiles
(platform, sponsor and personal links excluded), careers page, and dated RSS/Atom news on the same domain.
