# Senate Financial Disclosures — Pipeline Design

A scheduled, resumable pipeline that collects U.S. Senate financial disclosure
filings, OCRs the scanned ones, and publishes a queryable dataset. Orchestrated
by GitHub Actions, with raw source files in Cloudflare R2 and derived data
committed to this repo.

**Status:** design. The go/no-go IP-reputation probe has **passed** from a
GitHub-hosted runner (a runner IP can clear the site's Akamai edge), so the
GitHub-orchestrated approach is viable.

---

## 1. Goals and non-goals

### Goals
- Collect Annual and Periodic Transaction reports for current + former senators
  from `efdsearch.senate.gov`, from 2012 to present.
- Persist raw source files (scanned GIFs/PDFs, electronic HTML) durably and cheaply.
- Turn scanned filings into text via a vision LLM.
- Publish derived, queryable artifacts (CSV + SQLite + DuckDB) that anyone can
  `git clone` and use, plus per-filing structured data.
- Run unattended on a monthly or quarterly schedule; be fully resumable.
- Keep the whole thing cheap enough to run as a personal/public project.

### Non-goals
- Not a real-time system. Quarterly freshness is fine.
- Not a full historical backfill in a single run (the backfill is a one-time,
  possibly multi-run bootstrap; steady state is incremental).
- No commercial exploitation of the data (the source's terms prohibit it).
- Not attempting to normalize/analyze the financial data beyond transcription
  and basic structuring — downstream analysis is a separate project.

---

## 2. Architecture overview

Six logical components, mapped to concrete services:

| Concern            | Implementation                                   |
|--------------------|--------------------------------------------------|
| Scheduler          | GitHub Actions `schedule:` + `workflow_dispatch` |
| Phase 1 compute    | GitHub-hosted `ubuntu-latest` runner             |
| Raw file storage   | Cloudflare R2 bucket (S3-compatible, no egress)  |
| Repo + artifacts   | This repository (code + compiled datasets)       |
| Phase 2 OCR        | GitHub Models (vision model), CPU runner         |
| Secrets            | GitHub Actions encrypted secrets                 |

Data flow:

```
                 ┌─────────────────────────────────────────────┐
                 │            GitHub Actions (cron)             │
                 │                                              │
  efdsearch ───▶ │  Phase 1: collect ──▶ raw files ──▶ R2      │
   .senate.gov   │        │                                     │
                 │        └──▶ manifest (source of truth)       │
                 │                    │                         │
                 │  Phase 2: OCR ◀────┘  (scanned subset only)  │
                 │        │                                     │
                 │        └──▶ per-filing JSON/CSV              │
                 │                    │                         │
                 │  Phase 3: compile ─┴──▶ full-history         │
                 │                        CSV / SQLite / DuckDB │
                 │                    ┌─────────┴─────────┐     │
                 │   small text CSVs  │                   │  big binaries
                 │   (commit to repo) │                   │  (Release / R2)
                 └────────┬───────────┘         ┌─────────┴───────────┐
                          │                     │                     │
                  repo working tree      GitHub Release assets    (or R2 keys)
                     (git push)          `latest` (replaceable)
```

The **manifest** is the join key between R2 (raw files) and the repo (derived
data). Every manifest row points to its R2 object key and tracks collection +
OCR status.

**Publishing split (important):** derived artifacts are handled two different
ways depending on how they behave in git. Small text files (the manifest,
per-filing CSVs) are committed to the repo. Large, binary, regenerated-wholesale
files (the compiled `.duckdb` / `.sqlite` and the full-history `.csv`) are
published as **GitHub Release assets** (or R2 objects), never committed to git.
See §6 for the reasoning.

---

## 3. Repository layout

```
/
├── DESIGN-SENATE.md           ← this file
├── README.md                  ← what the dataset is, how to use it
├── .github/
│   └── workflows/
│       ├── probe.yml          ← standalone edge probe (already built)
│       └── pipeline.yml       ← the main scheduled pipeline
├── src/
│   ├── config.py              ← all tunables (scope, delays, model IDs)
│   ├── session.py             ← curl_cffi session + eFD handshake
│   ├── collect.py             ← Phase 1: listing + download
│   ├── storage.py             ← R2 adapter (put/get/exists via S3 API)
│   ├── manifest.py            ← read/write/checkpoint the manifest
│   ├── ocr.py                 ← Phase 2: GitHub Models vision OCR
│   └── compile.py             ← Phase 3: build CSV/SQLite/DuckDB
├── data/                      ← COMMITTED derived artifacts (text only)
│   ├── manifest.csv           ← index of every filing + status
│   └── filings/               ← per-filing structured CSVs
└── tests/
    └── ...

  Compiled datasets are NOT in the repo. Each run publishes them as
  Release assets on the `latest` release (or to R2):
      senate_disclosures.duckdb
      senate_disclosures.sqlite
      senate_disclosures_full.csv
```

**What lives where, and why:**
- **Raw scans/HTML → R2, never git.** Binaries bloat git history permanently.
- **Small text derived data (manifest, per-filing CSVs) → repo.** Diffs well,
  browsable on GitHub, cheap to clone. This is the product people read.
- **Large compiled datasets (`.duckdb`, `.sqlite`, full-history `.csv`) →
  GitHub Release assets (or R2), never committed.** They're big, binary, and
  regenerated wholesale each run — the worst possible shape for git history.
  See §6 for the full reasoning and the LFS trade-off.

---

## 4. Phase 1 — Collect

Ported from the existing Colab notebook, which already works. Key pieces:

- **Session + handshake** (`session.py`): `curl_cffi` session with
  `impersonate="chrome"` clears the Akamai edge; GET `/search/home/` for the
  csrftoken, POST to accept the prohibition-agreement gate. This is proven.
- **Listing** (`collect.py`): paginated POST to `/search/report/data/`, page
  size 100, polite delay (~2s + jitter) between pages. Filter to Annual +
  Periodic Transaction reports for senators (current + former), drop candidates
  and extension notices.
- **Download** (`collect.py`): electronic filings save as HTML; paper filings
  are an HTML viewer embedding per-page GIFs on `efd-media-public.senate.gov`
  — download each page image in order. Rare direct PDFs handled too.
- **Storage** (`storage.py`): instead of Google Drive, write each raw file to
  R2 under a stable key, e.g. `raw/<year>/<filer>_<date>_<reportid>/<page>.gif`.
- **Manifest** (`manifest.py`): one row per filing with filer, date, report
  type, format (electronic/scanned), R2 keys, and status columns. Checkpoint
  after every filing so a mid-run disconnect costs at most one item.

**Incremental behavior:** on each run, list only filings newer than the last
successful run (track a high-water mark — the max filing date seen — in the
manifest or a small state file). The backfill is the exception, not the norm.

### R2 specifics
- R2 is S3-API-compatible: use `boto3` with a custom `endpoint_url` pointing at
  the R2 account endpoint. No code changes beyond the endpoint + credentials.
- R2 has **no egress fees**, which matters because this is a public dataset and
  people will download the raw corpus.
- Credentials: an R2 API token (access key ID + secret). Stored as GitHub secrets.

---

## 5. Phase 2 — OCR

Replaces the notebook's self-hosted Gemma-on-L4 with a hosted vision model, so
this phase runs on a **plain CPU runner** — no GPU needed. This removes the
single biggest obstacle to running OCR inside GitHub Actions.

- **Backend:** GitHub Models, using a vision-capable model. Authenticates with
  the auto-provisioned `GITHUB_TOKEN`, so there is **no third-party secret to
  manage** for the free/prototyping quota.
- **Input:** the scanned subset from the manifest (`format == scanned` AND
  `ocr_status != done`). Rasterize/resize page images as needed, send each page
  image + the transcription prompt.
- **Prompt:** transcribe verbatim; preserve tables as ` | `-separated rows;
  capture every transaction line (date, owner, asset, ticker, type, amount
  range) exactly; mark illegible regions. (Reuse the notebook's prompt.)
- **Output:** per-filing JSON with `pages[]` and a joined `full_text`, plus a
  flattened per-filing CSV of transaction rows where parseable.
- **Resume:** skip filings already OCR'd; checkpoint the manifest per filing.

### Cost / rate notes (verify against live pricing at build time)
- GitHub Models free quota is aimed at prototyping — fine for a **quarterly
  incremental** run (a few hundred new scanned pages), throttled for a big
  backfill.
- **Backfill escape hatch:** if the historical bootstrap is too big for the
  free quota, run it once against the **Gemini Batch API** (Gemma-tier models
  are the cheapest Google offers; Batch is 50% off and async with a ~24h SLA).
  Keep the OCR backend behind a small interface so swapping is a config change.
- Rough sizing: ~3k token-units per page all-in; a big multi-page annual filing
  is well under a cent at Gemma-tier rates. Volume, not price, is the constraint.

---

## 6. Phase 3 — Compile

After collection + OCR, build the published artifacts from the manifest +
per-filing data. The compile step is idempotent — a pure function of the
collected/OCR'd inputs, rebuilt from scratch each run.

Two output channels, chosen by how each artifact behaves in git:

**Committed to the repo (text, diffs well, small):**
- **`manifest.csv`** — always-current index of every filing and its status.
- **Per-filing CSVs** under `data/filings/`.
- Final step: `git commit` + `git push` using the run's token.

**Published as Release assets, NOT committed (large, binary, regenerated whole):**
- Compiled full-history **`.csv`**, **`.sqlite`**, and **`.duckdb`**.
- Attached to a single rolling release (tag `latest`), replacing the prior
  assets each run, e.g. via `gh release upload latest <file> --clobber`.
- People download "the current dataset" from the release page / a stable URL.

### Why the split — the git-history trap
Git keeps every version of every file forever. For **text** (the CSVs) it stores
efficient diffs, so appending rows is cheap. For **binary blobs** (SQLite,
DuckDB) a tiny logical change rewrites bytes throughout the file, so git can't
diff it — every run stores a *fresh, near-complete copy* in history. A 90 MB
DuckDB regenerated quarterly becomes >1 GB of history in three years, even
though the working copy is always ~90 MB and each version looks identical to the
last. Every `git clone` then pays for all of it. This bloat happens *below* the
100 MB per-file hard limit, so it bites silently.

### Limits to keep in mind
- **100 MB per file** — hard push block (50 MB is a soft warning).
- **Repo size** — GitHub recommends <1 GB, emails you around 5 GB.
- **Release assets** — up to **2 GB each**, live outside git history, don't
  count against repo size. This is why they fit regenerated big binaries.

### Why not Git LFS
LFS swaps large files for pointers and stores bytes on a separate server, which
solves "can't commit a >100 MB file" and "clone is huge" — but it **still
versions every copy**, so quarterly regeneration burns LFS storage AND bandwidth
quota (free tier ~1 GB each; bandwidth is the painful meter for a public repo
everyone clones). LFS is the right tool only when the large file *must* live in
the git working tree and be versioned through git. Our compiled datasets need
neither: they're replaceable "latest" snapshots, which is exactly what Releases
(or R2) are for. So this project **does not use LFS**.

### R2 alternative
Since R2 is already in the stack, the compiled datasets could equally be
published to a stable R2 key (`datasets/senate_disclosures.duckdb`), overwritten
each run — no egress fees, clean public URL. Trade-off vs. Releases: you lose
GitHub's built-in version/changelog UI (recover it by date-stamping keys if
wanted). Either channel works; pick one and keep it consistent.

---

## 7. Orchestration (GitHub Actions)

Single workflow, `pipeline.yml`, triggered by `schedule:` (quarterly) and
`workflow_dispatch:` (manual). Structure as chained jobs so failure is isolated
and restartable:

```
job: collect   → runs Phase 1, writes raw to R2, updates manifest, uploads
                 manifest as a job artifact
job: ocr       → needs: collect; runs Phase 2 on the scanned subset
job: compile   → needs: ocr; builds datasets, commits text CSVs back to repo,
                 and publishes compiled .csv/.sqlite/.duckdb as `latest`
                 Release assets (or to R2)
```

The `compile` job needs write permission for both channels: `contents: write`
in the workflow (the auto-provisioned token can then commit *and* manage
releases), plus the R2 creds it already holds if publishing datasets there
instead. Set it explicitly:

```yaml
  compile:
    needs: ocr
    permissions:
      contents: write     # commit CSVs + create/update the `latest` release
```

Because each job runs on a fresh VM, pass the manifest between jobs via an
artifact (or re-read it from the committed copy / R2). Nothing persists on the
runner between jobs.

### Constraints to design around
- **6-hour per-job cap.** Incremental runs fit easily. For the backfill, shard
  by year or date range across multiple runs; resume-by-skip makes this safe.
- **Scheduled workflows are best-effort** and get **auto-disabled after 60 days
  of repo inactivity.** A quarterly cadence risks this — mitigate with the
  weekly `probe.yml` (which doubles as a keep-alive) or an occasional manual run.
  This makes `probe.yml` load-bearing for the schedule: do not remove or move it
  without another keep-alive in place.
- **Everything not pushed is lost** when the VM dies — so R2 upload + git commit
  are mandatory final steps, not nice-to-haves.

---

## 8. Secrets and configuration

Stored as GitHub Actions encrypted secrets (Settings → Secrets and variables →
Actions), referenced as `${{ secrets.NAME }}`, injected as env vars:

| Secret                     | Used by   | Notes                                  |
|----------------------------|-----------|----------------------------------------|
| `R2_ACCESS_KEY_ID`         | Phase 1/3 | R2 API token                           |
| `R2_SECRET_ACCESS_KEY`     | Phase 1/3 | R2 API token                           |
| `R2_ACCOUNT_ENDPOINT`      | Phase 1/3 | R2 S3 endpoint URL (can be a variable) |
| `R2_BUCKET`                | Phase 1/3 | bucket name (can be a variable)        |
| `GITHUB_TOKEN`             | Phase 2   | auto-provisioned; no setup needed      |
| `GEMINI_API_KEY`           | Phase 2*  | only if using the Batch backfill path  |

`*` optional — only needed if the OCR backend is switched to Gemini for backfill.

- Secrets are encrypted at rest, masked in logs, and withheld from fork PRs
  (important: this is a **public** repo).
- Prefer OIDC over long-lived keys where a provider supports it; R2 currently
  uses static API tokens, so those go in secrets.

---

## 9. Open questions / decisions to make during implementation

1. **Backfill strategy.** One big sharded bootstrap vs. letting incremental runs
   slowly catch up. Affects whether the Batch OCR path is needed at all.
2. **Manifest format at scale.** CSV is simple but a growing manifest may be
   better as SQLite (also lets Phase 3 query it directly). Decide early.
3. **Transaction parsing depth.** How much structure to extract from OCR text
   (full transaction rows vs. raw text only). Start with raw text + best-effort
   rows; tighten later.
4. **R2 key scheme.** Lock the object-key convention before the first real run;
   changing it later means re-keying the whole corpus.
5. **Idempotency of re-OCR.** Deleting a filing's outputs + clearing its status
   should cleanly force re-download / re-OCR. Verify this works end to end.
6. **Politeness under Actions.** Confirm the delay/backoff still reads as polite
   from a runner and that the edge stays clear across a full run (the probe only
   tested the handshake + one page).
7. **OCR image-token budget.** The House pilot (see `prior_art/paper/ETL_PRIOR_ART.md`)
   found every local vision-model failure was perception starvation at the
   default ~130-token page read; accuracy only held at ~1120 image tokens per
   page. GitHub Models may not expose that knob. Verify a hosted model reads
   scanned pages at adequate resolution before committing to it; otherwise the
   Gemini Batch escape hatch (or a self-hosted Gemma run) becomes the main path.
8. **Data terms.** Derived artifacts must ship with `DATA-TERMS.md` (statutory
   use restriction). Release assets should include it alongside the datasets.

---

## 10. Build order (suggested for Claude Code)

1. `config.py` + `session.py` + the probe (session/handshake — already proven).
2. `storage.py` (R2 adapter) with a tiny put/get/exists round-trip test.
3. `manifest.py` (schema, read/write, checkpoint).
4. `collect.py` (listing + download → R2 + manifest); run a tiny date-bounded
   slice end to end.
5. `ocr.py` (GitHub Models vision, behind a swappable interface); OCR one filing.
6. `compile.py` (build CSV/SQLite/DuckDB from per-filing data).
7. `pipeline.yml` wiring the three jobs; test via `workflow_dispatch` on a
   narrow date range before enabling the schedule.
8. Backfill: shard by year, run manually, watch the 6h cap and OCR quota.
```
