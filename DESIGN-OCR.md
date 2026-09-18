# Extraction Tier — Design (shared by both chambers)

How scanned and e-filed disclosure pages become structured transaction rows.
This tier is chamber-independent: the Senate and House pipelines hand it page
images and a document class, and it hands back per-filing JSON. Chamber-specific
collection is in `DESIGN-SENATE.md` and `DESIGN-HOUSE.md`.

**Status:** design, 2026-09-18. Supersedes the GitHub Models plan, which died
when GitHub retired that product on 2026-07-30.

---

## 1. Decision summary

| Role | Backend | Cost | Why |
|---|---|---|---|
| Primary scanner, e-filed docs | Gemma 4 26B A4B via Gemini API | free tier | Clean renders; small quality gap; spreads load |
| Primary scanner, scanned docs | Gemma 4 31B via Gemini API | free tier | Best free document-understanding scores; the hard 30% |
| Adjudicator | the *other* Gemma 4 model | free tier | Disagreement signal without a paid model |
| Overflow / insurance | Self-hosted Gemma 4 on Colab Pro via Colab CLI | Pro compute units | Full control of image-token budget; survives free-tier changes |
| Paid fallback | Vertex AI managed Gemma 4 26B A4B (MaaS) | per token | Same model, paid SLA; guards against free tier disappearing |
| Manual review | human, via a flagged queue in the manifest | time | Two-model disagreement or schema failure |

The output **schema** is fixed across every backend. The **prompt** is not: each
backend gets its own prompt file, tuned to what that model responds to.

---

## 2. Why this shape

- **GitHub Models is gone.** Retired for all customers 2026-07-30. Its
  replacement (Copilot CLI/SDK, billed in AI credits) is an agent runtime, not a
  bare inference endpoint, and needs a personal PAT in a personal repo. Not a fit.
- **Gemma 4 on the Gemini API is free with image input.** Both `gemma-4-31b-it`
  and `gemma-4-26b-a4b-it` are served; the pricing page lists both as free of
  charge with no paid tier. Rate limits observed in AI Studio on 2026-09-18
  (read off the chart; confirm exact values from the tooltip):

  | Limit (per model) | 26B A4B | 31B |
  |---|---|---|
  | Requests / minute | ~30 | ~30 |
  | Input tokens / minute | ~15–16K | ~15–16K |
  | Requests / day | ~14K | ~14K |

  Tokens-per-minute is the binding constraint, not requests. At a 1120 visual
  token budget a one-page filing is ~1.5–2K input tokens, so ~8–10 single-page
  docs/min per model, ~20M input tokens/day per model.
- **The two models appear to have separate pools.** This is an interpretation
  of the AI Studio UI (a per-model limit chart), not documented. **Test it in
  the first real run** by driving both models concurrently and watching for
  429s. If pools are shared, halve the throughput figures above.
- **Model choice.** Gemma 4 model card: 31B dense (30.7B params) vs 26B A4B MoE
  (25.2B total, 3.8B active). OmniDocBench 1.5: 0.131 vs 0.149 (lower is
  better). Both accept image input with the same visual token budget ladder
  (70/140/280/560/1120). On the API both are free, so the 31B takes the hard
  documents; the 26B is the Vertex-managed model, so it is the upgrade path.
- **Two-model adjudication** mirrors the House pilot pattern (12B first pass,
  31B verifier, Claude Sonnet tie-break) but with no paid tier in the loop.
- **Colab is overflow, not primary.** Colab CLI (launched 2026-06-05) provisions
  T4/L4/A100/H100 headlessly via `colab exec`, keeps the VM alive after
  disconnect, and moves artifacts with `upload`/`download`. It runs from a
  laptop against the Pro subscription; driving it from Actions is unconfirmed
  and not planned. The MoE decodes faster than the 31B at the same VRAM, so it
  burns fewer units per page there.

---

## 3. Routing rule

```
classify(doc)  ->  scanned | efiled
    scanned  ->  31B first
    efiled   ->  26B first
result  ->  validate(schema) and sanity(row count vs header, dates plausible)
    pass     ->  accept, record model + prompt version in manifest
    fail     ->  send to the other model
        agree (within tolerance)  ->  accept the second result, note escalation
        disagree / fail again     ->  flag for manual review, keep both outputs
```

Tolerance and "agree" are defined on the calibration set (§6), not guessed.
Every accepted row records which model and prompt version produced it, so the
dataset is auditable and re-runnable per backend.

---

## 4. Interface

```python
class Extractor(Protocol):
    name: str                      # "gemini:gemma-4-31b-it", "colab:gemma-4-26b-a4b", ...
    def extract(self, pages: list[bytes], doc_class: str, prompt: str) -> ExtractionResult: ...
```

`ExtractionResult` is the fixed schema (§5). Backends:

- `gemini.py` — `google-genai` SDK, API key from env. Handles 429 with backoff
  and a token-bucket that respects the observed TPM. One request per document
  (all pages), matching the House pilot.
- `colab.py` — OpenAI-compatible client against a `llama-server` or vLLM on a
  Colab runtime, base URL from env. Serving recipe from the House pilot:
  `--image-min-tokens 1120 --image-max-tokens 1120`, greedy, thinking off.
- `vertex.py` — same as `gemini.py` against the Vertex MaaS endpoint with ADC.
  Built only if needed.

Prompts live in `prompts/<backend>/<doc_class>.md`, versioned; the manifest
records the prompt version per row.

---

## 5. Output schema (fixed)

Per document:

```
doc_id, chamber, filer, doc_class (scanned|efiled), n_pages,
model, prompt_version, extracted_at,
n_transactions_header, transactions: [
  { owner, asset_name, ticker, transaction_type, transaction_date,
    notification_date, amount_range, description, page }
],
legibility, escalated (bool), review_flag (bool), review_reason
```

This is the House pilot's direct-to-JSON shape, generalised. Verbatim
transcription (the original Senate plan) is dropped as the primary product;
if wanted it is a second prompt against the same pages, not a prerequisite.
Normalisation (owner/type/amount/ticker cleaning, date plausibility) happens
downstream in ingest, not here: the extractor stays faithful to the page.

---

## 6. Calibration set

Before choosing routing thresholds or trusting any throughput figure:

- ~20 hand-checked documents per chamber spanning e-filed single-page,
  e-filed multi-page, scanned checkbox form, scanned handwritten, and one
  bond/muni-heavy filing (the ticker-phantom case).
- Run every backend against it. Record per-document agreement, per-field
  error, wall time, tokens, and 429s.
- Publish the table in the README. It is the evidence behind the routing rule
  and the most useful thing a reader of this repo can see.

---

## 7. Things to verify in the first real run

1. **Separate vs shared rate-limit pools** across the two Gemma models.
2. **How the Gemini API counts image tokens for Gemma 4** — at the requested
   visual budget, a fixed per-image figure, or otherwise. The throughput math
   assumes the budget. Whether the budget is even settable through the API is
   itself unconfirmed.
3. **Exact rate-limit values** from the AI Studio tooltip.
4. **Free-tier data-use terms.** Prompts on the free tier may be used for
   product improvement. The inputs are public records, so this is tolerable,
   but confirm nothing in the terms conflicts with `DATA-TERMS.md`.
5. **Multi-page documents in one request** stay under the per-request and
   per-minute token caps; long annual reports may need per-page requests.
6. **Colab CLI auth and Pro entitlement** from a laptop, and L4 unit burn.

---

## 8. Sizing (provisional, to be replaced by calibration numbers)

| Corpus | Docs | On the free tier |
|---|---|---|
| House PTRs, 2013–present | ~8,400 | 1–2 days at ~2K tokens/doc, one model |
| House annual reports | unsized | multi-page; unknown |
| Senate, 2012–present | unsized | scanned share unknown |

All prior timing estimates (42 s/doc median on a single 8 GB consumer GPU
through a flapping tunnel) are obsolete. Re-measure on the API and on an L4.
