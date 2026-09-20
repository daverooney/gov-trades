# Gov Trades

### Who knows what they're buying? You do!

Well, in theory. Eventually.

This repo is a spiritual successor to housestockwatcher.com and senatestockwatcher.com (both now sadly defunct), as well as the repos of shoulders of giants [here](https://github.com/timothycarambat/senate-stock-watcher-data) and [here](https://github.com/TattooedHead/house-stock-watcher-data). It collects known stock trades made by holders of public office in the United States of America, sourced from their public disclosures. 

This repo was conceived as a project to brush up on data pipeline use and design, with a public data source as a side benefit. 

The data presented here was sourced from these official sources:
- [The Office of the Clerk of the House of Representatives](https://disclosures-clerk.house.gov/FinancialDisclosure)
- [The United States Senate Financial Disclosures page](https://efdsearch.senate.gov/)

Other sources may be added in the future.

## Layout

- `scripts/probe_efd.py` - go/no-go probe: can this host clear the Senate eFD edge? Run with `uv run scripts/probe_efd.py`.
- `.github/workflows/probe.yml` - runs the probe weekly from a GitHub-hosted runner.
- `DESIGN-SENATE.md` - pipeline design for the Senate side (the base design).
- `DESIGN-HOUSE.md` - House pipeline as a delta from the Senate design.
- `DESIGN-OCR.md` - the shared extraction tier: Gemma 4 on the Gemini API, Colab overflow, routing and calibration.
- `prior_art/` - reference material carried over from the earlier House work. Not on the import path.
- `src/gov_trades/` - the library. `config.py` (env vars only), `storage.py` (local directory or R2), `filings.py` (the manifest CSV), and `house/` (Clerk session and yearly index).
- `scripts/` - thin CLIs that print a JSON summary. `house_listing.py --year 2026` diffs one year's FD index against the manifest.
- `tests/` - unit tests; no network.

## Development

```bash
cp .env.example .env    # set CONTACT_EMAIL at minimum
uv sync
uv run python -m pytest
uv run python scripts/house_listing.py --year 2026
```

## License and data terms

The **code** in this repo is mine and is under the [MIT License](LICENSE).

The **data** is not covered by that license. It is public-record material published by the House and Senate, and its use is restricted by federal statute regardless of how it reaches you. See [DATA-TERMS.md](DATA-TERMS.md) for the restriction text. In short: no commercial use (other than news media dissemination), no credit-rating use, and no use in soliciting money.
