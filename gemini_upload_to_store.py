"""
OptiBot Mini-Clone — Step 2 (Gemini): load the File Search store.

Uploads the scraped Markdown files to a Gemini **File Search store** via the
API (no UI drag-and-drop). File Search is Gemini's managed RAG / "vector store"
equivalent: it chunks, embeds, and indexes each document so OptiBot can ground
its answers on the OptiSigns support docs.

Why Gemini File Search:
  - Real free tier (no credit card / prepaid balance required).
  - Storage + query-time embeddings are free; only first-time indexing is
    billed on paid tiers, and it's waived within the free tier's limits.

Chunking strategy (see README):
  white-space chunking, max_tokens_per_chunk=800, max_overlap_tokens=200.

Auth:
  Set GEMINI_API_KEY (get one free at https://aistudio.google.com/apikey).

Usage:
    python gemini_upload_to_store.py
    python gemini_upload_to_store.py --max 40         # small test batch
    python gemini_upload_to_store.py --max-tokens 800 --overlap 200
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

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from gemini_auth import get_gemini_api_key

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_STORE_NAME = "OptiBot Support Docs"
DEFAULT_ARTICLES_DIR = "articles"
DEFAULT_STATE_FILE = "gemini_store.json"
DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-001"
DEFAULT_MAX_TOKENS = 512     # tokens per chunk (Gemini max is 512)
DEFAULT_OVERLAP = 100        # overlap tokens between chunks (must be <= half)

# Free tier is ~15 requests/minute. Sleep between uploads to stay under it.
THROTTLE_SECONDS = 4.5
RETRY_ATTEMPTS = 5
POLL_INTERVAL = 3.0


# ---------------------------------------------------------------------------
# Chunk estimation (local, approximate — mirrors our chunking params)
# ---------------------------------------------------------------------------


def get_encoder():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, encoder) -> int:
    if encoder is None:
        return max(1, len(text) // 4)
    return len(encoder.encode(text))


def estimate_chunks(num_tokens: int, max_chunk: int, overlap: int) -> int:
    if num_tokens <= max_chunk:
        return 1
    step = max_chunk - overlap
    return 1 + math.ceil((num_tokens - max_chunk) / step)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def collect_markdown_files(articles_dir: Path) -> list[Path]:
    files = sorted(p for p in articles_dir.glob("*.md") if p.is_file())
    if not files:
        sys.exit(f"No .md files found in '{articles_dir}'. Run scraper.py first.")
    return files


def with_retry(fn, *, label: str):
    """Call fn() with exponential backoff on rate-limit / transient errors."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except genai_errors.APIError as exc:
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            transient = status in (429, 500, 503) or "RESOURCE_EXHAUSTED" in str(exc)
            if not transient or attempt == RETRY_ATTEMPTS:
                raise
            wait = min(60, THROTTLE_SECONDS * (2 ** attempt))
            print(f"  ! {label} rate-limited/transient ({status}); "
                  f"retry {attempt}/{RETRY_ATTEMPTS} in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)


def poll_operation(client: genai.Client, operation):
    while not operation.done:
        time.sleep(POLL_INTERVAL)
        operation = client.operations.get(operation)
    return operation


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Markdown docs to a Gemini File Search store.")
    parser.add_argument("--articles", default=DEFAULT_ARTICLES_DIR)
    parser.add_argument("--name", default=DEFAULT_STORE_NAME)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--max", type=int, default=None, help="limit number of files (for testing)")
    args = parser.parse_args()

    api_key = get_gemini_api_key()

    articles_dir = Path(args.articles)
    files = collect_markdown_files(articles_dir)
    if args.max is not None:
        files = files[:args.max]

    # --- Estimate chunks locally (same params we send to Gemini) ----------
    encoder = get_encoder()
    total_tokens = 0
    total_chunks = 0
    for path in files:
        n = count_tokens(path.read_text(encoding="utf-8"), encoder)
        total_tokens += n
        total_chunks += estimate_chunks(n, args.max_tokens, args.overlap)

    print(f"Files to embed        : {len(files)}")
    print(f"Total tokens (approx) : {total_tokens:,}")
    print(f"Estimated chunks      : {total_chunks:,}  "
          f"(white-space: max={args.max_tokens}, overlap={args.overlap})")
    print()

    client = genai.Client(api_key=api_key)

    # --- Create the File Search store -------------------------------------
    store = client.file_search_stores.create(
        config=types.CreateFileSearchStoreConfig(
            display_name=args.name,
            embedding_model=args.embedding_model,
        )
    )
    print(f"Created File Search store: {store.name} ('{args.name}')")
    print()

    chunking_config = types.ChunkingConfig(
        white_space_config=types.WhiteSpaceConfig(
            max_tokens_per_chunk=args.max_tokens,
            max_overlap_tokens=args.overlap,
        )
    )

    # --- Upload each file --------------------------------------------------
    uploaded = 0
    failed = 0
    for idx, path in enumerate(files, start=1):
        def _upload(p=path):
            op = client.file_search_stores.upload_to_file_search_store(
                file_search_store_name=store.name,
                file=str(p),
                config=types.UploadToFileSearchStoreConfig(
                    display_name=p.stem,
                    mime_type="text/markdown",
                    chunking_config=chunking_config,
                ),
            )
            return poll_operation(client, op)

        try:
            op = with_retry(_upload, label=path.name)
            if getattr(op, "error", None):
                failed += 1
                print(f"  [{idx}/{len(files)}] FAILED {path.name}: {op.error}")
            else:
                uploaded += 1
                if idx % 10 == 0 or idx == len(files):
                    print(f"  [{idx}/{len(files)}] uploaded ({uploaded} ok, {failed} failed)")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [{idx}/{len(files)}] ERROR {path.name}: {exc}", file=sys.stderr)

        time.sleep(THROTTLE_SECONDS)  # respect free-tier rate limit

    print()
    print("=" * 60)
    print(f"Files embedded  : {uploaded} (failed: {failed})")
    print(f"Chunks embedded : ~{total_chunks:,} (estimated, white-space strategy)")
    print(f"File Search store: {store.name}")
    print("=" * 60)

    state = {
        "store_name": store.name,
        "display_name": args.name,
        "embedding_model": args.embedding_model,
        "files_embedded": uploaded,
        "files_failed": failed,
        "estimated_chunks": total_chunks,
        "chunking": {"max_tokens_per_chunk": args.max_tokens, "max_overlap_tokens": args.overlap},
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    Path(args.state_file).write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Saved state to '{args.state_file}'.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
