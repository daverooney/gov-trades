#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai>=1.0",
#     "requests>=2.31",
# ]
# ///
"""
Gemma-on-Gemini-API capability probe
=====================================
Answers, empirically, what the docs do not say about Gemma 4 on the Gemini API:

  1. Does it accept a PDF inline (application/pdf), or only images?
  2. Does it honour response_schema (structured JSON output)?
  3. Does it accept media_resolution?
  4. What does the usage metadata say about image/PDF token counts?

Runs each check against both Gemma 4 models and, for comparison, one Gemini
Flash model. Prints a table. Exit 0 always; the table is the answer.

Needs GEMINI_API_KEY in the environment. Uses one real e-filed House PTR
(public, open access) as the test document. Each call is a single request,
so the whole probe costs ~10 requests against the free tier.
"""
import io
import os
import subprocess
import sys
import tempfile
import time

import requests
from google import genai
from google.genai import types

PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20033500.pdf"
UA = "gov-trades probe (d@verooney.com)"
MODELS = ["gemma-4-26b-a4b-it", "gemma-4-31b-it", "gemini-3.7-flash"]
PROMPT = ("List every transaction on this financial disclosure as JSON: "
          "an array of objects with keys asset_name, ticker, transaction_type, "
          "transaction_date, amount_range. Return only JSON.")
SCHEMA = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_name": {"type": "string"},
                    "ticker": {"type": "string"},
                    "transaction_type": {"type": "string"},
                    "transaction_date": {"type": "string"},
                    "amount_range": {"type": "string"},
                },
                "required": ["asset_name", "transaction_type"],
            },
        }
    },
    "required": ["transactions"],
}


def fetch_pdf() -> bytes:
    r = requests.get(PDF_URL, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content


def render_png(pdf: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "doc.pdf")
        with open(src, "wb") as f:
            f.write(pdf)
        subprocess.run(["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1",
                        src, os.path.join(d, "page")], check=True)
        png = [p for p in os.listdir(d) if p.endswith(".png")][0]
        with open(os.path.join(d, png), "rb") as f:
            return f.read()


def call(client, model, part, config=None):
    t0 = time.time()
    try:
        r = client.models.generate_content(
            model=model,
            contents=[part, PROMPT],
            config=config,
        )
        um = r.usage_metadata
        detail = ""
        if um is not None:
            mods = getattr(um, "prompt_tokens_details", None) or []
            detail = ", ".join(f"{m.modality}={m.token_count}" for m in mods)
        text = (r.text or "").strip()
        looks_json = text.startswith("{") or text.startswith("[")
        return ("OK", f"{time.time()-t0:.1f}s in={um.prompt_token_count} "
                      f"out={um.candidates_token_count} [{detail}] "
                      f"json={'yes' if looks_json else 'no'} "
                      f"head={text[:60]!r}")
    except Exception as e:  # noqa: BLE001
        msg = " ".join(str(e).split())[:160]
        return ("FAIL", f"{time.time()-t0:.1f}s {msg}")


def main():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("!! GEMINI_API_KEY not set")
        return 1
    client = genai.Client(api_key=key)

    print(">> fetching test PDF")
    pdf = fetch_pdf()
    print(f"   {len(pdf)} bytes")
    print(">> rendering page 1 to PNG")
    png = render_png(pdf)
    print(f"   {len(png)} bytes")

    pdf_part = types.Part.from_bytes(data=pdf, mime_type="application/pdf")
    png_part = types.Part.from_bytes(data=png, mime_type="image/png")

    checks = [
        ("png, plain", png_part, None),
        ("pdf, plain", pdf_part, None),
        ("png, response_schema", png_part,
         types.GenerateContentConfig(response_mime_type="application/json",
                                     response_schema=SCHEMA)),
        ("png, media_resolution=high", png_part,
         types.GenerateContentConfig(media_resolution="MEDIA_RESOLUTION_HIGH")),
    ]

    rows = []
    for model in MODELS:
        for label, part, cfg in checks:
            print(f">> {model:22s} {label}")
            status, detail = call(client, model, part, cfg)
            print(f"   {status}: {detail}")
            rows.append((model, label, status, detail))
            time.sleep(2)

    print("\n== SUMMARY ==")
    w = max(len(r[1]) for r in rows)
    for model, label, status, detail in rows:
        print(f"{model:22s} {label:{w}s} {status:4s} {detail[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
