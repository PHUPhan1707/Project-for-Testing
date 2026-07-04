"""
OptiBot Mini-Clone — Step 2: Create the OptiBot assistant (reproducible).

The take-home allows setting up the assistant in the OpenAI Playground UI.
This script does the same thing via API so the setup is reproducible and the
vector store is wired up automatically with the `file_search` tool.

Prereqs:
  - OPENAI_API_KEY set in the environment.
  - `vector_store.json` present (created by upload_to_vector_store.py).

Usage:
    python create_assistant.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

# The system prompt below is copied VERBATIM from the take-home spec.
SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""

MODEL = "gpt-4o"
STATE_FILE = "vector_store.json"


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ERROR: OPENAI_API_KEY is not set. Export it and re-run.")

    state_path = Path(STATE_FILE)
    if not state_path.exists():
        sys.exit(f"ERROR: {STATE_FILE} not found. Run upload_to_vector_store.py first.")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    vector_store_id = state["vector_store_id"]

    client = OpenAI()
    assistant = client.beta.assistants.create(
        name="OptiBot",
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        tools=[{"type": "file_search"}],
        tool_resources={"file_search": {"vector_store_ids": [vector_store_id]}},
    )

    print(f"Created assistant: {assistant.id}")
    print(f"Attached vector store: {vector_store_id}")

    state["assistant_id"] = assistant.id
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Saved assistant id to '{STATE_FILE}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
