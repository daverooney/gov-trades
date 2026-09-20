#!/usr/bin/env python3
"""Diff one year's Clerk FD index against the manifest.

Prints a JSON summary to stdout. Does not download documents or modify the
manifest; that is download.py's job (DESIGN-HOUSE §8 step 2).

Usage:
    uv run python scripts/house_listing.py --year 2026 [--filing-type P ...] [--all-types]
                                           [--manifest data/filings.csv] [--dump new.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gov_trades.config import PROJECT_ROOT, load_settings  # noqa: E402
from gov_trades.filings import read_manifest  # noqa: E402
from gov_trades.house.listing import classify_docid, fetch_index, new_filings  # noqa: E402
from gov_trades.house.session import FetchError, ThrottledSession  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--filing-type", action="append", default=None, help="repeatable; default P")
    ap.add_argument("--all-types", action="store_true")
    ap.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "filings.csv")
    ap.add_argument("--dump", type=Path, help="write the new rows as JSON here")
    args = ap.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")
    settings = load_settings()
    session = ThrottledSession(settings)
    manifest = read_manifest(args.manifest)
    types = None if args.all_types else frozenset(args.filing_type or ["P"])

    try:
        rows = fetch_index(session, args.year)
    except FetchError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    fresh = new_filings(rows, manifest, types)
    in_scope = [r for r in rows if types is None or r.filing_type in types]
    summary = {
        "ok": True,
        "year": args.year,
        "filing_types": sorted(types) if types else "all",
        "index_rows": len(rows),
        "in_scope": len(in_scope),
        "already_in_manifest": len(in_scope) - len(fresh),
        "new": len(fresh),
        "new_by_class": dict(Counter(classify_docid(r.doc_id) for r in fresh)),
        "index_by_type": dict(sorted(Counter(r.filing_type for r in rows).items())),
    }
    if args.dump:
        args.dump.write_text(json.dumps([asdict(r) for r in fresh], indent=1))
        summary["dump"] = str(args.dump)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
