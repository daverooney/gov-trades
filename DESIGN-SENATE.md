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

Seven logical components, mapped to concrete services:

| Concern            | Implementation                                   |
|--------------------|--------------------------------------------------|
| Scheduler          | GitHub Actions `schedule:` + `workflow_dispatch` |
| Phase 1 compute    | GitHub-hosted `ubuntu-latest` runner             |
| Raw file storage   | Cloudflare R2 bucket (S3-compatible, no egress)  |
| Repo + artifacts   | This repository (code + compiled datasets)       |
| Phase 2 extraction | Gemma 4 via Gemini API free tier (see `DESIGN-OCR.md`) |
| Overflow compute   | Colab Pro runtime via Colab CLI, self-hosted Gemma |
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
├── DESIGN-HOUSE.md            ← House delta from this design
├── DESIGN-OCR.md              ← shared extraction tier
├── README.md                  ← what the dataset is, how to use it
├── .github/
│   └── workflows/
│       ├── probe.yml          ← standalone edge probe (already built)
│       └── pipeline.yml       ← the main scheduled pipeline
├── DATA-TERMS.md              ← statutory use restriction on the data
├── CHANGELOG-SCHEMA.md        ← field promotions, schema versions
├── schemas/
│   └── extraction-v1.json     ← response_schema sent to the model
├── prompts/
│   └── <backend>/<class>-v1.md ← prompt per backend and doc class
├── src/
│   ├── config.py              ← all tunables (scope, delays, model IDs)
│   ├── session.py             ← curl_cffi session + eFD handshake
│   ├── collect.py             ← Phase 1: listing + download
│   ├── storage.py             ← R2 adapter (put/get/exists via S3 API)
│   ├── filings.py             ← read/write/checkpoint data/filings.csv
│   ├── extract/               ← Phase 2: backends per DESIGN-OCR.md
│   ├── validate.py            ← row/page checks; sets validation_status
│   ├── ingest.py              ← normalise raw_response → transactions rows
│   └── build_release.py       ← Phase 3: JSONL → SQLite/DuckDB/Parquet
├── data/                      ← COMMITTED text, the source of truth
│   ├── filings.csv            ← one row per filing (the manifest)
│   ├── extractions/{chamber}/{year}/{filing_id}.jsonl
│   └── transactions/{chamber}/{year}/{filing_id}.jsonl
└── tests/
    └── golden/                ← ~200 hand-verified pages (DESIGN-OCR §6)

  Compiled datasets are NOT in the repo. Each release publishes them as
  Release assets (or to R2):
      senate_disclosures.duckdb
      senate_disclosures.sqlite
      *.parquet (one per table)
      RELEASE_NOTES.md (row counts, coverage, model/prompt versions, pointer to DATA-TERMS.md)
```

**What lives where, and why:**
- **Raw scans/HTML → R2, never git.** Binaries bloat git history permanently.
- **Small text derived data (`filings.csv`, per-filing JSONL) → repo.** Diffs
  well, browsable on GitHub, cheap to clone. This is the product people read.
  Per-filing files, deterministic ordering, fixed key order (DESIGN-OCR §5).
- **Large compiled datasets (`.duckdb`, `.sqlite`, full-history `.csv`) →
  GitHub Release assets (or R2), never committed.** They're big, binary, and
  regenerated wholesale each run — the worst possible shape for git history.
  See §6 for the full reasoning and the LFS trade-off.

---

## 4. Phase 1 — Collect

Ported from the existing Colab notebook, which already works and is now
committed at `prior_art/colab/senate_disclosures_pipeline_v14.ipynb` (see
`prior_art/README.md` for the cell map and the one bug not to port: the
report-id regex must match uppercase UUIDs). Its 2026-07-19 run sized the
corpus at 4,250 senator reports since 2012, 24% paper-filed. The raw corpus
from that run stays on Google Drive and is **not** reused: every byte in R2
is collected by this repo's code, so provenance is one story (decision
2026-09-20; the corpus is ~2 GB and re-crawling it is cheap). Key pieces:

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
- **Filings index** (`filings.py`, the manifest): one row per filing per the
  `filings` table in DESIGN-OCR §5, with `raw_key`, `content_sha256`,
  `doc_class`, and `amends_filing_id` for amendments (link, never replace).
  Checkpoint after every filing so a mid-run disconnect costs at most one item.

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

## 5. Phase 2 — Extraction

The extraction tier is shared with the House pipeline and specified in
**`DESIGN-OCR.md`**: Gemma 4 on the Gemini API free tier as the primary
(31B for scanned, 26B A4B for e-filed), the other model as adjudicator,
self-hosted Gemma on Colab via the Colab CLI as overflow, Vertex managed
Gemma as paid fallback. Fixed output schema, per-backend prompts, a
golden set before any threshold is chosen.

Senate-specific points only:

- **Input:** the scanned subset from the manifest (`format == scanned` AND
  `extract_status != done`). Paper filings arrive as per-page GIFs from the
  viewer; electronic filings are HTML and are parsed directly, not sent to a
  model. Rare direct PDFs are rasterised with `pdftoppm`.
- **Document class:** every scanned Senate filing routes as `scanned`, so the
  31B is the Senate's first-pass model. Expect Senate to lean harder on the
  31B pool than the House does.
- **Multi-page annual reports** may exceed the per-request token budget at a
  1120 visual budget. Fall back to per-page requests with page-stitching in
  the result; see DESIGN-OCR §7.
- **Resume:** skip filings already extracted; checkpoint the manifest per
  filing. Unchanged.

### History
The original plan (GitHub Models on a CPU runner, GITHUB_TOKEN auth) died when
GitHub retired GitHub Models on 2026-07-30. The Colab notebook's Gemma-on-L4
approach survives as the overflow path, now driven headlessly by the Colab CLI
rather than by hand.

---

## 6. Phase 3 — Compile

After collection + extraction, build the published artifacts from the
committed text under `data/`. The build is idempotent — a pure function of
the JSONL files, rebuilt from scratch each release. Anyone with the repo can
reproduce identical assets locally.

Two output channels, chosen by how each artifact behaves in git:

**Committed to the repo (text, diffs well, small):**
- **`data/filings.csv`** — always-current index of every filing.
- **Per-filing JSONL** under `data/extractions/` (every run, raw response
  inline) and `data/transactions/` (normalised rows).
- Commit messages carry the batch, model, and prompt version.

**Published as Release assets, NOT committed (large, binary, regenerated whole):**
- **`.sqlite`**, **`.duckdb`**, and one **`.parquet`** per table, built by
  loading the JSONL into the three-table schema and applying `is_current`.
- Validation queries run first; the release fails if counts or invariants
  regress against the previous release.
- Attached to a rolling `latest` release (or a `vYYYY.Q` tag), with
  `RELEASE_NOTES.md` pointing at `DATA-TERMS.md`, because people find assets
  via search and never read the README.
- DuckDB can query the Parquet straight from the release URL; say so in the
  README.

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
| `GEMINI_API_KEY`           | Phase 2   | AI Studio key; free tier, Gemma 4      |
| `COLAB_BASE_URL`           | Phase 2*  | overflow only; set per Colab session   |

`*` optional — the Colab overflow path is driven from a laptop, not Actions,
so this is normally a local env var rather than a repo secret.

### Three runtimes, one config path

The library reads configuration from environment variables only (`config.py`)
and never opens a file or a secrets API itself. Each runtime populates the
environment its own way:

| Runtime | Secret store | How it reaches the environment |
|---|---|---|
| Laptop | gitignored `.env` | `python-dotenv`, loaded by the thin scripts only |
| GitHub Actions | encrypted repo secrets | `env:` block in the workflow, `${{ secrets.NAME }}` |
| Colab Pro backfill | Colab secrets pane | `google.colab.userdata.get()` into `os.environ` at the top of the notebook; needs the R2 keys and `GEMINI_API_KEY` |

A committed `.env.example` lists every variable name with no values and is
the canonical reference for what the code expects.

### Storage backends

`storage.py` exposes put/get/exists over two backends selected by
environment: a local-directory backend (the default when the R2 variables
are unset) and R2 via `boto3`. Build and test the collector against the
local backend first; create the bucket and API token when the R2 round-trip
test (§10 step 2) is ready to run, not before.

When the bucket is created: one bucket, since key prefixes already separate
chamber and raw/parsed; one API token scoped to object read + write on that
bucket only; public read (r2.dev subdomain or custom domain) is a later
bucket setting for corpus downloads.

### Hygiene

- Secrets are encrypted at rest, masked in logs, and withheld from fork PRs
  (important: this is a **public** repo).
- Enable GitHub secret scanning with push protection (free for public
  repos) so a pushed key is blocked before it lands.
- Prefer OIDC over long-lived keys where a provider supports it; R2 currently
  uses static API tokens, so those go in secrets.

---

## 9. Open questions / decisions to make during implementation

1. **Backfill strategy.** One big sharded bootstrap vs. letting incremental runs
   slowly catch up. Affects whether the Colab overflow path is needed at all.
2. **Manifest format at scale.** Settled: text is the source of truth
   (`filings.csv` + per-filing JSONL); SQLite is built from it at release time
   and never committed. If `data/extractions/` outgrows the repo it moves to
   per-year Release assets or R2, not LFS.
3. **Transaction parsing depth.** Settled by `DESIGN-OCR.md` §5: direct-to-JSON
   transaction rows are the product; verbatim transcription is an optional
   second prompt, not a prerequisite.
4. **R2 key scheme.** Lock the object-key convention before the first real run;
   changing it later means re-keying the whole corpus.
5. **Idempotency of re-OCR.** Deleting a filing's outputs + clearing its status
   should cleanly force re-download / re-OCR. Verify this works end to end.
6. **Politeness under Actions.** Confirm the delay/backoff still reads as polite
   from a runner and that the edge stays clear across a full run (the probe only
   tested the handshake + one page).
7. **Extraction tier unknowns** are tracked in `DESIGN-OCR.md` §7: separate vs
   shared rate-limit pools, how Gemma 4 image tokens are counted on the API,
   exact limit values, free-tier data-use terms, multi-page requests.
8. **Data terms.** Derived artifacts must ship with `DATA-TERMS.md` (statutory
   use restriction). Release assets should include it alongside the datasets.

---

## 10. Build order (suggested for Claude Code)

1. `config.py` + `session.py` + the probe (session/handshake — already proven).
2. `storage.py` (R2 adapter) with a tiny put/get/exists round-trip test.
3. `filings.py` (schema per DESIGN-OCR §5, read/write, checkpoint).
4. `collect.py` (listing + download → R2 + filings.csv); run a tiny date-bounded
   slice end to end.
5. `extract/gemini.py` behind the `Extractor` protocol; run the golden set
   (DESIGN-OCR §6) before wiring routing.
6. `validate.py`, `ingest.py`, `build_release.py` (JSONL → SQLite/DuckDB/Parquet).
7. `pipeline.yml` wiring the three jobs; test via `workflow_dispatch` on a
   narrow date range before enabling the schedule.
8. Backfill: shard by year, run on the Gemini free tier from a laptop or a
   long-running Actions job, watch 429s. Only if it presses the limits, stand
   up `extract/colab.py` against a Colab CLI runtime as overflow.
```
