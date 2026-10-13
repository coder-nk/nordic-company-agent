# Limitations, source rights and privacy

## Source rights
* **Used:** Brønnøysundregistrene open data (NLOD 2.0, attribution in every envelope's `licences`),
  Regnskapsregisteret open API (NLOD 2.0), company-owned websites (robots.txt honoured, one polite fetch per
  page, quoted only as evidence), NAV job-ad feed (public token, NAV API terms: inactive ads are removed),
  GLEIF (CC0), Wikidata (CC0), Brreg Støtteregisteret (NLOD 2.0), Forskningsrådet SkatteFUNN lists and open
  project data (NLOD 2.0), Mattilsynet smilefjes inspections (CC BY 4.0, attributed in every envelope that uses them).
* **Not used** (their terms forbid systematic collection, or there is no permitted access): proff.no / forvalt.no,
  purehelp.no, 1881.no, gulesider.no, regnskapstall.no, LinkedIn, Meta/Instagram, Glassdoor, Indeed, Google or Bing
  results, Norid whois/RDAP, w2.brreg.no/kunngjoring (robots.txt disallows it). Social profile URLs are
  published only when the verified company website links to them (or Wikidata's P2333 item does and the handle
  matches the company); the platforms themselves are never fetched.
* **Not available without credentials or payment:** beneficial owners (Maskinporten), shareholder register,
  review/rating platforms (paid Google Places), Doffin/Patentstyret (keys tied to an account). These families
  are reported as `not_available`/`blocked` rather than guessed.

## Privacy
* Role holders: name, role, role group and last-changed date for the **input entity's current roles only**.
  Birth dates (`fodselsdato`) are removed before anything is stored; snapshots of role payloads are stored
  redacted, and their SHA-256 refers to the original bytes. No person-centric views or cross-company person
  lookups (enhetsregisterloven § 9-1). Sole proprietors are not enriched as people.
* Website e-mail addresses: only generic role mailboxes (post@, info@, ...) or the address the company filed
  in the registry. Personal-looking addresses are skipped.
* HTTP 410 from Enhetsregisteret (removed for legal reasons): nothing is published.

## Safe URL handling
Company-controlled URLs go through an SSRF guard: http(s) only, no credentials in URLs, and every DNS answer
must be a global unicast address on every redirect hop. Redirects are capped (≤6), bodies are capped
(2–3 MB), no cookies are sent or kept, there are timeouts, and robots.txt is honoured (5xx/timeouts =
disallow for the run). No secrets are needed. If any are added in future, they come from documented
environment variables only.

## Known gaps
* The open accounts API serves the latest 3 filings. Banks, insurers and pension funds are not served
  (reported as `not_available` with the reason). PDF filing copies are rate-limited and used only as extras.
* Websites that print no org number and fail registry corroboration are reported as `ambiguous`. This is
  deliberate: abstaining beats a wrong-company publication.
* JavaScript-only sites are fetched as static HTML. No headless browser is used.
* NAV is the only job source. Ads on finn.no or on company career pages that are not posted to NAV are missed,
  except for the careers-page URL itself.
