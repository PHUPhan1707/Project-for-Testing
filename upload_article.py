"""
Upload one or more specific Markdown articles to the existing Gemini File Search store.

Useful when you only need certain docs indexed (e.g. the YouTube article for
the sanity-check question) without uploading all 404 files.

Usage:
    python upload_article.py how-to-use-youtube-with-optisigns
    python upload_article.py articles/how-to-use-youtube-with-optisigns.md
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from google import genai
from google.genai import types

from gemini_auth import get_gemini_api_key
from gemini_upload_to_store import poll_operation, with_retry, THROTTLE_SECONDS

ARTICLES_DIR = Path("articles")
STATE_FILE = Path("gemini_store.json")
MAX_TOKENS = 512
OVERLAP = 100


def resolve_path(arg: str) -> Path:
    p = Path(arg)
    if p.suffix == ".md" and p.exists():
        return p
    slug = arg.removesuffix(".md")
    candidate = ARTICLES_DIR / f"{slug}.md"
    if candidate.exists():
        return candidate
    sys.exit(f"ERROR: article not found: {arg}")


def get_store_name(client: genai.Client) -> str:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))["store_name"]
    for store in client.file_search_stores.list():
        if store.display_name == "OptiBot Support Docs":
            return store.name
    sys.exit("ERROR: no File Search store found. Run main.py or gemini_upload_to_store.py first.")


def upload_file(client: genai.Client, store_name: str, path: Path) -> None:
    chunking = types.ChunkingConfig(
        white_space_config=types.WhiteSpaceConfig(
            max_tokens_per_chunk=MAX_TOKENS, max_overlap_tokens=OVERLAP,
        )
    )

    def _do():
        op = client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=store_name,
            file=str(path),
            config=types.UploadToFileSearchStoreConfig(
                display_name=path.stem,
                mime_type="text/markdown",
                chunking_config=chunking,
            ),
        )
        return poll_operation(client, op)

    with_retry(_do, label=path.name)
    print(f"Uploaded: {path.name}")


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit("Usage: python upload_article.py <slug-or-path> [more ...]")

    client = genai.Client(api_key=get_gemini_api_key())
    store_name = get_store_name(client)
    print(f"Store: {store_name}\n")

    for arg in sys.argv[1:]:
        path = resolve_path(arg)
        upload_file(client, store_name, path)
        time.sleep(THROTTLE_SECONDS)

    print("\nDone. Test with:")
    print('  python gemini_ask_optibot.py "How do I add a YouTube video?"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
