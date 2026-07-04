"""
OptiBot Mini-Clone — Step 2: Programmatically load the vector store.

Uploads the scraped Markdown files to an OpenAI Vector Store via the API
(no UI drag-and-drop), so the OptiBot assistant can answer questions using
`file_search` over the OptiSigns support docs.

What it does:
  1. Creates (or reuses) a Vector Store.
  2. Uploads every `articles/*.md` file with a defined *static* chunking
     strategy (see README for rationale).
  3. Logs how many files and (estimated) chunks were embedded, and saves the
     vector store id to `vector_store.json` for reuse / the delta-update job.

Chunking strategy (see README):
  static, max_chunk_size_tokens=800, chunk_overlap_tokens=200.

Auth:
  Set OPENAI_API_KEY in your environment before running.

Usage:
    python upload_to_vector_store.py
    python upload_to_vector_store.py --max-chunk-size 800 --chunk-overlap 200
    python upload_to_vector_store.py --name "OptiBot Support Docs"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None

from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_VECTOR_STORE_NAME = "OptiBot Support Docs"
DEFAULT_ARTICLES_DIR = "articles"
DEFAULT_STATE_FILE = "vector_store.json"
DEFAULT_MAX_CHUNK_SIZE = 800     # tokens (OpenAI allows 100..4096)
DEFAULT_CHUNK_OVERLAP = 200      # tokens (must be <= max_chunk_size / 2)
UPLOAD_BATCH_SIZE = 100          # files per file-batch upload


# ---------------------------------------------------------------------------
# Chunk estimation
# ---------------------------------------------------------------------------


def get_encoder():
    """Return a tiktoken encoder, or None if tiktoken is unavailable."""
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, encoder) -> int:
    if encoder is None:
        # Rough fallback: ~4 characters per token.
        return max(1, len(text) // 4)
    return len(encoder.encode(text))


def estimate_chunks(num_tokens: int, max_chunk: int, overlap: int) -> int:
    """
    Estimate how many chunks OpenAI will create for a file under the static
    strategy (sliding window of `max_chunk` tokens stepping by
    `max_chunk - overlap`). This mirrors the server-side behaviour closely
    enough to report a meaningful count.
    """
    if num_tokens <= max_chunk:
        return 1
    step = max_chunk - overlap
    return 1 + math.ceil((num_tokens - max_chunk) / step)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def collect_markdown_files(articles_dir: Path) -> list[Path]:
    files = sorted(p for p in articles_dir.glob("*.md") if p.is_file())
    if not files:
        sys.exit(f"No .md files found in '{articles_dir}'. Run scraper.py first.")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Markdown docs to an OpenAI Vector Store.")
    parser.add_argument("--articles", default=DEFAULT_ARTICLES_DIR, help="directory of .md files")
    parser.add_argument("--name", default=DEFAULT_VECTOR_STORE_NAME, help="vector store name")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="where to save the vector store id")
    parser.add_argument("--max-chunk-size", type=int, default=DEFAULT_MAX_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument("--batch-size", type=int, default=UPLOAD_BATCH_SIZE)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("ERROR: OPENAI_API_KEY is not set. Export it and re-run.")

    if args.chunk_overlap > args.max_chunk_size // 2:
        sys.exit("ERROR: --chunk-overlap must be <= half of --max-chunk-size.")

    articles_dir = Path(args.articles)
    files = collect_markdown_files(articles_dir)

    # --- Estimate chunks locally (same params we send to OpenAI) -----------
    encoder = get_encoder()
    total_tokens = 0
    total_chunks = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        n_tokens = count_tokens(text, encoder)
        total_tokens += n_tokens
        total_chunks += estimate_chunks(n_tokens, args.max_chunk_size, args.chunk_overlap)

    print(f"Files to embed        : {len(files)}")
    print(f"Total tokens (approx) : {total_tokens:,}")
    print(f"Estimated chunks      : {total_chunks:,}  "
          f"(static: max={args.max_chunk_size}, overlap={args.chunk_overlap})")
    print()

    # --- Create the vector store ------------------------------------------
    client = OpenAI()
    chunking_strategy = {
        "type": "static",
        "static": {
            "max_chunk_size_tokens": args.max_chunk_size,
            "chunk_overlap_tokens": args.chunk_overlap,
        },
    }

    vector_store = client.vector_stores.create(name=args.name)
    print(f"Created vector store: {vector_store.id} ('{args.name}')")

    # --- Upload in batches -------------------------------------------------
    uploaded = 0
    failed = 0
    for start in range(0, len(files), args.batch_size):
        batch_paths = files[start:start + args.batch_size]
        streams = [open(p, "rb") for p in batch_paths]
        try:
            batch = client.vector_stores.file_batches.upload_and_poll(
                vector_store_id=vector_store.id,
                files=streams,
                chunking_strategy=chunking_strategy,
            )
        finally:
            for s in streams:
                s.close()

        uploaded += batch.file_counts.completed
        failed += batch.file_counts.failed
        print(f"  batch {start // args.batch_size + 1}: "
              f"status={batch.status}, completed={batch.file_counts.completed}, "
              f"failed={batch.file_counts.failed}")

    print()
    print("=" * 60)
    print(f"Files embedded  : {uploaded} (failed: {failed})")
    print(f"Chunks embedded : ~{total_chunks:,} (estimated with static strategy)")
    print(f"Vector store id : {vector_store.id}")
    print("=" * 60)

    # --- Persist state -----------------------------------------------------
    state = {
        "vector_store_id": vector_store.id,
        "name": args.name,
        "files_embedded": uploaded,
        "files_failed": failed,
        "estimated_chunks": total_chunks,
        "chunking_strategy": chunking_strategy,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    Path(args.state_file).write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Saved state to '{args.state_file}'.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
