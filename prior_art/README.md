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

Still in the paper repo and not copied: the Gemma extraction harness
(`gemma_extract.py`, `jam_backfill.py`), the pilot JSON output, and the
serving-recipe findings.
