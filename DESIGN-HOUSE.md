# House Financial Disclosures — Pipeline Design

A delta from `DESIGN-SENATE.md`. Everything not mentioned here is the same:
R2 for raw files, the manifest as source of truth, the publishing split, the
three-job Actions orchestration, secrets, data terms, and the extraction tier
in `DESIGN-OCR.md`.

**Status:** design, 2026-09-18, from discovery against the Clerk site and the
prior House work in `prior_art/paper/`. Nothing built yet in this repo.

---

## 1. Scope decisions

- **Own the whole path from primary source.** No TattooedHead rows in the
  dataset. Their feed is used only as a *validation oracle*: per-document row
  counts on e-filed PTRs, reported as an agreement rate in the README. Reasons:
  their parser silently drops every paper-filed PTR (29% of filings), the repo
  has no license and one maintainer, and the portfolio value is in "built from
  the Clerk's PDFs, warts and all".
- **PTRs first (FilingType `P`).** Annual reports (`A`, and probably
  `C`/`H`/`T`) are a later phase: multi-page, unsized, never touched by the
  prior work. Extension notices (`X`), and types `D`/`W` are indexed but not
  downloaded until their meaning is confirmed.
- **Uniform vision path for e-filed and scanned.** The prior work showed
  pdfplumber parsing of e-filed PTRs is fragile (small-caps fonts serialise
  with mangled case, checkbox widgets become glyph runs, whole rows collapse
  into one cell). Every document goes to a vision model; document class
  drives which model and how the pages are sent (e-filed as the PDF itself,
  scans as page images), not whether a parser is tried first.

---

## 2. How the House job differs in shape

| Concern | Senate | House |
|---|---|---|
| Listing | paginated JSON search behind a legal-ack gate | one small zip per year, `<year>FD.zip`, containing a 9-field TSV/XML index; regenerated daily |
| Access | Akamai edge; needs `curl_cffi` Chrome impersonation + CSRF handshake | open; plain `requests` with a contact User-Agent and a polite delay |
| Incremental | date high-water mark | set difference of `DocID` against the manifest |
| Documents | electronic HTML, or per-page GIFs from a viewer | one PDF per filing; ~70% native text, ~30% CCITT bilevel scans at 200 dpi |
| Format detection | HTML vs GIF viewer | DocID length + prefix (table below); confirm with `pdffonts` (zero fonts = scan) |
| URL scheme | report id | `ptr-pdfs/<year>/<docid>.pdf` for `P`; `financial-pdfs/<year>/<docid>.pdf` for the rest |
| Extraction prompt | transcription-first (now retired) | direct-to-JSON table extraction, validated on 114 docs |

Index fields: Prefix, Last, First, Suffix, FilingType, StateDst, Year,
FilingDate, DocID. No page counts, no format flag, no transaction data. The
zip also carries `<year>FD.xml` (`<FinancialDisclosure><Member>...`), which is
easier to parse than the CRLF TSV.

DocID shape by filing type, from the 2026 index (1,302 rows, 2026-09-19):

| DocID shape | FilingType | Rows | Class |
|---|---|---|---|
| 8-digit, `1xxxxxxx` | `A`/`C`/`H`/`T` | 627 | e-filed annual-type |
| 8-digit, `2xxxxxxx` | `P` | 246 | e-filed PTR |
| 8-digit, `3xxxxxxx` | `X` | 188 | e-filed extension |
| 8-digit, `4xxxxxxx` | `D` | 38 | e-filed |
| 7-digit, `8xxxxxx`/`9xxxxxx` | `P`/`C`/`D`/`X`/`W` | 184 | paper |
| 4-digit, `8xxx` | `W` | 59 | paper |

So "prefix 2 means e-filed" holds only for PTRs. Classify on length first
(8 digits = e-filed, shorter = paper), then confirm with `pdffonts`.

---

## 3. Phase 1 — Collect (House variant)

- `session.py`: plain `requests.Session`, User-Agent with contact email,
  ~2 s delay + jitter. No handshake. Keep the module boundary so the Senate
  session can swap in.
- `listing.py`: for each year in scope, download `<year>FD.zip`, parse the
  `.txt`, filter to `FilingType == P` (configurable), diff `DocID` against the
  manifest. One request per year. 2008–present all resolve today.
- `download.py`: fetch the PDF by filing type + year + DocID; store under
  `raw/house/<year>/<filingtype>/<docid>.pdf`. Classify `scanned|efiled` on
  arrival (prefix, then `pdffonts` as the tiebreak) and record it.
- `filings.csv`: the shared `filings` table (DESIGN-OCR §5) plus House
  columns `filing_type` and `docid_prefix_class`. `report_type` is derived
  from `filing_type`.
- **Amendments:** how the FD index and the PDFs mark an amended PTR is not
  yet known (open question 6). Whatever the mechanism, an amendment gets its
  own `filing_id` with `amends_filing_id` set; the original is never dropped
  or overwritten.
- Rendering: e-filed PDFs are sent to the model as-is (258 tokens/page on
  Gemma, text layer preserved). Only scans are rendered, with
  `pdftoppm -png -r 150`, to ephemeral local disk; PNGs are never stored.
  Scans are ~25 KB/page CCITT so rendering is cheap.

---

## 4. Phase 2 — Extraction (House specifics)

Shared tier in `DESIGN-OCR.md`. House-only pieces, all lifted from
`prior_art/paper/`:

- **Normalisers** (`house_ingest.py`): `clean_house_ticker` with its two
  rules (in-row bracket match; vocabulary + paren guard), `parse_house_amount`
  (bucket / open-ended / exact), owner and type code maps, asset-name split.
  Port with the 27 tests. `KNOWN_ASSET_TYPE_CODES` is empirical; do not
  hand-extend it.
- **Date-plausibility guard** goes in ingest, not the extractor:
  `transaction_date > notification_date` or `> today` → flag. ~2% of pilot
  rows.
- **Header count is untrusted.** 7/114 pilot docs miscounted; trust the row
  list and report the mismatch as a sanity signal for routing.
- **Confidence field from the model is useless** (always "high" in the pilot).
  Do not route on it; route on schema validity + sanity checks + two-model
  agreement.
- **Scanned checkbox forms** are the known hard case (the 12B flattened partial
  sales and emitted checkbox amounts as letters). Under the routing rule they
  go to Gemini Flash first (1,102 tokens/page), with self-hosted Gemma 31B at
  1120 on Colab as adjudicator. The prior scan-path validation was n=1, so
  the golden set must over-sample scans. When a checkbox amount comes back
  as a single letter, ingest maps it deterministically to the form's column
  order (A = lowest bucket upward); that map was part of the pilot recipe and
  belongs in `ingest.py`, not the prompt.

---

## 5. Validation oracle

Per e-filed document, compare our `n_transactions` and the multiset of
`(date, ticker, type)` against TattooedHead's `filings_manifest.json` /
`all_transactions.json`. Report agreement rate by year in the README. Their
regex jam-recovery (added after July 2026) is unvalidated, so disagreement is
a signal for review, not a verdict against us.

---

## 6. Sizing (provisional)

| Measure | Value | Source |
|---|---|---|
| PTR filings 2013–2026 | 8,379 | TattooedHead manifest, 2026-09-17 |
| Scanned share | 29% | same |
| Pages per PTR (pilot n=122) | 83 one, 33 two, 6 three+ | paper repo png dir |
| Avg PDF size | ~64 KB | pilot |
| PTR corpus | ~0.5 GB (extrapolated) | |
| Annual reports | unsized | |

E-filed PTRs (~5,900) on Gemma at ~300 tokens/doc and 14.4K RPD: under a
day. Scanned PTRs (~2,500) on Gemini Flash Tier 1: a few hours, ~$3. Verify
with the golden set and the first sharded run.

---

## 7. Open questions

1. **Filing-type legend.** `A`/`C`/`D`/`H`/`T`/`W`/`X` meanings are guessed
   (`H` and `T` appear twice each in 2026; both share the e-filed annual
   prefix); find the Clerk's legend before downloading anything but `P`.
   Older years add more: 2015 has `O` (440 rows, both e-filed prefix `1`
   and paper), `E` (5) and `G` (1), and 4-digit paper DocIDs there start
   with `6`, not `8`. The 8-digit = e-filed rule held in 2015. About 2% of
   rows in each year probed have a blank FilingDate, all type `W`.
2. ~~Clerk Terms of Service~~ **Resolved 2026-09-19:** that PDF is a table of
   members' terms of service in Congress, not usage terms. The Clerk's
   disclosure site carries no usage terms or acknowledgement; only the
   statute applies. Recorded in `DATA-TERMS.md`.
3. **Annual report volume and page counts.** Needed before scoping phase 2.
4. **Prefix classification accuracy.** The §2 table is from one year's
   index; validate length/prefix → scanned/e-filed against `pdffonts` on a
   sample, and check that older years follow the same scheme, before relying
   on it for routing.
5. ~~Statute coverage~~ **Resolved:** `DATA-TERMS.md` now states that Title I
   covers both chambers.
6. **Amendment detection.** Not investigated. Find out whether the FD index
   carries an amendment flag or type, whether the PDF is marked, and whether
   a reliable link to the original filing can be derived.

---

## 8. Build order

1. `session.py` (plain requests) + `listing.py`; diff one year's index against
   an empty manifest.
2. `download.py` + classification; pull 20 docs spanning both classes into R2.
3. Port `house_ingest.py` + tests; wire the date guard.
4. Golden set (DESIGN-OCR §6) seeded with those 20 docs; run both Gemma models
   and Gemini Flash.
5. Routing rule from the golden-set numbers; `extract/gemini.py` end to end.
6. Validation oracle report.
7. Shard the PTR backfill by year; recent years first.
8. Annual reports, once sized.
