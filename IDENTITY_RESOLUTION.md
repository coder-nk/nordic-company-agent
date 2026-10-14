# Identity resolution and publication gates

The organisation number is the only identity anchor. A fact is published only when the source is keyed by,
or proves, that exact number. Abstaining (`ambiguous` / `not_available`) is always preferred to a
wrong-company publication.

| source | gate |
|---|---|
| Enhetsregisteret (live API, batch API, bulk files) | record `organisasjonsnummer` must echo the input. Filed website, e-mail and phone values that other organisations file too (property managers, accountants, groups) are labelled `relation: related` with a note; they are never presented as the entity's own channels. The same applies to e-mails and websites on a *domain* that other organisation numbers use (chain mailboxes such as ngbutikk.net, an accountant's or group's domain; freemail/ISP domains excluded), and to filed websites and e-mails on a domain the website gate refused as a group, parent, sister, chain, foreign, acquirer or platform site. A domain verified as the entity's own exclusive website keeps its filed contacts as `self`. Deleted entities: only name, form and deletion date. HTTP 410 (removed for legal reasons): nothing. Sub-unit numbers: labelled `relation: subunit`, parent org number given. |
| Roles, sub-units, group structure, signature/prokura | keyed by the org number in the URL; payload echo checked. Sub-units must have `overordnetEnhet == input`. Role holders that are entities are `relation: related`; parents are `relation: parent/ultimate_parent`. |
| Regnskapsregisteret | `virksomhet.organisasjonsnummer` must equal the input. Foreign-currency NUF filings (possibly the foreign head office) are `ambiguous`. A balance sheet off by more than 10% is withheld as `ambiguous`. |
| GLEIF | digits of `entity.registeredAs` == org number and legal address country NO (RA000472). |
| Wikidata | P2333 (Norwegian org number) == org number. Several items for one number → only the item ids, with a note. Ended statements are dropped. Social handles are kept only if the handle matches the legal name with no unexplained residue (drops e.g. `telenorsverige`). No financials from Wikidata. |
| NAV jobs | employer org number from the ad entry equals the company or one of its registered sub-units. Name matching only selects which entries to fetch. |
| Company websites | see below |

## Website gate (`web/verify.py`)

Leads come from the declared discovery caches (`site_discovery`: legal-name / e-mail domain guesses;
`site_discovery_cc`: hosts from the Common Crawl web graph and archive that print the labelled org number),
the registry-filed `hjemmeside`, Wikidata P856, and live
candidates (own-domain e-mail, legal-name slugs; DNS check, at most 2 fetched). A lead is never evidence.
The site is re-fetched live (robots.txt honoured, SSRF-safe) and classified. The gate was hardened over
four rounds of independent adversarial audits (EVAL.md), with precision always ahead of recall.

* **L1, confidence 0.97:** the exact org number is printed in visible text on a fetched page, next to an
  org-number label ("Org.nr", "Organisasjonsnummer", "NO … MVA", a privacy-controller or seller statement,
  a footer imprint), or it is the JSON-LD identifier/vatID of an Organization with the company's name. Not
  proof:
  * numbers that appear only in HTML source (comments, attributes, placeholders, scripts, hidden elements)
    or only in a document link;
  * numbers inside a list of several companies;
  * numbers printed under a different legal name.
* **L2, confidence 0.85:** only for the registry-filed website, and only when all of these hold:
  * an exact registry e-mail or phone is on the site;
  * the full legal name, with its upper-case form token, is in visible footer, imprint or contact text;
  * the privacy, terms or imprint page names no other controller, seller or owner;
  * there are no foreign or group signals;
  * the corroborating contacts are not shared with other organisation numbers.
* **L2r, confidence 0.75:** the registry-filed website when the L2 contact / legal-name / legal-page
  requirements are not all met, but every negative check passes and, in addition, the registrable domain
  itself carries the company's distinctive name (or the whole name), the title / site name carries it too,
  the site does not leave its domain, nothing names another owner or c/o company, and the home page is
  readable (not a JavaScript shell). The company filed the address itself; Builderr's own reference crawler
  treats registry-linked websites as the company website. Only registry-equal contact facts are published.
* **Never published as the company's official website**, so the claim is `ambiguous` with the reason:
  * parent, group, sister or foreign-HQ sites: other legal entities, foreign registrations, ownership
    phrases, a parent © owner, or the same brand on foreign country domains;
  * sections of another brand's site: hotel or retail chains, foreign parents' `/no/` sections, sub-paths of
    shared hosts;
  * property-manager, community and directory platforms (e.g. Vibbo);
  * hosts filed by several organisation numbers without a visible labelled proof;
  * parked, for-sale, hijacked or bot-wall pages.
* A published URL is never wider than the filed or proved URL. After a cross-domain redirect the locale path
  is kept.

Facts from a verified site:
* title, description (agency credits and chain boilerplate removed), brand and logo;
* generic role mailboxes on the site's own domain (never personal-looking addresses, authority or invoice
  mailboxes);
* phones from tel: links whose visible text matches, or labelled numbers inside the company's own block;
  never postboxes, foreign-prefixed numbers, numbers shared with other organisation numbers in the
  registry, or named persons' mobiles;
* company-linked social profiles whose handle matches the company's distinctive name (platform, sponsor,
  foreign-entity and generic handles excluded);
* careers page, RSS/Atom news and news-section items.

Every fact's evidence span contains the value; facts that cannot be anchored are dropped.
