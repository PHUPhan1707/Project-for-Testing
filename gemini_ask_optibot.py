"""
OptiBot Mini-Clone — Step 2 (Gemini): quick sanity check.

Sends a question to OptiBot (Gemini + File Search grounding) using the verbatim
system prompt, and prints the answer plus the cited source documents.

Unlike OpenAI's Assistants, Gemini has no persistent "assistant" object — the
system prompt and the File Search tool are supplied on each generate_content
call. (In AI Studio you paste the same system prompt into "System instructions"
for the screenshot.)

Defaults to the required test question: "How do I add a YouTube video?"

Prereqs:
  - GEMINI_API_KEY set (https://aistudio.google.com/apikey).
  - `gemini_store.json` present (run gemini_upload_to_store.py first).

Usage:
    python gemini_ask_optibot.py
    python gemini_ask_optibot.py "How do I set up a Raspberry Pi player?"
    python gemini_ask_optibot.py --model gemini-2.5-pro
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google import genai
from google.genai import types

from gemini_auth import get_gemini_api_key

STATE_FILE = "gemini_store.json"
DEFAULT_QUESTION = "How do I add a YouTube video?"
DEFAULT_MODEL = "gemini-2.5-flash"

# Copied VERBATIM from the take-home spec.
SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask OptiBot (Gemini + File Search).")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="a File-Search-capable model, e.g. gemini-2.5-flash or gemini-2.5-pro")
    parser.add_argument("--state-file", default=STATE_FILE)
    args = parser.parse_args()

    api_key = get_gemini_api_key()

    state_path = Path(args.state_file)
    if not state_path.exists():
        sys.exit(f"ERROR: {args.state_file} not found. Run gemini_upload_to_store.py first.")

    store_name = json.loads(state_path.read_text(encoding="utf-8"))["store_name"]

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=args.model,
        contents=args.question,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
        ),
    )

    print(f"Q: {args.question}\n")
    print("A:", response.text)

    # Surface the grounding citations (the docs File Search retrieved).
    seen: set[str] = set()
    cites: list[str] = []
    for cand in response.candidates or []:
        gm = getattr(cand, "grounding_metadata", None)
        for chunk in getattr(gm, "grounding_chunks", None) or []:
            ctx = getattr(chunk, "retrieved_context", None)
            if not ctx:
                continue
            label = ctx.title or ctx.document_name or ctx.uri or "(unknown)"
            if label not in seen:
                seen.add(label)
                cites.append(label)

    if cites:
        print("\nGrounded on documents:")
        for c in cites:
            print(f"  - {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
