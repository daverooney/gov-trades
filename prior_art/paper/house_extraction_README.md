# House PTR extraction capability study (2026-07-06/07)

Can vision LLMs extract transactions from disclosures-clerk.house.gov PTR
PDFs — including the ~21k rows the TattooedHead/house-stock-watcher-data
pdfplumber parser jams on? Full results and the serving recipe live in the
repo-root **FINDINGS.md** (addendum + grid sections).

Contents:
- `gemma_extract.py` — llama-server harness (OpenAI API, base64 page PNGs).
  Env knobs: `GEMMA_NOTHINK=1`, `GEMMA_TIMEOUT=<s>`, `GEMMA_TAG_SUFFIX=_1120`,
  `GEMMA_TEMP=<t>` (sampled consensus runs), `GEMMA_MAX_TOKENS=<n>`.
- `jam_backfill.py` — fetch/render/extract driver for jam-log docs
  (per-doc checkpointing, resumable, 3× retries for a flappy network path).
- `extract_<model>[_nothink][_1120].json` — model outputs per condition
  (Claude Haiku/Sonnet via subagents; Gemma 4 12B/31B QAT via llama-server).
- `extract_..._nothink_560.json` — the middle-budget pass (12B): cure holds,
  ~18% faster, small-glyph desc fields decay; effective read ~470-505 tok/page.
- `extract_..._nothink_1120_t0_r2.json`, `..._t1_r1..5.json` — repeat-run
  consensus study on the scan form (greedy is bit-identical; sampled flicker
  lands exactly on the genuinely ambiguous field).
- `extract_jam2026_..._nothink_1120.json` — all 114 jammed 2026 docs,
  each with the mirror's jammed raw rows embedded for reconciliation.
- `picked.txt` / `picked_jammed.txt` — the 6 gauntlet + 3 jammed doc IDs.
  PDFs re-fetchable: disclosures-clerk.house.gov/public_disc/ptr-pdfs/<year>/<docid>.pdf
  (year index in the annual FD.zip). Ground truth was established by manual
  read; per-doc scoring is in FINDINGS.md.

Headline: every local-model failure was perception starvation at the
default ~130-token page read. At `--image-max-tokens 1120` the 31B is
locally Sonnet-grade and the 12B's name confabulation vanishes.

Backfill pilot headline (2026-07-07): 114/114 jammed 2026 docs extracted
clean at ~87s/doc avg; recent jam-log years are ~2.6× duplicate-inflated;
one new systematic error — bracket asset-type codes ([GS], [OT]) read as
tickers — needs a deterministic post-filter before ingestion.
