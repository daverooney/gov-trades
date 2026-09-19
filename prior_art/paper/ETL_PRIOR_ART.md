# House disclosure ETL — prior art for the acquisition repo

> **Read me if you are a Claude Code instance in a separate data-acquisition
> repo, crawling this analysis repo for reusable House-PTR ETL work.** This
> repo (`paper`) is pivoting to be analysis-only; the acquisition/ETL concerns
> documented here are moving out. Everything below was built and validated
> here 2026-07-06 → 2026-07-19. Lift the code and — more importantly — the
> edge-case catalog. The failure modes were expensive to find.

## TL;DR of what exists to reuse

| Concern | Where | State |
|---|---|---|
| PDF → transactions extraction harness | `gemma_extract.py`, `jam_backfill.py` | Working, validated 114/114 |
| Extraction-JSON → normalized rows | `../../src/paper/house_ingest.py::parse_extraction` | Working, 27 tests |
| Ticker phantom filter (load-bearing) | `../../src/paper/house_ingest.py::clean_house_ticker` | Working, validated on pilot |
| Amount / owner / type / asset-type normalization | same module | Working |
| Serving recipe (Gemma vision) | repo-root `FINDINGS.md` grid + addendum | Documented |

**Not built yet** (open work for the new repo): the TattooedHead *clean-feed*
mapping + downloader, a unified ingest CLI merging clean + extracted rows, a
date-plausibility guard, and the actual full-corpus backlog run. See
"Open work" at the bottom.

## Data sources (the "spiritual ancestors")

Original sites, both **dead**: `housestockwatcher.com`, `senatestockwatcher.com`.
Living successors and primaries:

- **House (our path):** `github.com/TattooedHead/house-stock-watcher-data` —
  direct successor to housestockwatcher; created 2026-05-29, daily cron,
  2012→present (~23.6k rows), old-API-compatible JSON, per-row `source_url`
  to the Clerk PDF. **Caveat:** its pdfplumber parser *jams* on ~20.3k unique
  rows (logs 21,122 raw) — those are the rows the extraction harness recovers.
- **House primary:** `disclosures-clerk.house.gov/public_disc/ptr-pdfs/<year>/<docid>.pdf`
  (annual index in each year's `<year>FD.zip`). This is what TattooedHead
  mirrors and what our extractor reads directly. Also our `ptr_link`.
- **Senate mirror:** `github.com/timothycarambat/senate-stock-watcher-data` —
  clean JSON but **frozen at 2020-12-02** (fine for backtest, dead for live).
- **Senate primary:** `efdsearch.senate.gov` — gated behind a legal-ack wall.
- **Both chambers:** `capitoltrades.com` — alive but **429s** our scraper even
  at 1 req/min (see `../../src/paper/capitoltrades_scrape.py`, `ROADMAP.md`).

## Extraction harness (PDF → JSON)

`jam_backfill.py`: Clerk fetch → `pdftoppm` 150 DPI PNGs → Gemma vision
(`gemma_extract.py`) → per-doc JSON. Resumable, per-doc checkpoint, **3×
retries 90s apart** (the tailscale path to the WSL inference host flaps and
stalls large uploads mid-batch — retries absorb it). Output shape:
`{model, year, docs:[{doc_id, filer, jammed_rows, n_transactions,
transactions:[...], legibility, confidence}]}`.

Serving recipe headline (full grid in repo-root `FINDINGS.md`): **Gemma 4 12B
nothink at `--image-min/max-tokens 1120`** is the first-pass workhorse
(~43s/doc median). Every local-model failure was *perception starvation* at
the default ~130-token page read; at 1120 the name confabulation vanishes and
the 31B is Sonnet-grade. Claude Sonnet as verifier tier went 33/33. Greedy
decoding is bit-identical across reruns.

Validated pilot: **114/114 jammed 2026 docs, zero parse failures**, 359
transactions. Projection: ~5 GPU-days for all 4,947 jammed docs, ~12h for the
2022–2026 tail (1,101 docs).

## The normalization layer (`src/paper/house_ingest.py`)

Pure functions, no I/O — mirrors the `senate_ingest.py` pattern. `parse_extraction(data)`
converts one extraction file to `PoliticianTrade` rows. Key mappings:

- **owner codes:** blank/None→`Self`, `SP`→`Spouse`, `JT`→`Joint`, `DC`→`Child`
- **type codes:** `P`→`buy`, `S` / `S (partial)`→`sell`, `E`→`exchange`
- **`doc_id` + file `year`** → Clerk PDF URL as `ptr_link`; `ptr_row_idx` is a
  per-doc 0-based index over *kept* rows (source order) → idempotent under the
  DB `UNIQUE(source, ptr_link, transaction_date, ticker, amount_raw, owner,
  raw_type, ptr_row_idx)` constraint.
- **`asset_name`** ("Verizon … Common Stock (VZ) [ST]\nFILING STATUS: New") is
  split into a clean description + a 2-letter asset-type code.
- **`FILING STATUS:` lines** are boilerplate → dropped from `comment`;
  `SUBHOLDING OF:` / `DESCRIPTION:` context is kept.

## Edge-case catalog — the expensive-to-find part

### 1. Ticker phantoms: bracket asset-type codes read as tickers (CRITICAL)
House PTRs print the **real ticker in parens** — `(VZ)` — and the
**asset-TYPE in brackets** — `[ST]`, `[GS]`, `[OT]`. The 12B extractor
routinely emits the *bracket code* as the ticker. In the 2026 pilot **128/225
non-null tickers were codes, not symbols** — including **`GS` ×100** (the
`[GS]` government-security code, which would masquerade as Goldman Sachs and
silently poison a mirror-the-buys backtest). Also `OT`, `OI`, `CS`, `HN`, `ST`,
`PS`. Muni/bond-heavy filings didn't exist in the original 9-doc gauntlet, so
this only surfaced at pilot scale.

`clean_house_ticker(ticker, asset_name)` fixes it deterministically, two rules:
1. **In-row bracket match** — if the ticker equals a `[XX]` code literally
   present in that row's `asset_name`, it's the asset-type code → null it
   (recovering a real `(XXX)` paren ticker if one is present).
2. **Vocabulary + paren-guard** — if the ticker is in `KNOWN_ASSET_TYPE_CODES`
   `{CS,CT,GS,HN,OI,OT,PS,RS,ST,VA}` (derived empirically from every `[XX]`
   seen bracketed across the corpus — **do not hand-guess the enumeration**)
   **and** it is not parenthesized in its own asset_name → null it. This
   catches the residual where the model *drops* the bracket (e.g.
   `"US Treasury Bill"` with ticker `GS`).

**Robustness property that makes this safe:** a genuine Goldman Sachs buy
prints `...(GS) [ST]` — `[GS]` is absent and `(GS)` is present, so a real GS
ticker survives *both* rules. Rule 2 strips `GS` only when no paren vouches
for it. Result on the pilot: 225→92 tickered rows, `GS 100→0`, **zero
asset-type codes leak**, real equities untouched.

### 2. Amount strings come in three shapes
`parse_house_amount` handles all three → `(low, high)` USD:
- standard bucket `$1,001 - $15,000` → `(1001, 15000)`
- open-ended `Spouse/DC Over $1,000,000` → `(1000000, None)`
- exact value `$318.74` → `(318.74, 318.74)`  ← easy to miss; single value, not a range

### 3. Dates: future/malformed misreads (~2% of rows)
Gemma occasionally misreads the date — the pilot had **7/359 malformed** and
at least one impossible **future date** (a `2026-12-26` buy seen mid-2026). A
transaction dated after its own disclosure/notification is impossible and would
break point-in-time backtest logic. **The mapper stays faithful (does not
rewrite source data); the date-plausibility guard belongs in the ingest step**,
where "now" and the disclosure date are both available (drop/flag
`transaction_date > disclosure_date` or `> today`).

### 4. Jam-log is ~2.6× duplicate-inflated (recent years)
TattooedHead's daily cron re-logs the same failing rows every run: 2026's 554
logged rows are only 211 unique (2025: 612→355). Historic years are near-unique.
Dedupe the jam log before scoping a backfill; whole log is 21,122 raw → 20,283
unique / 4,947 docs.

### 5. Header `n_transactions` miscounts
7/114 pilot docs had a wrong header count. **Trust the extracted row list, not
the header.**

### 6. Non-equity instruments legitimately have no ticker
Bonds, notes, structured products, BDCs, muni GOs → ticker `None` after
filtering. That's correct; they're excluded from an equity mirror anyway. In
the pilot only ~92/359 rows carry a real ticker.

### 7. Ops: inference-path flapping
The tailscale→WSL path to the local Gemma host blackholes/stalls large uploads
intermittently (MTU clamp is healthy and is *not* the culprit — the tunnel
flaps). Any batch client needs retries; `jam_backfill.py`'s 3×/90s absorbed
everything.

## File inventory (crawl these)

- `gemma_extract.py` — llama-server harness (OpenAI API, base64 page PNGs). Env
  knobs: `GEMMA_NOTHINK`, `GEMMA_TIMEOUT`, `GEMMA_TAG_SUFFIX`, `GEMMA_TEMP`,
  `GEMMA_MAX_TOKENS`.
- `jam_backfill.py` — fetch/render/extract driver (resumable, retries).
- `../../src/paper/house_ingest.py` — normalization + ticker filter (this doc's core).
- `../../src/paper/senate_ingest.py` — the pattern `house_ingest` mirrors.
- `../../tests/test_house_ingest.py` — 27 tests; every edge case above is encoded here.
- `extract_jam2026_*.json` — 114-doc pilot output (each doc embeds its jammed
  raw rows for reconciliation). `extract_*.json` — the model×mode×budget grid.
- `../../src/paper/schema.sql` — `politician_trades` table (the target shape).
- repo-root `FINDINGS.md` — extraction capability study, serving recipe, grid.

## Open work (not done here — carry into the new repo)

1. **TattooedHead clean-feed mapping + downloader** — fetch `all_transactions.json`,
   map its clean rows to the same normalized shape, run them through the *same*
   `clean_house_ticker`. Determine clean-vs-jammed doc split.
2. **Unified ingest** — merge clean + extracted rows, dedupe, upsert; add the
   date-plausibility guard (edge case #3).
3. **Full backlog run** — the 2022–2026 tail (~12h) or full corpus (~5 GPU-days).
4. Optional: a prompt clause telling the model tickers are parenthesized and
   bracket codes are asset types (belt-and-suspenders alongside the post-filter).
