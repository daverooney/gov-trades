"""The filings manifest: one row per source document, kept as CSV in git.

Schema follows the ``filings`` table in DESIGN-OCR §5 plus the House-only
columns from DESIGN-HOUSE §3. Rows are written in ``filing_id`` order with a
fixed column order so diffs stay readable.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path

COLUMNS = (
    "filing_id",
    "chamber",
    "filer_name",
    "filer_id",
    "report_type",
    "amends_filing_id",
    "filing_date",
    "source_url",
    "raw_key",
    "content_sha256",
    "page_count",
    "doc_class",
    "first_seen_at",
    "review_flag",
    "review_reason",
    # House-only
    "filing_type",
    "docid_prefix_class",
)


@dataclass
class Filing:
    filing_id: str
    chamber: str
    filer_name: str = ""
    filer_id: str = ""
    report_type: str = ""
    amends_filing_id: str = ""
    filing_date: str = ""
    source_url: str = ""
    raw_key: str = ""
    content_sha256: str = ""
    page_count: str = ""
    doc_class: str = ""
    first_seen_at: str = ""
    review_flag: str = "0"
    review_reason: str = ""
    filing_type: str = ""
    docid_prefix_class: str = ""

    def to_row(self) -> dict[str, str]:
        return {k: ("" if v is None else str(v)) for k, v in asdict(self).items()}

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Filing":
        known = {f.name for f in fields(cls)}
        return cls(**{k: (v or "") for k, v in row.items() if k in known})


assert tuple(f.name for f in fields(Filing)) == COLUMNS


Manifest = dict[str, Filing]


def read_manifest(path: Path) -> Manifest:
    """Load the manifest keyed by filing_id. A missing file is an empty manifest."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return {row["filing_id"]: Filing.from_row(row) for row in reader}


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Write atomically, sorted by filing_id, fixed column order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for filing_id in sorted(manifest):
            writer.writerow(manifest[filing_id].to_row())
    tmp.replace(path)
