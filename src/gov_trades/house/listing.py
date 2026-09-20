"""The Clerk's yearly financial-disclosure index.

Each year has ``<year>FD.zip`` holding a TSV and an XML copy of the same
nine-field table (DESIGN-HOUSE §2). The XML is parsed here; the TSV is CRLF
with a BOM and is left alone.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree

from ..config import HOUSE_CLERK_BASE
from ..filings import Filing, Manifest
from .session import ThrottledSession

INDEX_FIELDS = ("Prefix", "Last", "First", "Suffix", "FilingType", "StateDst", "Year", "FilingDate", "DocID")

# FilingType letter -> report_type in the shared filings table. Letters not
# listed keep the letter itself until the Clerk's legend is confirmed
# (DESIGN-HOUSE §7 Q1).
REPORT_TYPES = {
    "P": "PTR",
    "A": "annual",
    "X": "extension",
}


def index_url(year: int) -> str:
    return f"{HOUSE_CLERK_BASE}/financial-pdfs/{year}FD.zip"


def document_url(year: int, filing_type: str, doc_id: str) -> str:
    folder = "ptr-pdfs" if filing_type == "P" else "financial-pdfs"
    return f"{HOUSE_CLERK_BASE}/{folder}/{year}/{doc_id}.pdf"


def classify_docid(doc_id: str) -> str:
    """'efiled' for 8-digit DocIDs, 'paper' otherwise (DESIGN-HOUSE §2 table).

    Provisional: from the 2026 index only. ``pdffonts`` is the tiebreak at
    download time.
    """
    if not doc_id.isdigit():
        raise ValueError(f"non-numeric DocID: {doc_id!r}")
    return "efiled" if len(doc_id) == 8 else "paper"


@dataclass(frozen=True)
class IndexRow:
    prefix: str
    last: str
    first: str
    suffix: str
    filing_type: str
    state_dst: str
    year: int
    filing_date: str  # ISO 8601
    doc_id: str

    @property
    def filing_id(self) -> str:
        return f"house-{self.doc_id}"

    @property
    def filer_name(self) -> str:
        parts = [self.prefix, self.first, self.last, self.suffix]
        return " ".join(p for p in parts if p)

    def to_filing(self, first_seen_at: str) -> Filing:
        return Filing(
            filing_id=self.filing_id,
            chamber="house",
            filer_name=self.filer_name,
            report_type=REPORT_TYPES.get(self.filing_type, self.filing_type),
            filing_date=self.filing_date,
            source_url=document_url(self.year, self.filing_type, self.doc_id),
            first_seen_at=first_seen_at,
            filing_type=self.filing_type,
            docid_prefix_class=classify_docid(self.doc_id),
        )


_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def normalize_date(raw: str) -> str:
    """'4/15/2026' -> '2026-04-15'. Blank stays blank (seen on type-W rows
    in every year probed); anything else raises."""
    raw = raw.strip()
    if not raw:
        return ""
    m = _DATE_RE.match(raw)
    if not m:
        raise ValueError(f"unrecognised FilingDate: {raw!r}")
    month, day, year = (int(g) for g in m.groups())
    return date(year, month, day).isoformat()


def parse_index_xml(data: bytes) -> list[IndexRow]:
    root = ElementTree.fromstring(data.lstrip(b"\xef\xbb\xbf"))
    if root.tag != "FinancialDisclosure":
        raise ValueError(f"unexpected root element: {root.tag}")
    rows: list[IndexRow] = []
    for member in root.iter("Member"):
        text = {f: (member.findtext(f) or "").strip() for f in INDEX_FIELDS}
        rows.append(
            IndexRow(
                prefix=text["Prefix"],
                last=text["Last"],
                first=text["First"],
                suffix=text["Suffix"],
                filing_type=text["FilingType"],
                state_dst=text["StateDst"],
                year=int(text["Year"]),
                filing_date=normalize_date(text["FilingDate"]),
                doc_id=text["DocID"],
            )
        )
    return rows


def parse_index_zip(data: bytes, year: int) -> list[IndexRow]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = f"{year}FD.xml"
        if name not in zf.namelist():
            raise ValueError(f"{name} not in index zip; members: {zf.namelist()}")
        return parse_index_xml(zf.read(name))


def fetch_index(session: ThrottledSession, year: int) -> list[IndexRow]:
    return parse_index_zip(session.get(index_url(year)), year)


def new_filings(
    rows: Iterable[IndexRow],
    manifest: Manifest,
    filing_types: frozenset[str] | None = frozenset({"P"}),
) -> list[IndexRow]:
    """Index rows in scope whose DocID the manifest has not seen.

    ``filing_types=None`` means every type. Duplicate DocIDs within the
    index collapse to their first occurrence.
    """
    seen: set[str] = set()
    out: list[IndexRow] = []
    for row in rows:
        if filing_types is not None and row.filing_type not in filing_types:
            continue
        if row.filing_id in manifest or row.doc_id in seen:
            continue
        seen.add(row.doc_id)
        out.append(row)
    return out
