#!/usr/bin/env python3
"""Backfill jammed rows by running each jammed doc through the local
multimodal model. Reuses the prompt/request logic from gemma_extract.py.

Two subcommands:

    uv run python data/house_pdfs/jam_backfill.py fetch [YEAR]
        Collect doc_ids for YEAR (default 2026) from jammed_rows.jsonl,
        download each PDF from the Clerk (2s polite delay, skip if
        present), render pages to png/<docid>-<n>.png at 150 DPI via
        pdftoppm (skip if page 1 exists).

    uv run python data/house_pdfs/jam_backfill.py extract [YEAR] [BASE_URL]
        Run every fetched doc through the model. Resumable: skips any
        doc whose .raw.txt already exists. Writes per-doc raw/stats
        files (jam<YEAR>_<tag>_<docid>.*) plus a combined
        extract_jam<YEAR>_<tag>.json including the mirror's jammed raw
        rows per doc for later comparison.

Env knobs are gemma_extract's: GEMMA_NOTHINK, GEMMA_TEMP, GEMMA_TIMEOUT,
GEMMA_MAX_TOKENS, GEMMA_TAG_SUFFIX.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import gemma_extract as gx

HERE = Path(__file__).parent
PNG = HERE / "png"
UA = {"User-Agent": "Mozilla/5.0 (research; contact d@verooney.com)"}


def jammed_docs(year: str) -> dict[str, list[str]]:
    """doc_id -> raw jammed rows for that filing year."""
    docs: dict[str, list[str]] = defaultdict(list)
    for line in (HERE / "jammed_rows.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if str(r["year"]) == year:
            docs[r["doc_id"]].append(r["raw"])
    return dict(docs)


def fetch(year: str) -> None:
    pdf_dir = HERE / f"jam{year}_pdfs"
    pdf_dir.mkdir(exist_ok=True)
    docs = jammed_docs(year)
    print(f"{len(docs)} docs for {year}", file=sys.stderr)
    for i, doc_id in enumerate(sorted(docs), 1):
        pdf = pdf_dir / f"{doc_id}.pdf"
        if not pdf.exists():
            url = (f"https://disclosures-clerk.house.gov/public_disc/"
                   f"ptr-pdfs/{year}/{doc_id}.pdf")
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=60) as r:
                    pdf.write_bytes(r.read())
            except Exception as e:
                print(f"{doc_id}: FETCH FAILED {e}", file=sys.stderr)
                continue
            time.sleep(2)
        if not (PNG / f"{doc_id}-1.png").exists():
            res = subprocess.run(
                ["pdftoppm", "-png", "-r", "150", str(pdf), str(PNG / doc_id)],
                capture_output=True, text=True)
            if res.returncode != 0:
                print(f"{doc_id}: RENDER FAILED {res.stderr.strip()}",
                      file=sys.stderr)
                continue
        n = len(list(PNG.glob(f"{doc_id}-*.png")))
        print(f"[{i}/{len(docs)}] {doc_id}: {n} page(s)", file=sys.stderr)
    print(json.dumps({"done": True, "docs": len(docs)}))


def extract(year: str, base_url: str | None) -> None:
    # gemma_extract resolves BASE from *its* argv at import time, which here
    # is our subcommand word — always set it explicitly.
    gx.BASE = (base_url or
               "http://blackbird2-wsl.tail2aa4e.ts.net:8000").rstrip("/")
    docs = jammed_docs(year)
    model = gx.get_model()
    tag = "".join(c if c.isalnum() else "_" for c in model.split("/")[-1])[:48]
    if gx.NOTHINK:
        tag += "_nothink"
    tag += __import__("os").environ.get("GEMMA_TAG_SUFFIX", "")
    prefix = f"jam{year}_{tag}"
    print(f"model: {model} | {len(docs)} docs | tag {prefix}", file=sys.stderr)
    combined = {"model": model, "year": year, "docs": []}
    for i, (doc_id, jam_rows) in enumerate(sorted(docs.items()), 1):
        pages = sorted(p.name for p in PNG.glob(f"{doc_id}-*.png"))
        raw_path = HERE / f"{prefix}_{doc_id}.raw.txt"
        entry: dict = {"doc_id": doc_id, "jammed_rows": jam_rows}
        if not pages:
            print(f"[{i}/{len(docs)}] {doc_id}: NO PAGES, skipped",
                  file=sys.stderr)
            entry["error"] = "no rendered pages"
            combined["docs"].append(entry)
            continue
        if raw_path.exists():  # resume: reuse the prior response
            text = raw_path.read_text()
            stats = {"resumed": True}
        else:
            # The tailscale path to the server flaps (direct<->relay path
            # migration stalls big uploads); ride it out with spaced retries.
            for attempt in range(1, 4):
                try:
                    text, stats = gx.ask(model, doc_id, pages)
                    break
                except Exception as e:
                    print(f"[{i}/{len(docs)}] {doc_id}: attempt {attempt} "
                          f"failed: {e}", file=sys.stderr)
                    if attempt == 3:
                        entry["error"] = str(e)
                    else:
                        time.sleep(90)
            if "error" in entry:
                print(f"[{i}/{len(docs)}] {doc_id}: FAILED after 3 attempts",
                      file=sys.stderr)
                combined["docs"].append(entry)
                continue
            raw_path.write_text(text)
            (HERE / f"{prefix}_{doc_id}.stats.json").write_text(
                json.dumps(stats, indent=1))
        parsed = gx.parse_json(text)
        if parsed is None:
            entry.update({"error": "bad JSON", "raw": text[:500]})
        else:
            parsed["_stats"] = stats
            entry.update(parsed)
        n = entry.get("n_transactions", "unparseable")
        secs = stats.get("seconds", 0)
        print(f"[{i}/{len(docs)}] {doc_id}: {secs}s, pages={len(pages)}, "
              f"n_transactions={n}, jammed={len(jam_rows)}", file=sys.stderr)
        combined["docs"].append(entry)
        # checkpoint the combined file every doc so a crash loses nothing
        (HERE / f"extract_{prefix}.json").write_text(
            json.dumps(combined, indent=2))
    print(json.dumps({"done": True, "model": model,
                      "docs": len(combined["docs"])}))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    year = sys.argv[2] if len(sys.argv) > 2 else "2026"
    if cmd == "fetch":
        fetch(year)
    elif cmd == "extract":
        extract(year, sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        sys.exit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    main()
