# House PTR direct-to-JSON prompt, v1

Lifted verbatim from `prior_art/paper/gemma_extract.py` (`PROMPT`), the prompt
validated on 114 e-filed 2026 PTRs plus the gauntlet docs in the paper repo.
`%s` is the DocID. Serving notes (thinking off, greedy decode, checkbox amounts
come back as a letter that ingest maps to a bucket) are in DESIGN-OCR §4 and
DESIGN-HOUSE §4. Known weakness: the `confidence` field carries no signal.

```
These images are the pages of one US House Periodic Transaction Report (PTR). Extract EVERY transaction row into exactly this JSON shape and output ONLY the JSON, no prose, no markdown fences:

{
  "doc_id": "%s",
  "filer": "<name as printed>",
  "n_transactions": <int>,
  "transactions": [
    {
      "owner": "<SP/DC/JT or null if the owner column is blank>",
      "asset_name": "<full asset description as printed>",
      "ticker": "<symbol in parentheses, null if none>",
      "transaction_type": "<P | S | S (partial) | E as printed or as checked in the type checkboxes>",
      "transaction_date": "YYYY-MM-DD",
      "notification_date": "YYYY-MM-DD",
      "amount_range": "<as printed, or the checked amount-column range on checkbox forms>",
      "description": "<filing status / subholding / cap-gains checkbox state; null if none>"
    }
  ],
  "legibility": "<one line>",
  "confidence": "<high | medium | low>"
}

Transcribe exactly what is printed. Do not guess missing values; use null. A row may continue across a page boundary - do not double-count it.
```
