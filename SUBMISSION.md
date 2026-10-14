# Signalpost submission: step by step

Deadlines: register the project link on Unstop by **17 Oct 2026, 23:59 IST**. Builderr accepts version 1 plus
up to **4 revised exact commit hashes until 18 Oct** (5 versions in total). Final ranking is the mean over the
daily batches while a frozen version is active, so **submit version 1 early** and use the revisions.

Public board as of 5 Oct 2026: 37 submissions, 28 assessed, **0 qualified** (65/100 is required). The leader
had 60.80 (recall 13.82/50, evidence 26.98/30, synthesis 12/12, UX 8/8). Recall decides the ranking.

## 1. Push the repository (you)

```bash
cd ~/Documents/nordic-company-agent
git log --oneline -1                       # the commit you will submit
# create an empty public repository on GitHub (e.g. nordic-company-agent), then:
git remote add origin https://github.com/<you>/nordic-company-agent.git
git push -u origin HEAD:main
git rev-parse HEAD                         # paste this exact hash into the e-mail
```
The repository is about 160 MB (declared caches and the two deliverable runs). The largest file is under GitHub's
100 MB limit. Commits are authored by this machine's git identity (`git config user.name`). Change it before pushing if
you want another name on them.

## 2. E-mail version 1 to submit@builderr.ai (you)

Subject: `Signalpost submission v1 – nordic-company-agent`

```
Agent name: nordic-company-agent
Repository: https://github.com/<you>/nordic-company-agent
Exact commit: <git rev-parse HEAD>

Run command (one command, pinned environment via uv):
  ./run.sh <organisations.jsonl> <output_dir>
  (wraps: uv run nca run --input <file> --out <dir>; optional --bulk <frozen enheter CSV> uses the
   frozen registry snapshot as identity anchor with zero requests; default caps --max-requests 3000
   --time-budget 40)
Reference-harness form:
  python scripts/run_competition_batch.py --organisations batch.jsonl --bulk brreg-enheter.csv \
    --output out/envelopes.jsonl --report out/run-report.json --run-id <id> --expected-count <n>

100-company smoke test: smoke/ in the repository (envelopes.jsonl, run-report.json, changes.jsonl,
  validation.json, site/index.html = the interactive viewer, opens from disk)
Interactive app: ./ui.sh (http://127.0.0.1:8765): search, compare, evidence drawer, live research

Models / APIs: no LLM. Keyless official and open sources only:
  Brønnøysundregistrene (Enhetsregisteret, Regnskapsregisteret, update feeds, signature/prokura,
  Støtteregisteret; NLOD 2.0), GLEIF (CC0), Wikidata (CC0), NAV Arbeidsplassen job feed (public token),
  Forskningsrådet SkatteFUNN / project data (NLOD), Mattilsynet smilefjes (CC BY 4.0), and
  company-owned websites (robots.txt honoured). Declared caches in cache/ (see CACHES.md).
Expected cost per official run: $0.00 third-party cost. 1,000 companies use the 2,000-request cap
  in about 8 minutes; 100 companies about 780 requests in about 4 minutes.
Secrets: none.
Contact for results: <your e-mail>
```

## 3. Ask Builderr what is scored (you; optional but valuable)

To `submit@builderr.ai` (or Builderr's Discord):

```
Hi! Two questions for Signalpost (agent: nordic-company-agent):
1. Which external field families count for the 50 recall points? Besides website, social profiles,
   jobs and news, do state-aid grants (Støtteregisteret), R&D projects (SkatteFUNN / Research Council),
   food-safety inspections, LEI and dated registry events count as families?
2. Declared caches: our agent ships pre-built discovery leads (domains where the exact org number was
   printed when crawled) and re-verifies every lead live before publishing. Is that acceptable, and
   could you share per-family recall for our version 1 so we can see where we stand?
Thank you!
```

## 4. Unstop

Register the project link on Unstop (the repository URL) before **17 Oct, 23:59 IST**.

## Revisions

Each improvement is a new commit. Send its exact hash to submit@builderr.ai as "version N" (at most 4
revisions, last one by 18 Oct). Regenerate the deliverables first:

```bash
uv run pytest -q
tools/make_deliverables.sh refresh        # re-runs smoke/ and profiles/ against the previous run
git add -A && git commit -m "Signalpost vN" && git push && git rev-parse HEAD
```

Clean-machine check (what Builderr does): `git clone <repo> /tmp/check && cd /tmp/check && ./run.sh batches/smoke-100.jsonl /tmp/check-out`.
