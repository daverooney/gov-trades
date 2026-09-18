> **Prior art, absorbed 2026-09-18.** This brief came from a separate deep dive
> on different priors (paid Gemini 3.7 Flash Batch, no discovery done, no
> House prior art, no R2). It is kept verbatim for reference. What was taken
> into the live design docs:
>
> - §4 data model (three tables, every run kept, `is_current`, `_raw`
>   siblings, `extra`, schema changelog) → `DESIGN-OCR.md` §5, verbatim in
>   spirit.
> - §3 native PDF input for e-filed docs → `DESIGN-OCR.md` §3 routing.
> - §3 amendment linking → `filings.amends_filing_id`, `DESIGN-HOUSE.md` §3.
> - §5 per-filing JSONL and diff-friendliness rules → `DESIGN-OCR.md` §5,
>   `DESIGN-SENATE.md` §3.
> - §6 200-page golden set → `DESIGN-OCR.md` §6.
> - §7 release workflow (validate, Parquet, release notes pointing at the data
>   terms) → `DESIGN-SENATE.md` §6.
>
> What was **not** taken, and why:
>
> - Gemini 3.7 Flash as the primary model. Probing showed Gemma 4 on the free
>   tier handles e-filed docs (as PDF, with schema output) at zero cost; Flash
>   is kept for the scanned tier only. See `DESIGN-OCR.md` §2.
> - Nightly batches. Cadence is quarterly incremental plus a sharded backfill.
> - "No non-Google model provider" as a non-goal. Colab-hosted Gemma and a
>   paid fallback are in the design; the backend is swappable by design.
> - `LICENSE-DATA.md`. The repo uses `DATA-TERMS.md` for the same purpose.
> - The open questions in §9 on discovery mechanism and PDF storage are
>   answered by `DESIGN-HOUSE.md` §2 and R2 respectively.

# Project Brief: Congressional Stock Disclosure Extraction

_Last updated: 2026-09-18. Prices and model names below are as of this date and will drift; treat the "Verify" items as required checks, not suggestions._

This document captures the design decisions and research behind this project so that a co-implementer (human or Claude Code) can work from a shared understanding. Read it fully before proposing changes to the pipeline, schema, or repo layout.

---

## 1. Goal

Extract structured transaction data from PDFs of stock-trading disclosures filed by members of the US Congress (Periodic Transaction Reports and related filings), covering a backlog from 2012 to present plus ongoing filings.

Outputs:

- A Git repository of plain-text data (CSV / JSONL) as the durable, diffable source of truth.
- Periodically published (roughly quarterly) SQLite, DuckDB, and Parquet assets as GitHub Releases, built reproducibly from the text files.

Non-goals for now: a live web frontend, real-time ingestion, or any non-Google model provider. These may come later; don't design them out, but don't build them.

---

## 2. Model selection (Gemini)

### Decisions

- **Primary extraction model: `gemini-3.7-flash`** via the Gemini API Batch tier.
- **Thinking level: medium** by default; **high** for pages flagged as handwritten/scanned. Note that 3.x models use a `thinking_level` string enum (`low` / `medium` / `high`), not the older integer `thinking_budget`.
- **`media_resolution`: high** for image inputs so handwriting is not downsampled.
- **Structured outputs with a JSON schema** for every extraction call (see §4 for the shape).
- **Escalation model: `gemini-3.1-pro-preview`** for pages that fail validation. Escalation only, never the default.
- **Candidate for A/B testing, not adopted: `gemini-3.8-flash`** (released 2026-09-02). Same per-token price as 3.7 through 2026-12-31, but by design consumes more tokens per task on multi-step work. May be better on handwritten pages; must be measured on the test set before switching.
- **Candidate for routing typed filings: `gemini-3.5-flash-lite`** (and the cheaper `gemini-3.1-flash-lite`). If Lite matches 3.7 on the clean, electronically-filed era (roughly 2018+), route by era. Also must be measured first.

### Why cost is not the deciding factor

At ~1,000 pages per nightly batch, spend is single-digit dollars per night on any Flash-tier model and likely low hundreds of dollars for the entire backlog. Accuracy on messy scanned forms dominates. **Do not trade accuracy for token savings.**

Batch-tier list prices, USD per 1M tokens, as of 2026-09-18:

| Model | Input | Output | Notes |
|---|---|---|---|
| `gemini-3.7-flash` | $0.375 | $1.875 | Introductory; doubles to $0.75 / $3.75 on 2027-01-01 |
| `gemini-3.8-flash` | $0.375 | $1.875 | Same intro schedule as 3.7 |
| `gemini-3.5-flash-lite` | $0.15 | $1.25 | Image input priced same as text; no announced increase |
| `gemini-3.1-flash-lite` | $0.125 | $0.75 | No announced increase |
| `gemini-3.1-pro-preview` | $1.00 | $6.00 | Prompts ≤200k tokens; escalation only |

Batch is a flat 50% off Standard. A "Flex" tier exists at the same discount for synchronous-but-low-priority calls if batch submission ever becomes awkward.

### Verify before first production run

- [ ] Per-page token counts for a representative sample (typed page, handwritten page, multi-page filing) using the count-tokens endpoint. The cost estimates in this document assumed ~1,500 input / ~1,000 output tokens per page and are **unverified placeholders**.
- [ ] Current model IDs and pricing at https://ai.google.dev/gemini-api/docs/pricing and https://ai.google.dev/gemini-api/docs/models. Google shipped three Flash models in six weeks this summer; expect churn.
- [ ] Whether `gemini-3.5-pro` has shipped. As of this writing it has not (announced May 2026, repeatedly delayed). If it exists, evaluate it as the escalation model instead of 3.1 Pro.
- [ ] Batch API turnaround and any per-job size limits. Google targets completion within 24 hours; nightly 1,000-page jobs are well within intended use.

---

## 3. Input handling

### Prefer native PDF input over rasterized images

Gemini accepts PDFs directly. For electronically-filed (typed) reports this preserves the text layer, which is more reliable than OCR-ing our own render, and it handles multi-page transaction tables without stitching page images together.

Rasterize **only** true scans (no usable text layer), and when rasterizing, do it at a resolution that keeps handwriting legible.

Implementation note: detect text-layer presence per PDF at ingest (e.g., via `pdfplumber` or `pypdf` text extraction yielding non-trivial content) and record the result on the `filings` row so routing decisions are reproducible.

### Known ugliness in the corpus

- Older House filings are frequently handwritten, faxed, skewed, or rotated.
- Amount fields are checkbox ranges (e.g., "$1,001 – $15,000"), not numbers.
- Transaction tables span pages.
- Amendments exist and must be linked to the filing they amend rather than silently overwriting it.
- Form layouts change over the years. Expect to discover new fields mid-project.

---

## 4. Data model

Three tables. Do **not** collapse to one wide table; expose that as a view instead.

```sql
-- One row per source PDF
CREATE TABLE filings (
  filing_id        TEXT PRIMARY KEY,   -- stable id derived from source doc id
  chamber          TEXT NOT NULL,      -- 'house' | 'senate'
  filer_name       TEXT,
  filer_id         TEXT,               -- source-system id if available
  report_type      TEXT,               -- e.g. 'PTR', 'annual', 'amendment'
  amends_filing_id TEXT,               -- FK to filings.filing_id, nullable
  filing_date      TEXT,               -- ISO 8601
  source_url       TEXT,
  source_path      TEXT,               -- path in repo or archive
  page_count       INTEGER,
  has_text_layer   INTEGER,            -- 0/1, drives PDF-vs-image routing
  first_seen_at    TEXT NOT NULL       -- ISO 8601
);

-- One row per (filing, page, model run). Keep every run.
CREATE TABLE extractions (
  extraction_id     TEXT PRIMARY KEY,
  filing_id         TEXT NOT NULL REFERENCES filings(filing_id),
  page_number       INTEGER,           -- NULL when whole PDF was sent
  model_id          TEXT NOT NULL,     -- e.g. 'gemini-3.7-flash'
  thinking_level    TEXT,              -- 'low' | 'medium' | 'high'
  media_resolution  TEXT,
  prompt_version    TEXT NOT NULL,     -- git-tracked prompt/schema version
  batch_id          TEXT,
  run_at            TEXT NOT NULL,     -- ISO 8601
  input_tokens      INTEGER,
  output_tokens     INTEGER,
  raw_response      TEXT NOT NULL,     -- full JSON response, verbatim
  validation_status TEXT,              -- 'ok' | 'warn' | 'fail'
  is_current        INTEGER NOT NULL DEFAULT 0  -- "best run" for this page
);

-- One row per extracted transaction line
CREATE TABLE transactions (
  extraction_id    TEXT NOT NULL REFERENCES extractions(extraction_id),
  row_index        INTEGER NOT NULL,
  owner            TEXT,               -- 'self' | 'spouse' | 'dependent' | 'joint'
  asset_name       TEXT,
  ticker           TEXT,               -- normalized: uppercased, stripped
  ticker_raw       TEXT,
  transaction_type TEXT,               -- 'purchase' | 'sale' | 'sale_partial' | 'exchange'
  transaction_date TEXT,               -- ISO 8601
  transaction_date_raw TEXT,
  notification_date TEXT,
  amount_low       INTEGER,
  amount_high      INTEGER,
  amount_raw       TEXT,
  cap_gains_over_200 INTEGER,          -- 0/1/NULL; example of a late-discovered field
  extra            TEXT,               -- JSON: anything the schema didn't anticipate
  row_confidence   REAL,
  PRIMARY KEY (extraction_id, row_index)
);

-- The "one big table" people actually want
CREATE VIEW transactions_current AS
SELECT f.*, e.model_id, e.prompt_version, e.run_at, t.*
FROM transactions t
JOIN extractions e ON e.extraction_id = t.extraction_id
JOIN filings f     ON f.filing_id = e.filing_id
WHERE e.is_current = 1;
```

### Principles

- **Keep `raw_response`.** When a new field is discovered, backfill from stored responses rather than re-running the batch.
- **Never overwrite a run.** Re-runs with a better model or prompt get a new `extraction_id`; `is_current` selects the winner. This is what makes "did the new prompt help?" answerable.
- **Normalize and preserve.** Every cleaned field has a `_raw` sibling. Analysts filter on the clean column and audit against the raw one.
- **`extra` is a JSON escape hatch**, not a dumping ground. When something shows up in `extra` consistently, promote it to a real column and note it in `CHANGELOG-SCHEMA.md`.

### Extraction JSON schema

The schema passed to the model for structured output should be a **superset** of `transactions` columns plus a free-form `extra` object per row and a page-level `notes` string. Version it in `schemas/extraction-vN.json`; `prompt_version` in `extractions` must reference the exact version used.

---

## 5. Repository layout

Git holds text; databases are derived and never committed.

```
.
├── PROJECT_BRIEF.md            # this file
├── README.md
├── LICENSE                     # code license
├── LICENSE-DATA.md             # data usage restrictions (see §8)
├── CHANGELOG-SCHEMA.md         # field promotions, schema versions
├── schemas/
│   └── extraction-v1.json      # JSON schema sent to the model
├── prompts/
│   └── extract-v1.md           # prompt text, versioned in lockstep with schema
├── pipeline/
│   ├── discover.py             # find new filings, write filings index
│   ├── prepare.py              # text-layer detection, rasterize scans only
│   ├── submit_batch.py         # build JSONL requests, submit Batch job
│   ├── collect_batch.py        # fetch results -> data/ text files
│   ├── validate.py             # row/page checks; sets validation_status
│   ├── ingest.py               # text files -> SQLite (three tables + view)
│   └── build_release.py        # SQLite -> DuckDB + Parquet, attach to Release
├── data/
│   ├── filings.csv             # index; one row per PDF; sorted by filing_id
│   ├── extractions/
│   │   └── {chamber}/{year}/{filing_id}.jsonl   # one line per run, raw_response inline
│   └── transactions/
│       └── {chamber}/{year}/{filing_id}.jsonl   # one line per row, sorted (extraction_id,row_index)
└── .github/workflows/
    ├── nightly-batch.yml
    └── release.yml
```

### Diff-friendliness rules

Git's value here is line-level history, so:

- One file per filing, **not** one file per batch night.
- Deterministic sort order and fixed key order in every JSON line. A re-run should change only the lines it actually changed.
- Fixed column order in CSVs. Add columns at the end; never reorder.
- Nightly commits should carry the batch ID, model, and prompt version in the commit message.

### Size management

GitHub's practical ceiling is a few GB per repo and 100 MB per file. Raw responses are roughly 2–5x the flattened rows. Plan: keep them in-repo until size becomes a problem, then move `data/extractions/` to a separate `-raw` repository or to gzipped per-year Release assets. Do not use Git LFS for this; it makes the text un-diffable, which defeats the purpose.

Do **not** commit `.sqlite`, `.duckdb`, or `.parquet` files. They are not diffable and belong in Releases.

---

## 6. Nightly batch workflow

1. `discover.py` — pull the list of new/changed filings from the source indexes; append to `data/filings.csv`.
2. `prepare.py` — for each new filing, detect text layer; download PDF; rasterize only if no text layer.
3. `submit_batch.py` — build a JSONL of ~1,000 requests (whole PDFs where possible, page images otherwise), each with the current prompt and JSON schema; submit as a Batch job; record `batch_id`.
4. Wait (Batch targets ≤24h; in practice usually much faster).
5. `collect_batch.py` — fetch results; write one line per run into `data/extractions/...`; flatten rows into `data/transactions/...`.
6. `validate.py` — run checks (see below); set `validation_status`; queue failures for escalation.
7. Commit with message like `batch: 2026-09-19 · gemini-3.7-flash · prompt v1 · batch_id=...`.

Because throughput is not the constraint, larger batches less often (e.g., 5,000 every few nights) are fine if that simplifies operations.

### Validation checks (initial set)

- Row count on page vs. count the model reports in `notes`.
- `amount_low <= amount_high`, both from the known set of range boundaries.
- `transaction_date` parses and falls within a plausible window of `filing_date`.
- `ticker` is either NULL or matches `^[A-Z.\-]{1,6}$` (loosen if needed).
- Any row with non-empty `extra` gets `warn` so new fields surface early.

Pages with `fail` go to the escalation model; pages with `warn` are reviewed in bulk during discovery.

### Test set

Maintain ~200 hand-verified pages across eras and form types under `tests/golden/`. Every prompt or model change must report precision/recall on this set before being adopted. This is the only defensible basis for the routing decisions in §2.

---

## 7. Release workflow (quarterly-ish)

`release.yml` on manual dispatch or a `vYYYY.Q` tag:

1. Check out the repo at the tag.
2. `ingest.py` — load all text files into a fresh SQLite database using the DDL in §4; apply `is_current` selection.
3. Run validation queries; fail the release if counts or invariants regress against the previous release.
4. `build_release.py` — export DuckDB file and Parquet (one file per table); generate `RELEASE_NOTES.md` with row counts, date coverage, model/prompt versions in use, and a pointer to `LICENSE-DATA.md`.
5. Attach `.sqlite`, `.duckdb`, `*.parquet`, and notes to the GitHub Release.

Reproducibility matters more than speed: anyone with the repo must be able to rebuild identical assets locally with `python pipeline/ingest.py && python pipeline/build_release.py`.

### Downstream conveniences (not in scope, but keep them possible)

- DuckDB can query Parquet straight from a Release URL; mention this in the README.
- Datasette can serve the SQLite asset directly if a browsable frontend is ever wanted.

---

## 8. Data usage restrictions

This is public data, but not unrestricted data. The Ethics in Government Act restricts use of these reports for commercial purposes, credit determinations, and solicitation, and the House and Senate download pages carry similar language.

- Document the exact restrictions in `LICENSE-DATA.md`, separate from the code `LICENSE`.
- Every Release's notes must include a one-line pointer to `LICENSE-DATA.md`, because people will find the assets via search and never read the README.
- Do not add any feature that facilitates a prohibited use.

---

## 9. Open questions

Record answers here as they're settled.

- What is the ongoing monthly filing cadence? (Affects nothing about design; affects how soon nightly runs become mostly empty.)
- Exact discovery mechanism for new filings per chamber (index pages, search endpoints, or bulk downloads)?
- Do we store the source PDFs in-repo, in a separate archive, or only by URL? (Leaning: URL plus content hash in `filings`, PDFs cached outside Git.)
- Which era boundary, if any, justifies routing to Flash-Lite? Decide only after the golden-set comparison.

---

## 10. Working agreements for a Claude Code co-implementer

- Read this file and `CHANGELOG-SCHEMA.md` before changing anything under `pipeline/` or `schemas/`.
- Any change to the prompt or schema bumps the version and must be evaluated against `tests/golden/` before merging.
- Never write a pipeline step that overwrites an existing extraction; add a new run and update `is_current`.
- Never commit database files.
- When in doubt about a pricing or model-ID detail, check the two Google URLs in §2 rather than relying on this document.
