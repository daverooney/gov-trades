# Prior art

Reference material only. Nothing here is on the import path and the tests
will not run from this repo.

## `paper/`

Copied from the `paper` analysis repo (sibling checkout at `../paper`) on
2026-09-18. That repo is pivoting to analysis-only; the ETL concerns are
moving here.

- `ETL_PRIOR_ART.md` - the handoff doc. Its value is the **edge-case catalog**
  (ticker phantoms, amount shapes, date misreads, jam-log duplication). Its
  relative paths (`../../src/paper/...`, `gemma_extract.py`, `jam_backfill.py`,
  `FINDINGS.md`, `schema.sql`) point into the paper repo and do not resolve here.
- `house_ingest.py` - the normalization layer and the load-bearing
  `clean_house_ticker` filter. Imports `paper.models.PoliticianTrade`, which
  does not exist in this repo. Lift the functions, not the module.
- `test_house_ingest.py` - 27 tests encoding every edge case above. Port them
  along with whatever gets lifted.

- `gemma_extract.py` - the llama-server client used for the pilot (copied
  2026-09-19). Base64 PNG pages, `enable_thinking=False`, greedy by default,
  fence-stripping JSON parser, usage/timings capture. Its `PROMPT` is the
  validated House prompt, now also at `prompts/colab/house-ptr-v1.md`. Reads
  `sys.argv` at import and hardcodes a `DOCS` dict; lift the client, not the
  module.
- `jam_backfill.py` - fetch, render (`pdftoppm -r 150`), extract, checkpoint
  driver. `fetch()` is most of the House download step; the retry loop and the
  per-doc `.raw.txt` checkpoint are the resume pattern. Its input is
  TattooedHead's jam log, which the FD-index diff replaces.
- `house_extraction_README.md` - the experiment folder's own README.

Still in the paper repo and not copied: 18 pilot `extract_*.json` outputs
under `experiments/house_extraction/` (552 KB; the Sonnet ones are
hand-verified-grade references) and the gitignored `data/house_pdfs/` corpus
(114 e-filed 2026 PDFs, 9 gauntlet/jammed PDFs, 172 rendered pages, the
21,122-row jam log). Useful for picking *which* DocIDs go in the House
golden set and for comparing model outputs; the PDFs themselves are
re-downloaded by this repo's collector, not copied (decision 2026-09-20: no
prior-art data enters the dataset, only prior-art code and findings).

## `colab/`

`senate_disclosures_pipeline_v14.ipynb` - the Senate eFD collect notebook
(2026-07-19), previously untracked in `~/Downloads`. Committed with outputs
because the run logs are the only sizing evidence for the Senate corpus
(DESIGN-SENATE §5). The Google Drive folder id is redacted. Cells that matter:

- cell 10: `_request` (5 retries, exponential backoff on 429/5xx, hard fail on
  401/403) and `make_session` (curl_cffi Chrome + CSRF + gate POST).
- cell 14: `_parse_row` (content-based), `_keep` (senator/candidate filter),
  `_fmt_and_id`, `SKIP_KINDS`, `fetch_all_rows` (page size 100), `build_index`.
- cell 16: manifest as local SQLite with CSV export at every checkpoint.
- cell 18: `download_report` - electronic to HTML; paper to the viewer's GIF
  pages in order, PDF fallback, `_viewer.html` fallback for inspection.
- cell 22: `run_phase1` - done means files present and not `_viewer.html`.

Do not port: the Google Drive sync (cells 4-6, top of 22), pandas, the
HF-transformers Phase 2 (cells 26-30, superseded by DESIGN-OCR), and the
`_fmt_and_id` UUID regex, which is lowercase-only while paper-filing hrefs
carry uppercase UUIDs (571 of 1,029 paper ids fell back to
`search_view_paper_<UUID>` strings). Match case-insensitively.

## `PROJECT_BRIEF.md`

A competing design from a separate deep dive, 2026-09-18. Its header lists
which parts were absorbed into the live design docs and which were not, and
why. Read the header; the body is kept verbatim for reference.
