#!/usr/bin/env python3
"""One-off capability test: extract House PTR transactions from PDF page
images using a local multimodal model behind llama-server's OpenAI API.

Usage:
    uv run python data/house_pdfs/gemma_extract.py [BASE_URL] [DOC_ID ...]

BASE_URL defaults to http://blackbird2-wsl.tail2aa4e.ts.net:8000. With no
DOC_IDs, all nine documents run. Output files are tagged with the served
model's name, so different models (31B vs 12B) don't overwrite each other:
gemma_<model>_<docid>.raw.txt per doc plus a combined
extract_gemma_<model>.json in the same shape as the Claude-model outputs.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
# GEMMA_NOTHINK=1 disables the model's reasoning phase via chat_template_kwargs
# (verified honored by llama-server for this Gemma template).
NOTHINK = os.environ.get("GEMMA_NOTHINK") == "1"
# Client-side per-request timeout, seconds. llama-server has no generation
# deadline of its own — for grind-all-night jobs just raise this.
TIMEOUT = int(os.environ.get("GEMMA_TIMEOUT", "1800"))
# GEMMA_TEMP=1.0 samples at Gemma's recommended settings (top_k 64, top_p
# 0.95) for run-to-run consensus tests; default 0 = greedy, as always.
TEMP = float(os.environ.get("GEMMA_TEMP", "0"))
# Raise for many-row docs where 4096 would truncate the JSON mid-array.
MAX_TOKENS = int(os.environ.get("GEMMA_MAX_TOKENS", "4096"))
BASE = (sys.argv[1] if len(sys.argv) > 1 else
        "http://blackbird2-wsl.tail2aa4e.ts.net:8000").rstrip("/")

DOCS = {
    "20034915": ["20034915-1.png"],
    "20034562": ["20034562-1.png"],
    "20034298": ["20034298-1.png"],
    "20033783": ["20033783-1.png", "20033783-2.png"],
    "20034660": ["20034660-1.png", "20034660-2.png", "20034660-3.png"],
    "9115816": ["9115816-1.png"],
    "20000077": ["jammed_20000077-1.png"],
    "20032062": ["jammed_20032062-1.png"],
    "20017359": ["jammed_20017359-1.png", "jammed_20017359-2.png"],
}

PROMPT = """These images are the pages of one US House Periodic Transaction Report (PTR). Extract EVERY transaction row into exactly this JSON shape and output ONLY the JSON, no prose, no markdown fences:

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

Transcribe exactly what is printed. Do not guess missing values; use null. A row may continue across a page boundary - do not double-count it."""


def get_model() -> str:
    with urllib.request.urlopen(f"{BASE}/v1/models", timeout=10) as r:
        return json.load(r)["data"][0]["id"]


def ask(model: str, doc_id: str, pages: list[str]) -> tuple[str, float]:
    content: list[dict] = [{"type": "text", "text": PROMPT % doc_id}]
    for p in pages:
        b64 = base64.b64encode((HERE / "png" / p).read_bytes()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    payload = {
        "model": model,
        "temperature": TEMP,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": content}],
    }
    if TEMP > 0:
        payload["top_k"] = 64
        payload["top_p"] = 0.95
    if NOTHINK:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        resp = json.load(r)
    msg = resp["choices"][0]["message"]
    stats = {
        "seconds": round(time.time() - t0, 1),
        "usage": resp.get("usage"),
        "timings": resp.get("timings"),
        "reasoning_chars": len(msg.get("reasoning_content") or ""),
        "content_chars": len(msg.get("content") or ""),
    }
    return msg["content"], stats


def parse_json(text: str) -> dict | None:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.startswith("json") else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def main() -> None:
    model = get_model()
    tag = "".join(c if c.isalnum() else "_" for c in model.split("/")[-1])[:48]
    if NOTHINK:
        tag += "_nothink"
    # Distinguish server-side conditions the harness can't detect (e.g. the
    # --image-max-tokens budget): GEMMA_TAG_SUFFIX=_1120
    tag += os.environ.get("GEMMA_TAG_SUFFIX", "")
    subset = [d for d in sys.argv[2:] if d in DOCS]
    docs = {d: DOCS[d] for d in subset} if subset else DOCS
    print(f"model: {model} | docs: {list(docs)}", file=sys.stderr)
    combined = {"model": model, "docs": []}
    for doc_id, pages in docs.items():
        try:
            text, stats = ask(model, doc_id, pages)
        except Exception as e:
            print(f"{doc_id}: FAILED {e}", file=sys.stderr)
            combined["docs"].append({"doc_id": doc_id, "error": str(e)})
            continue
        (HERE / f"gemma_{tag}_{doc_id}.raw.txt").write_text(text)
        (HERE / f"gemma_{tag}_{doc_id}.stats.json").write_text(json.dumps(stats, indent=1))
        parsed = parse_json(text)
        n = parsed.get("n_transactions") if parsed else "unparseable"
        u = stats.get("usage") or {}
        print(f"{doc_id}: {stats['seconds']:.0f}s, n_transactions={n}, "
              f"prompt={u.get('prompt_tokens')}, output={u.get('completion_tokens')}, "
              f"reasoning_chars={stats['reasoning_chars']}", file=sys.stderr)
        if parsed is not None:
            parsed["_stats"] = stats
        combined["docs"].append(parsed or {"doc_id": doc_id, "error": "bad JSON", "raw": text[:500]})
    (HERE / f"extract_gemma_{tag}.json").write_text(json.dumps(combined, indent=2))
    print(json.dumps({"done": True, "model": model, "docs": len(combined["docs"])}))


if __name__ == "__main__":
    main()
