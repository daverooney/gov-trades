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
- **PTRs first (FilingType `P`).** Annual reports (`A`, and probably `C`/`T`)
  are a later phase: multi-page, unsized, never touched by the prior work.
  Extension notices (`X`), and types `D`/`W` are indexed but not downloaded
  until their meaning is confirmed.
- **Uniform vision path for e-filed and scanned.** The prior work showed
  pdfplumber parsing of e-filed PTRs is fragile (small-caps fonts serialise
  with mangled case, checkbox widgets become glyph runs, whole rows collapse
  into one cell). Rendering every page and sending it to the model is the
  simpler design; document class drives the routing tier, not the code path.

---

## 2. How the House job differs in shape

| Concern | Senate | House |
|---|---|---|
| Listing | paginated JSON search behind a legal-ack gate | one small zip per year, `<year>FD.zip`, containing a 9-field TSV/XML index; regenerated daily |
| Access | Akamai edge; needs `curl_cffi` Chrome impersonation + CSRF handshake | open; plain `requests` with a contact User-Agent and a polite delay |
| Incremental | date high-water mark | set difference of `DocID` against the manifest |
| Documents | electronic HTML, or per-page GIFs from a viewer | one PDF per filing; ~70% native text, ~30% CCITT bilevel scans at 200 dpi |
| Format detection | HTML vs GIF viewer | DocID prefix: `2xxxxxxx` e-filed, `8`/`9xxxxxxx` paper; confirm with `pdffonts` (zero fonts = scan) |
| URL scheme | report id | `ptr-pdfs/<year>/<docid>.pdf` for `P`; `financial-pdfs/<year>/<docid>.pdf` for the rest |
| Extraction prompt | transcription-first (now retired) | direct-to-JSON table extraction, validated on 114 docs |

Index fields: Prefix, Last, First, Suffix, FilingType, StateDst, Year,
FilingDate, DocID. No page counts, no format flag, no transaction data.

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
- `manifest`: adds `filing_type`, `docid_prefix_class`, `n_pages`, and the
  shared `extract_status` / `model` / `prompt_version` / `review_flag` columns.
- Rendering: `pdftoppm -png -r 150` to ephemeral local disk only; PNGs are
  never stored. Scans are ~25 KB/page CCITT so rendering is cheap.

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
  sales and emitted checkbox amounts as letters). They go to the 31B first
  under the routing rule. The prior scan-path validation was n=1, so the
  calibration set must over-sample scans.

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

On the Gemini free tier at ~2K tokens/doc, the PTR corpus is 1–2 days on one
model. Verify with the calibration set and the first sharded run.

---

## 7. Open questions

1. **Filing-type legend.** `A`/`C`/`T`/`D`/`W`/`X` meanings are guessed; find
   the Clerk's legend before downloading anything but `P`.
2. **Clerk Terms of Service.** `clerk.house.gov/member_info/Terms_of_Service.pdf`
   has not been read. Confirm automated download is permitted and add any
   restriction to `DATA-TERMS.md`.
3. **Annual report volume and page counts.** Needed before scoping phase 2.
4. **Prefix classification accuracy.** Validate prefix → scanned/e-filed
   against `pdffonts` on a sample before relying on it for routing.
5. **Statute coverage.** `DATA-TERMS.md` quotes the statute as applied to
   Senate reports; the same Title I applies to House reports. Confirm and
   say so explicitly there.

---

## 8. Build order

1. `session.py` (plain requests) + `listing.py`; diff one year's index against
   an empty manifest.
2. `download.py` + classification; pull 20 docs spanning both classes into R2.
3. Port `house_ingest.py` + tests; wire the date guard.
4. Calibration set (DESIGN-OCR §6) using those 20 docs; run both Gemma models.
5. Routing rule from the calibration numbers; `extract/gemini.py` end to end.
6. Validation oracle report.
7. Shard the PTR backfill by year; recent years first.
8. Annual reports, once sized.
