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
| Primary scanner, e-filed docs | Gemma 4 26B A4B via Gemini API, PDF in, schema out | free tier | Fixed 258 tokens/page is enough for clean renders; ~14K docs/day |
| Primary scanner, scanned docs | Gemini 3.7 Flash via Gemini API (free tier, else paid Batch) | free / ~$0.375 per 1M in | Reads images at 1,102 tokens; hosted Gemma cannot exceed 258 |
| Adjudicator, e-filed | Gemma 4 31B via Gemini API | free tier | Second free model, separate pool (unconfirmed) |
| Adjudicator, scanned | Self-hosted Gemma 4 31B at 1120 on Colab, or Gemini 3.1 Pro | Pro units / per token | The pilot's verifier-grade recipe; Pro only on escalation |
| Overflow / insurance | Self-hosted Gemma 4 on Colab Pro via Colab CLI | Pro compute units | Full control of image-token budget; survives free-tier changes |
| Paid fallback, e-filed | Vertex AI managed Gemma 4 26B A4B (MaaS) | per token | Same model, paid SLA |
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
  charge with no paid tier. Exact limits from the AI Studio rate-limit table,
  2026-09-18. The project is on **Tier 1** (billing linked); the compare view
  shows Gemma's limits are identical on the free tier.

  | Model | RPM | Input TPM | RPD |
  |---|---|---|---|
  | Gemma 4 31B | 30 | 16K | 14.4K |
  | Gemma 4 26B A4B | 30 | 16K | 14.4K |
  | Gemini 3.7 Flash (Tier 1) | 1,000 | 2M | 10K |

  The probe below showed hosted Gemma reads at a fixed 258 tokens/page, so a
  one-page filing is ~300 input tokens and RPM, not TPM, is the binding cap:
  ~30 docs/min and ~14K docs/day per model.
- **Per-model caps are confirmed**, not an interpretation: the rate-limit table
  lists each model separately with its own usage counters, and the probe's
  calls appeared on their own rows.
- **Gemini Flash is on the free tier only nominally** (~5 RPM, ~250K TPM per
  the compare deltas). On this project's Tier 1 it is paid: ~$0.75/M input
  standard, ~$0.375/M Batch. The scanned PTR corpus (~2,500 docs × ~1.5K
  tokens ≈ 4M tokens) is ~$3 standard, so Batch is optional, not required.
- **Model choice.** Gemma 4 model card: 31B dense (30.7B params) vs 26B A4B MoE
  (25.2B total, 3.8B active). OmniDocBench 1.5: 0.131 vs 0.149 (lower is
  better). Both accept image input with the same visual token budget ladder
  (70/140/280/560/1120) when self-hosted; the API pins both at ~258. On the
  API both are free and equally resolution-limited, so they split the e-filed
  tier (26B first, 31B adjudicates); the 26B is the Vertex-managed model, so
  it is the upgrade path. The 31B's 1120-budget advantage only exists on Colab.
- **Two-model adjudication** mirrors the House pilot pattern (12B first pass,
  31B verifier, Claude Sonnet tie-break) but with no paid tier in the loop.
- **Colab is overflow, not primary.** Colab CLI (launched 2026-06-05) provisions
  T4/L4/A100/H100 headlessly via `colab exec`, keeps the VM alive after
  disconnect, and moves artifacts with `upload`/`download`. It runs from a
  laptop against the Pro subscription; driving it from Actions is unconfirmed
  and not planned. The MoE decodes faster than the 31B at the same VRAM, so it
  burns fewer units per page there.

### Probe results, 2026-09-18 (`scripts/probe_gemma_api.py`)

One e-filed House PTR sent to each model as PNG and as PDF, with and without
a response schema and `media_resolution=high`. All twelve calls succeeded.

| Model | PNG tokens | PDF tokens | Schema output | `media_resolution` effect | Latency |
|---|---|---|---|---|---|
| gemma-4-26b-a4b-it | 258 (IMAGE) | 258 (DOCUMENT) | yes, clean JSON | none | 2–19 s |
| gemma-4-31b-it | 258 | 258 | yes | none | 3–16 s |
| gemini-3.7-flash | 1,102 | 520 | yes | none observed | 2–3 s |

Consequences:
- **Gemma on the API accepts PDFs and honours `response_schema`.** Both were
  undocumented. Send e-filed docs as PDF; schema output is also much faster
  than free-text (no fence, no preamble).
- **Hosted Gemma reads every image at a fixed 258 tokens**, roughly the 280
  tier of the visual-budget ladder, and the setting cannot be raised. The
  House pilot needed 1120 on scans and saw a P/S flip at 560. **Hosted Gemma
  is therefore not the scan-tier model.** Gemini Flash reads at 1,102 natively.
- Token math changes: at ~300 input tokens per e-filed page the TPM cap no
  longer binds; RPM (~30) does, giving ~14K docs/day/model.
- **Gemma latency explained** (second probe run, with `thoughts_token_count`):
  Gemma thinks by default on free-text calls (263–901 thought tokens) and not
  at all under `response_schema` (none; 2–3 s). Decode rates from the token
  counts: 26B A4B ~45 tok/s, 31B ~33 tok/s, Gemini Flash ~230 tok/s. The MoE
  is faster per token as expected; it only looked slower because it thought
  more. Flash still thinks under schema mode (~180 tokens) but is fast enough
  not to care. **Production setting: schema mode plus an explicit
  `thinking_level`, not the undocumented side effect.** This matches the House
  pilot, which validated extraction with thinking off.
- **To test:** tiling a scanned page into 2–4 crops gives hosted Gemma 258
  tokens per crop. If that recovers scan accuracy on the golden set, the
  scan tier can also be free Gemma.

---

## 3. Routing rule

```
classify(doc)  ->  scanned | efiled
    scanned  ->  Gemini 3.7 Flash first (1,102 tokens/page)
    efiled   ->  Gemma 4 26B first, as PDF, with response_schema
result  ->  validate(schema) and sanity(row count vs header, dates plausible)
    pass     ->  accept, record model + prompt version in manifest
    fail     ->  send to the adjudicator for that class
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

- `gemini.py` — `google-genai` SDK, API key from env; serves both Gemma and
  Gemini models by model id. `response_schema` on every call. E-filed docs go
  as PDF (one request per document); scans go as page images. Handles 429
  with backoff and a rate limiter per model id.
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

1. ~~Separate vs shared rate-limit pools~~ **Resolved:** separate, per the
   AI Studio rate-limit table.
2. ~~How the Gemini API counts image tokens for Gemma 4~~ **Resolved:** fixed
   258 per image or PDF page, not settable. See probe results above.
2b. ~~Gemini 3.7 Flash free-tier limits~~ **Resolved:** Tier 1 applies (1K RPM,
   2M TPM, 10K RPD); scan tier is paid at ~$3 for the PTR corpus.
2d. ~~Gemma latency vs `thinking_level`~~ **Resolved:** default thinking; off
   under schema mode. Set it explicitly.
2c. **Tiled-crop trick** on hosted Gemma for scans, measured on the golden set.
3. ~~Exact rate-limit values~~ **Resolved:** see table in §2.
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
| House PTRs, e-filed (~70%) | ~5,900 | under one day on Gemma at ~14K RPD |
| House PTRs, scanned (~30%) | ~2,500 | Flash on Tier 1: ~3 hours at 1K RPM; ~$3 standard, ~$1.50 Batch |
| House annual reports | unsized | multi-page; unknown |
| Senate, 2012–present | unsized | scanned share unknown |

All prior timing estimates (42 s/doc median on a single 8 GB consumer GPU
through a flapping tunnel) are obsolete. Re-measure on the API and on an L4.
