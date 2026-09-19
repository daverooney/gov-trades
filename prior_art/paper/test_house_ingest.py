"""Tests for House PTR parsing — the load-bearing ticker post-filter.

Cases mirror the real failure modes seen in the 2026 jam-backfill pilot
(experiments/house_extraction/extract_jam2026_*.json).
"""

from __future__ import annotations

import pytest

from paper.house_ingest import (
    SOURCE_EXTRACT,
    clean_house_ticker,
    parse_extraction,
    parse_house_amount,
)


class TestCleanHouseTicker:
    def test_bracket_code_gs_is_stripped(self):
        # The 100-row phantom: [GS] government security read as Goldman ticker.
        assert (
            clean_house_ticker(
                "GS", "California St Go Call 12/1/27 4% due 12/1/47 [GS]"
            )
            is None
        )

    @pytest.mark.parametrize(
        "code,name",
        [
            ("OT", "Apollo Debt Solutions BDC Class S [OT]"),
            ("CS", "JP Morgan 3-year auto callable buffer note [CS]"),
            ("OI", "Some Other Income instrument [OI]"),
            ("ST", "Weird note tagged stock [ST]"),
        ],
    )
    def test_all_bracket_codes_stripped(self, code, name):
        assert clean_house_ticker(code, name) is None

    def test_real_ticker_in_parens_survives(self):
        # Genuine equity: ticker in (VZ), asset type in [ST]. VZ != ST.
        assert clean_house_ticker("VZ", "Verizon Communications Inc. Common Stock (VZ) [ST]") == "VZ"

    def test_real_gs_buy_survives(self):
        # A genuine Goldman Sachs stock buy: prints (GS) [ST]; [GS] is absent,
        # so the ticker must NOT be stripped. This is the robustness property.
        assert clean_house_ticker("GS", "Goldman Sachs Group Inc (GS) [ST]") == "GS"

    def test_recover_paren_ticker_when_model_returned_bracket_code(self):
        # Model wrongly returned the bracket code, but a real (XXX) is present.
        assert (
            clean_house_ticker("GS", "Some Equity Common Stock (ABC) [GS]") == "ABC"
        )

    def test_class_share_ticker_preserved(self):
        assert clean_house_ticker("BRK.B", "Berkshire Hathaway Class B (BRK.B) [ST]") == "BRK.B"

    def test_null_placeholders_become_none(self):
        for ph in ("", "--", "N/A", None):
            assert clean_house_ticker(ph, "Some bond [GS]") is None

    def test_whitespace_and_case_normalized(self):
        assert clean_house_ticker("  vz ", "Verizon (VZ) [ST]") == "VZ"

    def test_no_asset_name_passes_ticker_through(self):
        assert clean_house_ticker("AAPL", None) == "AAPL"
        assert clean_house_ticker("AAPL", "") == "AAPL"

    def test_dropped_bracket_gs_treasury_bill_stripped(self):
        # The residual leak: model emitted GS but dropped the [GS] bracket.
        # GS collides with a real ticker, so it must still be caught.
        assert clean_house_ticker("GS", "US Treasury Bill") is None

    def test_dropped_bracket_code_no_parens_stripped(self):
        # OT is in the asset-type vocabulary → stripped when unparenthesized.
        assert clean_house_ticker("OT", "Regeneration VC Fund 1 LP") is None

    def test_two_letter_non_vocab_code_survives(self):
        # OL is NOT in the asset-type vocabulary → left alone (rule 4).
        assert clean_house_ticker("OL", "Solace Health") == "OL"

    def test_real_gs_buy_without_bracket_survives_via_parens(self):
        # Rule-3 guard: a real Goldman buy prints (GS) even if [ST] is dropped.
        assert clean_house_ticker("GS", "Goldman Sachs Group Inc (GS)") == "GS"

    def test_non_code_two_letter_ticker_survives(self):
        # A two-letter ticker that is NOT an asset-type code is untouched.
        assert clean_house_ticker("BA", "Boeing Co") == "BA"


class TestParseHouseAmount:
    def test_standard_bucket(self):
        assert parse_house_amount("$1,001 - $15,000") == (1001.0, 15000.0)

    def test_open_ended_spouse_dc(self):
        assert parse_house_amount("Spouse/DC Over $1,000,000") == (1000000.0, None)

    def test_exact_value(self):
        assert parse_house_amount("$318.74") == (318.74, 318.74)

    def test_unknown_and_empty(self):
        assert parse_house_amount("Unknown") == (None, None)
        assert parse_house_amount("") == (None, None)
        assert parse_house_amount(None) == (None, None)


class TestParseExtraction:
    def _doc(self, **tx):
        base = {
            "owner": None,
            "asset_name": "Verizon Communications Inc. Common Stock (VZ) [ST]\nFILING STATUS: New",
            "ticker": "VZ",
            "transaction_type": "P",
            "transaction_date": "2026-03-09",
            "notification_date": "2026-03-11",
            "amount_range": "$1,001 - $15,000",
            "description": "FILING STATUS: New",
        }
        base.update(tx)
        return {"doc_id": "20033500", "filer": "Hon. Pete Sessions", "transactions": [base]}

    def _parse_one(self, **tx):
        data = {"year": "2026", "docs": [self._doc(**tx)]}
        rows = parse_extraction(data)
        assert len(rows) == 1
        return rows[0]

    def test_core_field_mapping(self):
        r = self._parse_one()
        assert r.source == SOURCE_EXTRACT
        assert r.chamber == "House"
        assert r.ptr_link == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033500.pdf"
        assert r.ptr_row_idx == 0
        assert r.filer_name == "Pete Sessions"  # 'Hon.' stripped
        assert r.transaction_date == "2026-03-09"
        assert r.disclosure_date == "2026-03-11"
        assert r.ticker == "VZ"
        assert r.asset_description == "Verizon Communications Inc. Common Stock (VZ)"
        assert r.asset_type == "Stock"
        assert r.transaction_type == "buy"
        assert r.raw_type == "P"
        assert r.owner == "Self"
        assert (r.amount_low, r.amount_high) == (1001.0, 15000.0)
        assert r.comment is None  # pure boilerplate dropped

    def test_owner_and_type_codes(self):
        assert self._parse_one(owner="SP").owner == "Spouse"
        assert self._parse_one(owner="JT").owner == "Joint"
        assert self._parse_one(owner="DC").owner == "Child"
        assert self._parse_one(transaction_type="S").transaction_type == "sell"
        assert self._parse_one(transaction_type="S (partial)").transaction_type == "sell"
        assert self._parse_one(transaction_type="E").transaction_type == "exchange"

    def test_phantom_ticker_filtered_in_pipeline(self):
        r = self._parse_one(
            ticker="GS",
            asset_name="US Treasury Bill",
            amount_range="Spouse/DC Over $1,000,000",
        )
        assert r.ticker is None
        assert (r.amount_low, r.amount_high) == (1000000.0, None)

    def test_comment_keeps_substantive_context(self):
        r = self._parse_one(
            description="FILING STATUS: New\nSUBHOLDING OF: Fidelity Trust\nDESCRIPTION: Structured Note"
        )
        assert "Fidelity Trust" in r.comment
        assert "FILING STATUS" not in r.comment

    def test_undated_row_skipped(self):
        data = {"year": "2026", "docs": [self._doc(transaction_date=None)]}
        assert parse_extraction(data) == []

    def test_row_idx_increments_over_kept_rows(self):
        doc = {
            "doc_id": "999",
            "filer": "Hon. A B",
            "transactions": [
                {"transaction_date": "2026-01-01", "ticker": "AAPL", "asset_name": "Apple (AAPL) [ST]", "transaction_type": "P", "amount_range": "$1,001 - $15,000"},
                {"transaction_date": None, "ticker": "X", "asset_name": "skip", "transaction_type": "P", "amount_range": ""},
                {"transaction_date": "2026-01-02", "ticker": "MSFT", "asset_name": "Microsoft (MSFT) [ST]", "transaction_type": "S", "amount_range": "$1,001 - $15,000"},
            ],
        }
        rows = parse_extraction({"year": "2026", "docs": [doc]})
        assert [r.ptr_row_idx for r in rows] == [0, 1]
        assert [r.ticker for r in rows] == ["AAPL", "MSFT"]
