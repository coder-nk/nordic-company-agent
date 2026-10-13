# Research and abstention policy

1. **Anchor on the organisation number** (MOD11-validated before any request). Every module is keyed by it,
   or must prove it.
2. **Official records first** (Enhetsregisteret, Regnskapsregisteret, roles, sub-units, group structure, update
   feeds), then the **verified company website** and what it links to, then open registries keyed by the
   number (GLEIF, Wikidata P2333, NAV job ads by employer number, Støtteregisteret).
3. **Copy, never compute or guess.** Values are copied exactly from source bytes, and money is parsed as
   Decimal and never rescaled. Derived values (county from municipality number, equity ratio, revenue
   growth) are marked `derived` and cite their inputs.
4. **Abstain when unsure:** `ambiguous` for a candidate that can't be tied to the entity; `not_available`
   when the source was checked and has nothing; `blocked` when the source refused; `not_applicable`
   when the question does not apply; `failed` when the check could not run. Absence is never 0.
5. **Label relations** (parent, ultimate parent, sub-unit, related entity) instead of collapsing them.
6. **No LLM.** Summaries are deterministic templates over published claims, with claim and evidence ids
   per sentence. Unknowns are listed explicitly.
7. **Respect sources:** robots.txt, rate limits, licences and terms. No scraping of platforms whose terms
   prohibit it. Personal data is limited to current roles of the input entity, and birth dates are removed.
