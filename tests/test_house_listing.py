import io
import zipfile

import pytest

from gov_trades.filings import Filing
from gov_trades.house.listing import (
    IndexRow,
    classify_docid,
    document_url,
    index_url,
    new_filings,
    normalize_date,
    parse_index_xml,
    parse_index_zip,
)

XML = b"""\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>
<FinancialDisclosure>
  <Member>
    <Prefix />
    <Last>Aaron</Last>
    <First>Richard</First>
    <Suffix />
    <FilingType>W</FilingType>
    <StateDst>MI04</StateDst>
    <Year>2026</Year>
    <FilingDate>4/15/2026</FilingDate>
    <DocID>8068</DocID>
  </Member>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Pelosi</Last>
    <First>Nancy</First>
    <Suffix />
    <FilingType>P</FilingType>
    <StateDst>CA11</StateDst>
    <Year>2026</Year>
    <FilingDate>1/6/2026</FilingDate>
    <DocID>20031234</DocID>
  </Member>
  <Member>
    <Prefix />
    <Last>Smith</Last>
    <First>John</First>
    <Suffix>Jr.</Suffix>
    <FilingType>P</FilingType>
    <StateDst>TX01</StateDst>
    <Year>2026</Year>
    <FilingDate>12/31/2026</FilingDate>
    <DocID>9115816</DocID>
  </Member>
  <Member>
    <Prefix />
    <Last>Smith</Last>
    <First>John</First>
    <Suffix>Jr.</Suffix>
    <FilingType>P</FilingType>
    <StateDst>TX01</StateDst>
    <Year>2026</Year>
    <FilingDate>12/31/2026</FilingDate>
    <DocID>9115816</DocID>
  </Member>
</FinancialDisclosure>
"""


def _zip(year=2026, xml=XML, with_xml=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{year}FD.txt", "Prefix\tLast\r\n")
        if with_xml:
            zf.writestr(f"{year}FD.xml", xml)
    return buf.getvalue()


def test_parse_xml_rows():
    rows = parse_index_xml(XML)
    assert len(rows) == 4
    assert rows[0] == IndexRow("", "Aaron", "Richard", "", "W", "MI04", 2026, "2026-04-15", "8068")
    assert rows[1].filer_name == "Hon. Nancy Pelosi"
    assert rows[2].filer_name == "John Smith Jr."
    assert rows[1].filing_id == "house-20031234"


def test_parse_zip_requires_xml():
    assert len(parse_index_zip(_zip(), 2026)) == 4
    with pytest.raises(ValueError, match="2026FD.xml"):
        parse_index_zip(_zip(with_xml=False), 2026)


def test_wrong_root_rejected():
    with pytest.raises(ValueError, match="root"):
        parse_index_xml(b"<Other/>")


@pytest.mark.parametrize(
    "raw,iso",
    [("4/15/2026", "2026-04-15"), ("12/1/2008", "2008-12-01"), (" 1/2/2020 ", "2020-01-02"), ("", ""), ("  ", "")],
)
def test_normalize_date(raw, iso):
    assert normalize_date(raw) == iso


@pytest.mark.parametrize("raw", ["2026-04-15", "4/15/26", "13/1/2026", "n/a"])
def test_normalize_date_rejects(raw):
    with pytest.raises(ValueError):
        normalize_date(raw)


@pytest.mark.parametrize(
    "doc_id,cls",
    [("20031234", "efiled"), ("10078673", "efiled"), ("30028196", "efiled"), ("9115816", "paper"), ("8068", "paper")],
)
def test_classify_docid(doc_id, cls):
    assert classify_docid(doc_id) == cls


def test_classify_docid_non_numeric():
    with pytest.raises(ValueError):
        classify_docid("abc")


def test_urls():
    assert index_url(2026).endswith("/financial-pdfs/2026FD.zip")
    assert document_url(2026, "P", "20031234").endswith("/ptr-pdfs/2026/20031234.pdf")
    assert document_url(2026, "A", "10078673").endswith("/financial-pdfs/2026/10078673.pdf")


def test_new_filings_filters_dedupes_and_diffs():
    rows = parse_index_xml(XML)
    manifest = {"house-20031234": Filing(filing_id="house-20031234", chamber="house")}
    fresh = new_filings(rows, manifest)
    assert [r.doc_id for r in fresh] == ["9115816"]  # W dropped, Pelosi known, duplicate collapsed
    assert [r.doc_id for r in new_filings(rows, {}, None)] == ["8068", "20031234", "9115816"]
    assert new_filings(rows, {}, frozenset({"W", "P"}))[0].doc_id == "8068"


def test_to_filing():
    row = parse_index_xml(XML)[1]
    f = row.to_filing("2026-09-20T00:00:00Z")
    assert f.filing_id == "house-20031234"
    assert f.chamber == "house"
    assert f.report_type == "PTR"
    assert f.filing_type == "P"
    assert f.docid_prefix_class == "efiled"
    assert f.filing_date == "2026-01-06"
    assert f.source_url.endswith("/ptr-pdfs/2026/20031234.pdf")
    assert f.first_seen_at == "2026-09-20T00:00:00Z"
    assert parse_index_xml(XML)[0].to_filing("t").report_type == "W"
