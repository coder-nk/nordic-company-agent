# Nordic Company Agent - Submission Information

## 📦 1,000 Company Profile Repository

**Repository URL:** https://github.com/coder-nk/nordic-company-agent

**Live Demo:** https://coder-nk.github.io/nordic-company-agent/

---

## 🔖 Exact Commit Hash

```
a4234974724bd94f063221763640e1bbd658c317
```

**Commit Message:** "Add comprehensive deployment configuration"

**Branch:** `master`

**Verification:**
```bash
git clone https://github.com/coder-nk/nordic-company-agent.git
cd nordic-company-agent
git checkout a4234974724bd94f063221763640e1bbd658c317
```

---

## ⚡ One Command to Run It

### Option 1: Run 1,000 Company Batch (Recommended)

```bash
./run.sh batches/profiles-1000.jsonl out/profiles
```

This will:
- Process 1,000 Norwegian companies
- Generate `out/profiles/envelopes.jsonl` with complete profiles
- Create static UI at `out/profiles/site/index.html`
- Produce run report with metrics

**Input:** `batches/profiles-1000.jsonl` (37KB, 1,000 organisation numbers)

**Expected Output:**
- `out/profiles/envelopes.jsonl` - 1,000 complete company profiles
- `out/profiles/run-report.json` - Execution metrics
- `out/profiles/changes.jsonl` - Changes from previous run
- `out/profiles/snapshots/` - Archived source responses
- `out/profiles/site/index.html` - Interactive web UI

### Option 2: Quick Demo (100 Companies)

```bash
./run.sh batches/smoke-100.jsonl out/smoke
```

### Option 3: Interactive UI (Pre-computed Results)

```bash
./ui.sh
# Opens http://localhost:8765 with 1,100 companies (profiles + smoke)
```

### Option 4: Docker

```bash
docker-compose up -d
# UI: http://localhost:8765
# API: http://localhost:8080
```

---

## 🤖 Model/API Details

### **No Language Model Used**

**LLM:** `null`  
**Rationale:** Deterministic, reproducible results from public data sources

### **Zero Paid APIs**

**Paid APIs:** `[]` (empty array)  
**Third-Party Cost:** **$0.00 USD**

### **Data Sources (All Free & Open)**

| Source | Type | Access | License |
|--------|------|--------|---------|
| **Brønnøysundregistrene Enhetsregisteret** | Official Registry | Keyless REST API | NLOD 2.0 |
| **Regnskapsregisteret** | Official Accounts | Keyless REST API | NLOD 2.0 |
| **Enhetsregisteret Bulk Files** | Official Registry | Public Download | NLOD 2.0 |
| **NAV Arbeidsplassen** | Job Postings | Public Token | NAV API Terms |
| **GLEIF LEI Registry** | Legal Entity IDs | Keyless REST API | CC0 1.0 |
| **Wikidata** | Knowledge Base | SPARQL Endpoint | CC0 1.0 |
| **Støtteregisteret** | State Aid Grants | Public API/Bulk | NLOD 2.0 |
| **Forskningsrådet** | R&D Grants | Public Files | NLOD 2.0 |
| **Mattilsynet** | Food Inspections | Public CSV | CC BY 4.0 |
| **Company Websites** | Official Sites | Public HTTP | Quoted as Evidence |

### **Technical Stack**

- **Language:** Python 3.12+
- **HTTP Client:** httpx (async)
- **HTML Parsing:** selectolax
- **Data Validation:** pydantic
- **Testing:** pytest (428 tests passing)
- **Deployment:** Docker, Docker Compose, GitHub Pages

### **Agent Characteristics**

```json
{
  "name": "nordic-company-agent",
  "version": "1.0.0",
  "llm": null,
  "paid_apis": [],
  "approach": "deterministic",
  "evidence": "full_provenance",
  "sources": "open_public_data_only"
}
```

---

## 💰 Expected Run Costs

### **Actual 1,000 Company Run Results**

From commit `a4234974724bd94f063221763640e1bbd658c317`:

| Metric | Value |
|--------|-------|
| **Third-Party Cost** | **$0.00 USD** |
| **Companies Processed** | 1,000 |
| **Total HTTP Requests** | 2,000 (hit budget cap) |
| **Wall Clock Time** | 8.1 minutes (486 seconds) |
| **Request Budget Used** | 100% (2,000 / 2,000) |
| **Time Budget Used** | 20% (486s / 2,400s) |
| **Successful Profiles** | 1,000 (100%) |
| **Identity Matches** | 997 exact, 3 deleted |

### **Request Distribution**

| Source | Requests | Purpose |
|--------|----------|---------|
| data.brreg.no | 1,389 | Registry, accounts, feeds |
| Bulk Downloads | 2 | Roles & subunits (full dataset) |
| Company Websites | 590 | Website verification |
| api.gleif.org | 17 | LEI lookups (batched) |
| query.wikidata.org | 1 | P2333 queries (batched) |
| Other Sources | 1 | Jobs feed pages |

### **Per-Company Metrics**

- **Median Requests:** 1 request/company
- **95th Percentile:** 6 requests/company
- **Median Runtime:** 1.9 seconds/company
- **95th Percentile:** 8.5 seconds/company

### **Coverage Achieved**

| Data Family | Company Coverage |
|-------------|------------------|
| Legal Identity | 100% |
| Annual Accounts | 99.7% |
| Leadership (Roles) | 99.7% |
| Workplaces | 99.7% |
| Public Activity | 99.6% |
| Hiring | 99.7% |
| Corporate Structure | 6.9% |
| Website | 6.0% (60 verified, 77 ambiguous) |
| Social Profiles | 3.1% |

**Total Claims Generated:** 101,533 claims across 1,000 companies

### **Cost Breakdown by Request Type**

```
Identity Lookups:       4 requests    ($0.00)
Batch External APIs:    25 requests   ($0.00)
Annual Accounts:        997 requests  ($0.00)
Website Verification:   590 requests  ($0.00)
Group Structure:        66 requests   ($0.00)
Signature/Prokura:      316 requests  ($0.00)
Bulk File Downloads:    2 requests    ($0.00)
─────────────────────────────────────────────
TOTAL:                  2,000 req     $0.00
```

### **Budget Strategy Used**

When the 2,000 request budget couldn't cover per-company API calls:

1. **Switched to Bulk Mode** (automatic)
   - Downloaded complete roles dataset (130MB, 351s)
   - Downloaded complete subunits dataset (89MB, 204s)
   - Cost: 2 requests for 1,000 companies

2. **Skipped Optional Modules**
   - account_copies: 997 companies (filing PDFs)
   - authorisations: 811 companies (incomplete coverage)

3. **Result:** 100% completion with full core data

### **Scaling Estimates**

| Batch Size | Expected Requests | Expected Time | Cost |
|------------|------------------|---------------|------|
| 100 companies | 778 | 3.7 min | $0.00 |
| 1,000 companies | 2,000 | 8.1 min | $0.00 |
| 10,000 companies | 5,000* | ~60 min | $0.00 |
| 100,000 companies | 15,000* | ~8 hours | $0.00 |

*Bulk mode automatically activates, batched APIs scale logarithmically

### **Hardware Requirements**

**Minimum:**
- CPU: 2 cores
- RAM: 2GB
- Disk: 5GB (caches + outputs)
- Network: Residential broadband

**Actual Run (1,000 companies):**
- CPU: 16 cores (24 concurrent connections)
- RAM: ~1GB peak usage
- Disk: 25MB envelopes + 96KB snapshots
- Network: ~220MB downloaded

### **Cost Comparison**

| Approach | Per Company | 1,000 Companies |
|----------|-------------|-----------------|
| **This Agent** | **$0.00** | **$0.00** |
| LLM-based (GPT-4) | ~$0.10 | ~$100 |
| Commercial APIs | ~$0.05 | ~$50 |
| Manual Research | ~$5.00 | ~$5,000 |

---

## 📊 Output Validation

All 1,000 profiles passed validation:

```json
{
  "passed": true,
  "checks": {
    "exact_expected_count": true,
    "same_order_as_input": true,
    "unique_organisation_numbers": true,
    "all_terminal_status": true,
    "every_available_claim_has_evidence": true,
    "no_dangling_evidence_ids": true,
    "evidence_has_source_and_time": true,
    "missing_values_never_encoded": true,
    "financial_claims_have_period": true,
    "no_invalid_availability": true
  }
}
```

---

## 🔍 Reproducibility

### Exact Reproduction

```bash
# Clone at exact commit
git clone https://github.com/coder-nk/nordic-company-agent.git
cd nordic-company-agent
git checkout a4234974724bd94f063221763640e1bbd658c317

# Install dependencies
uv sync --frozen

# Run exact same batch
uv run nca run \
  --input batches/profiles-1000.jsonl \
  --out out/profiles-reproduction \
  --max-requests 2000 \
  --time-budget 40

# Compare outputs
diff out/profiles/envelopes.jsonl out/profiles-reproduction/envelopes.jsonl
```

**Expected:** Byte-identical envelopes (modulo timestamps) if sources unchanged

### Determinism Guarantee

Same source bytes → same claims (provably deterministic):
- No randomness or sampling
- No LLM inference
- Stable claim IDs (content-addressed)
- Sorted output arrays

---

## 📁 Key Files

```
nordic-company-agent/
├── batches/
│   └── profiles-1000.jsonl          # 1,000 org numbers (37KB)
├── profiles/
│   ├── envelopes.jsonl.gz           # 1,000 profiles (24MB compressed)
│   └── run-report.json              # Execution metrics (16KB)
├── run.sh                           # One-command launcher
├── README.md                        # Documentation
├── DEPLOYMENT.md                    # Deployment guide
└── docker-compose.yml               # Container orchestration
```

---

## ✅ Verification Checklist

- [x] Repository URL accessible
- [x] Commit hash verified (a423497...)
- [x] One command works (`./run.sh batches/profiles-1000.jsonl out/profiles`)
- [x] No LLM used (llm: null)
- [x] No paid APIs (paid_apis: [])
- [x] Cost: $0.00 (third_party_cost_usd: 0.0)
- [x] 1,000 companies processed successfully
- [x] All validation checks passed
- [x] Full evidence provenance included
- [x] Live demo deployed
- [x] Docker configuration ready
- [x] 428 tests passing

---

## 🎯 Quick Links

- **Repository:** https://github.com/coder-nk/nordic-company-agent
- **Live Demo:** https://coder-nk.github.io/nordic-company-agent/
- **Commit:** https://github.com/coder-nk/nordic-company-agent/commit/a4234974724bd94f063221763640e1bbd658c317
- **Run Report:** `profiles/run-report.json`
- **Envelopes:** `profiles/envelopes.jsonl.gz` (24MB)

---

**Built for:** Builderr Signalpost Challenge  
**Agent Version:** 1.0.0  
**Python Version:** 3.12+  
**License:** MIT  
**Date:** October 14, 2026
