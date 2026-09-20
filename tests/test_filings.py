from gov_trades.filings import COLUMNS, Filing, read_manifest, write_manifest


def test_missing_file_is_empty(tmp_path):
    assert read_manifest(tmp_path / "nope.csv") == {}


def test_round_trip_sorted_fixed_columns(tmp_path):
    path = tmp_path / "filings.csv"
    m = {
        "house-2": Filing(filing_id="house-2", chamber="house", filer_name="B"),
        "house-1": Filing(filing_id="house-1", chamber="house", filer_name="A", filing_type="P"),
    }
    write_manifest(path, m)
    text = path.read_text()
    assert text.splitlines()[0] == ",".join(COLUMNS)
    assert text.index("house-1") < text.index("house-2")
    assert read_manifest(path) == m
    assert not list(tmp_path.glob("*.part"))


def test_from_row_ignores_unknown_columns():
    f = Filing.from_row({"filing_id": "house-9", "chamber": "house", "future_col": "x"})
    assert f.filing_id == "house-9"
    assert f.review_flag == "0"  # absent column falls back to the default
