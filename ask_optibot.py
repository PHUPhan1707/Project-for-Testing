"""
OptiBot Mini-Clone — Step 2: Quick sanity check.

Sends a question to the OptiBot assistant and prints the answer plus the
cited source files. Defaults to the required test question:

    "How do I add a YouTube video?"

Prereqs:
  - OPENAI_API_KEY set in the environment.
  - `vector_store.json` present with an `assistant_id` (run create_assistant.py).

Usage:
    python ask_optibot.py
    python ask_optibot.py "How do I set up a Raspberry Pi player?"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

STATE_FILE = "vector_store.json"
DEFAULT_QUESTION = "How do I add a YouTube video?"


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ERROR: OPENAI_API_KEY is not set. Export it and re-run.")

    state_path = Path(STATE_FILE)
    if not state_path.exists():
        sys.exit(f"ERROR: {STATE_FILE} not found. Run create_assistant.py first.")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assistant_id = state.get("assistant_id")
    if not assistant_id:
        sys.exit("ERROR: no assistant_id in state file. Run create_assistant.py first.")

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION

    client = OpenAI()
    thread = client.beta.threads.create(
        messages=[{"role": "user", "content": question}]
    )
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id, assistant_id=assistant_id
    )

    print(f"Q: {question}\n")
    if run.status != "completed":
        sys.exit(f"Run ended with status: {run.status}")

    messages = client.beta.threads.messages.list(thread_id=thread.id, order="desc")
    for msg in messages.data:
        if msg.role != "assistant":
            continue
        for part in msg.content:
            if part.type == "text":
                print("A:", part.text.value)
                # Surface file citations (annotations) if present.
                cites = [a for a in part.text.annotations if a.type == "file_citation"]
                if cites:
                    print("\nCited files:")
                    for a in cites:
                        fid = a.file_citation.file_id
                        f = client.files.retrieve(fid)
                        print(f"  - {f.filename}")
        break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
