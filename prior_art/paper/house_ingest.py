"""Parse House PTR data into PoliticianTrade objects.

Two feeds land here, both keyed to the same disclosures-clerk.house.gov PTRs:

  1. TattooedHead/house-stock-watcher-data — clean rows its pdfplumber parser
     recovered (old-API-compatible JSON).
  2. Our vision-model extractions of the ~21k rows that parser *jammed* on
     (`experiments/house_extraction/extract_jam*.json`, produced by
     jam_backfill.py / gemma_extract.py).

Pure functions. The script (`scripts/ingest_house.py`) handles I/O — network
fetch, file reads, DB writes. Everything here is testable with synthetic input.

The load-bearing piece is the ticker post-filter (`clean_house_ticker`): House
PTRs print the real ticker in parentheses — "Common Stock (VZ)" — and the
asset-TYPE in brackets — "[ST]", "[GS]", "[OT]". The 12B extractor routinely
mis-reads the bracket code as the ticker (100 phantom "GS" Goldman-Sachs rows
in the 2026 jam-backfill pilot were really the [GS] government-security code).
Without this filter those phantoms would poison any mirror-the-buys backtest.
"""

from __future__ import annotations

import re

from paper.models import PoliticianTrade

# Rows we extract directly from Clerk PTR PDFs (the ~21k the TattooedHead
# pdfplumber parser jammed on). Distinct source tag from the clean mirror feed.
SOURCE_EXTRACT = "house-clerk-extract"

# disclosures-clerk.house.gov canonical PTR PDF URL — also our ptr_link.
_PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"

# House PTR owner codes → the vocabulary used across sources. Blank/None = the
# filer themselves.
_OWNER_MAP = {"SP": "Spouse", "JT": "Joint", "DC": "Child"}

# House PTR transaction-type codes → normalized direction.
_TYPE_MAP = {
    "P": "buy",
    "S": "sell",
    "S (PARTIAL)": "sell",
    "E": "exchange",
}

# Best-effort human labels for the asset-type bracket codes (descriptive only;
# unknown codes fall through to the raw two-letter code).
_ASSET_TYPE_LABELS = {
    "ST": "Stock",
    "OP": "Stock Option",
    "PS": "Preferred Stock",
    "RS": "Restricted Stock Unit",
    "MF": "Mutual Fund",
    "ET": "Exchange-Traded Fund",
    "CT": "Cryptocurrency",
    "GS": "Government Security",
    "CS": "Corporate Security",
    "HN": "Hedge Fund/Note",
    "VA": "Variable Annuity",
    "OI": "Other",
    "OT": "Other",
}

_BUCKET_AMOUNT_RE = re.compile(r"^\$([\d,]+)\s*-\s*\$([\d,]+)$")
_EXACT_AMOUNT_RE = re.compile(r"^\$([\d,]+(?:\.\d+)?)$")
_OVER_AMOUNT_RE = re.compile(r"[Oo]ver\s+\$([\d,]+(?:\.\d+)?)")
_TRAILING_CODE_RE = re.compile(r"\s*\[[A-Z]{2}\]\s*$")
_TITLE_PREFIX_RE = re.compile(r"^(Hon\.|Mr\.|Mrs\.|Ms\.|Dr\.|Rep\.|Sen\.)\s+")

# A real ticker as printed on a House PTR: parenthesized, all-caps, may carry
# a class dot or preferred '$' (e.g. (BRK.B), (CADE$A)). 1-6 chars.
_PAREN_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.$]{0,5})\)")

# An asset-TYPE code as printed on a House PTR: two upper-case letters in
# square brackets, e.g. [ST] Stock, [GS] Government Security, [OT] Other,
# [CS] Corporate Security, [OI] Other Income, [HN] Hedge/Note, [PS] Preferred.
_BRACKET_CODE_RE = re.compile(r"\[([A-Z]{2})\]")

# The asset-type vocabulary, derived empirically from every [XX] code seen
# bracketed across the extraction corpus (experiments/house_extraction/
# extract_jam*.json). Used for the second, weaker filter rule: when the
# extractor emits one of these codes as a ticker but DROPS the bracket from
# the asset name (e.g. "US Treasury Bill" with ticker "GS"), the precise
# in-row bracket test can't catch it. GS in particular collides with a real
# ticker (Goldman Sachs), so an unfiltered phantom would price as Goldman.
# The paren-guard keeps genuine equity buys safe: a real buy prints "(GS)".
KNOWN_ASSET_TYPE_CODES = frozenset(
    {"CS", "CT", "GS", "HN", "OI", "OT", "PS", "RS", "ST", "VA"}
)

_NULL_TICKERS = {"", "--", "N/A", "N/A."}


def clean_house_ticker(ticker: str | None, asset_name: str | None) -> str | None:
    """Return the true equity ticker for a House PTR row, or None.

    Rules, in order:
      1. Normalize the model's ticker; treat placeholders as None.
      2. If that ticker equals a [XX] asset-type code literally present in
         asset_name, it is a mis-read bracket code, not a ticker. Try to
         recover a real (XXX) parenthesized ticker from asset_name; else None.
      3. Else if the ticker is a known asset-type code AND does not appear as a
         parenthesized (XXX) ticker in asset_name, it is a bracket code whose
         bracket the extractor dropped (e.g. "US Treasury Bill" -> "GS"). Null
         it. A genuine equity buy prints "(GS)", so it is kept by the guard.
      4. Otherwise keep the (normalized) model ticker.

    Non-equity instruments (bonds, notes, structured products) legitimately
    have no ticker and return None — they are excluded from the equity mirror
    anyway.
    """
    tk = (ticker or "").strip().upper()
    if tk in _NULL_TICKERS:
        tk = None
    if tk is None:
        return None

    brackets = set(_BRACKET_CODE_RE.findall(asset_name or ""))
    parens = set(_PAREN_TICKER_RE.findall(asset_name or ""))

    if tk in brackets:
        # Rule 2 — false ticker is the bracketed asset-type code. Try to
        # recover a genuine parenthesized ticker before giving up.
        for cand in _PAREN_TICKER_RE.findall(asset_name or ""):
            if cand not in brackets:
                return cand
        return None

    if tk in KNOWN_ASSET_TYPE_CODES and tk not in parens:
        # Rule 3 — bracket code with the bracket dropped, and no parenthesized
        # ticker vouches for it. Treat as asset-type code, not a ticker.
        return None

    return tk


def _normalize_date(d: str | None) -> str | None:
    """MM/DD/YYYY -> ISO; ISO passes through; else None. Extraction dates are
    already ISO, but the Clerk forms occasionally surface US-format dates."""
    if not d:
        return None
    d = d.strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", d)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    return None


def parse_house_amount(raw: str | None) -> tuple[float | None, float | None]:
    """Parse a House amount string to (low, high) USD.

    Handles the three shapes seen in PTRs:
      - standard bucket:  '$1,001 - $15,000'       -> (1001, 15000)
      - open-ended:       'Spouse/DC Over $1,000,000' -> (1000000, None)
      - exact value:      '$318.74'                 -> (318.74, 318.74)
    Returns (None, None) for 'Unknown', empty, or unparseable input.
    """
    if not raw:
        return (None, None)
    s = raw.strip()
    if s.lower() == "unknown":
        return (None, None)
    m = _BUCKET_AMOUNT_RE.match(s)
    if m:
        return (float(m.group(1).replace(",", "")), float(m.group(2).replace(",", "")))
    m = _EXACT_AMOUNT_RE.match(s)
    if m:
        v = float(m.group(1).replace(",", ""))
        return (v, v)
    m = _OVER_AMOUNT_RE.search(s)
    if m:
        return (float(m.group(1).replace(",", "")), None)
    return (None, None)


def _split_asset_name(asset_name: str | None) -> tuple[str | None, str | None]:
    """Return (clean_description, asset_type_code) from a raw asset_name.

    'Verizon ... Common Stock (VZ) [ST]\\nFILING STATUS: New'
      -> ('Verizon ... Common Stock (VZ)', 'ST')
    """
    if not asset_name:
        return (None, None)
    first = asset_name.split("\n", 1)[0].strip()
    code = None
    m = re.search(r"\[([A-Z]{2})\]\s*$", first)
    if m:
        code = m.group(1)
        first = _TRAILING_CODE_RE.sub("", first).strip()
    return (first or None, code)


def _asset_type_label(code: str | None) -> str | None:
    if not code:
        return None
    return _ASSET_TYPE_LABELS.get(code, code)


def _normalize_owner(raw: str | None) -> str:
    if not raw:
        return "Self"
    return _OWNER_MAP.get(raw.strip().upper(), raw.strip())


def _normalize_type(raw: str | None) -> str:
    if not raw:
        return "other"
    return _TYPE_MAP.get(raw.strip().upper(), "other")


def _clean_filer(name: str | None) -> str:
    if not name:
        return "Unknown"
    return _TITLE_PREFIX_RE.sub("", name.strip()).strip() or "Unknown"


def _clean_comment(desc: str | None) -> str | None:
    """Drop 'FILING STATUS: ...' boilerplate lines; keep SUBHOLDING/DESCRIPTION
    context. Returns None if nothing substantive remains."""
    if not desc:
        return None
    kept = [
        ln for ln in desc.split("\n")
        if not ln.strip().upper().startswith("FILING STATUS")
    ]
    return "\n".join(kept).strip() or None


def parse_extraction(
    data: dict, source: str = SOURCE_EXTRACT, fetched_at: str | None = None
) -> list[PoliticianTrade]:
    """Convert one vision-model extraction file (see gemma_extract.py /
    jam_backfill.py output shape) to PoliticianTrade rows.

    `data` has top-level 'year' and 'docs'; each doc has 'doc_id', 'filer',
    and 'transactions'. ptr_link is the canonical Clerk PDF URL; ptr_row_idx
    is a per-doc 0-based index over *kept* rows (source order) so re-ingestion
    is idempotent under the UNIQUE constraint. Rows with no parseable
    transaction_date are skipped. The ticker post-filter is applied here.
    """
    file_year = str(data.get("year") or "").strip()
    trades: list[PoliticianTrade] = []

    for doc in data.get("docs", []):
        doc_id = str(doc.get("doc_id") or "").strip()
        if not doc_id:
            continue
        filer = _clean_filer(doc.get("filer"))
        idx = 0

        for t in doc.get("transactions", []):
            tx_date = _normalize_date(t.get("transaction_date"))
            if not tx_date:
                continue

            year = file_year or tx_date[:4]
            asset_name = t.get("asset_name")
            description, code = _split_asset_name(asset_name)
            amount_raw = t.get("amount_range")
            amount_low, amount_high = parse_house_amount(amount_raw)
            raw_type = t.get("transaction_type")

            trades.append(
                PoliticianTrade(
                    source=source,
                    ptr_link=_PDF_URL.format(year=year, doc_id=doc_id),
                    ptr_row_idx=idx,
                    transaction_date=tx_date,
                    disclosure_date=_normalize_date(t.get("notification_date")),
                    filer_name=filer,
                    chamber="House",
                    party=None,
                    state=None,
                    owner=_normalize_owner(t.get("owner")),
                    ticker=clean_house_ticker(t.get("ticker"), asset_name),
                    asset_description=description,
                    asset_type=_asset_type_label(code),
                    transaction_type=_normalize_type(raw_type),
                    raw_type=raw_type,
                    amount_raw=amount_raw,
                    amount_low=amount_low,
                    amount_high=amount_high,
                    comment=_clean_comment(t.get("description")),
                    fetched_at=fetched_at,
                )
            )
            idx += 1

    return trades
