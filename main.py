"""
OptiBot Mini-Clone — Step 3: daily scrape + delta upload job.

Runs the whole pipeline once and exits (designed to be run daily by a
scheduler / cron / container):

  1. Re-scrape all published articles from support.optisigns.com.
  2. Detect new / updated / unchanged articles using a content hash.
  3. Upload ONLY the delta (new + changed) to the Gemini File Search store.
  4. Log counts: added, updated, skipped.

Delta state is stored *in the File Search store itself* as per-document
`custom_metadata` (article_id + content_hash), so the job is fully stateless
and idempotent — it works even in ephemeral containers with no local disk.

Env vars (see .env.sample):
  GEMINI_API_KEY   (required; GOOGLE_API_KEY or API_KEY also accepted)
  STORE_DISPLAY_NAME       default "OptiBot Support Docs"
  CHUNK_MAX_TOKENS         default 800
  CHUNK_OVERLAP_TOKENS     default 200
  EMBEDDING_MODEL          default models/gemini-embedding-001
  ARTICLES_DIR             default articles
  MAX_ARTICLES             default (unset = all); limit for testing

Usage:
    python main.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from gemini_auth import get_gemini_api_key
from google import genai
from google.genai import types

# Reuse the Step 1 scraper and the Step 2 upload helpers.
from scraper import make_session, iter_articles, build_article, write_article
from gemini_upload_to_store import (
    get_encoder, count_tokens, estimate_chunks, poll_operation, with_retry,
    THROTTLE_SECONDS,
)

# ---------------------------------------------------------------------------
# Configuration (env-driven)
# ---------------------------------------------------------------------------

STORE_DISPLAY_NAME = os.environ.get("STORE_DISPLAY_NAME", "OptiBot Support Docs")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "models/gemini-embedding-001")
ARTICLES_DIR = Path(os.environ.get("ARTICLES_DIR", "articles"))
MAX_TOKENS = int(os.environ.get("CHUNK_MAX_TOKENS", "512"))  # Gemini max is 512
OVERLAP = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "100"))
_max_env = os.environ.get("MAX_ARTICLES", "").strip()
MAX_ARTICLES = int(_max_env) if _max_env else None

RUN_LOG_FILE = os.environ.get("RUN_LOG_FILE", "last_run.json")

META_ARTICLE_ID = "article_id"
META_CONTENT_HASH = "content_hash"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------


def find_or_create_store(client: genai.Client):
    for store in client.file_search_stores.list():
        if store.display_name == STORE_DISPLAY_NAME:
            print(f"Reusing existing store: {store.name}")
            return store
    store = client.file_search_stores.create(
        config=types.CreateFileSearchStoreConfig(
            display_name=STORE_DISPLAY_NAME,
            embedding_model=EMBEDDING_MODEL,
        )
    )
    print(f"Created new store: {store.name}")
    return store


def load_existing_index(client: genai.Client, store_name: str) -> dict[str, dict]:
    """
    Build {article_id: {"hash": ..., "doc_name": ...}} from the documents
    already present in the store (using their custom_metadata).
    """
    index: dict[str, dict] = {}
    for doc in client.file_search_stores.documents.list(parent=store_name):
        meta = {m.key: m.string_value for m in (doc.custom_metadata or [])}
        aid = meta.get(META_ARTICLE_ID)
        if aid:
            index[aid] = {"hash": meta.get(META_CONTENT_HASH), "doc_name": doc.name}
    return index


def upload_article(client: genai.Client, store_name: str, path: Path,
                   article, chash: str, chunking_config):
    def _do():
        op = client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=store_name,
            file=str(path),
            config=types.UploadToFileSearchStoreConfig(
                display_name=path.stem,
                mime_type="text/markdown",
                chunking_config=chunking_config,
                custom_metadata=[
                    types.CustomMetadata(key=META_ARTICLE_ID, string_value=str(article.id)),
                    types.CustomMetadata(key=META_CONTENT_HASH, string_value=chash),
                    types.CustomMetadata(key="source_url", string_value=article.url),
                ],
            ),
        )
        return poll_operation(client, op)

    return with_retry(_do, label=path.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    started = time.time()
    api_key = get_gemini_api_key()
    client = genai.Client(api_key=api_key)

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    chunking_config = types.ChunkingConfig(
        white_space_config=types.WhiteSpaceConfig(
            max_tokens_per_chunk=MAX_TOKENS, max_overlap_tokens=OVERLAP,
        )
    )

    # 1. Locate the store and read what's already indexed.
    store = find_or_create_store(client)
    existing = load_existing_index(client, store.name)
    print(f"Documents already indexed: {len(existing)}\n")

    # 2. Re-scrape everything and classify against the existing index.
    session = make_session()
    used_names: set[str] = set()
    encoder = get_encoder()

    added, updated, skipped = [], [], []
    scraped = 0

    print("Re-scraping and diffing...")
    for raw in iter_articles(session):
        article = build_article(raw)
        path = write_article(article, ARTICLES_DIR, used_names)
        chash = content_hash(article.markdown)
        aid = str(article.id)
        scraped += 1

        prev = existing.get(aid)
        if prev is None:
            added.append((article, path, chash))
        elif prev.get("hash") != chash:
            updated.append((article, path, chash, prev.get("doc_name")))
        else:
            skipped.append(aid)

        if MAX_ARTICLES is not None and scraped >= MAX_ARTICLES:
            break

    print(f"Scraped {scraped} articles -> "
          f"{len(added)} new, {len(updated)} updated, {len(skipped)} unchanged\n")

    # 3. Apply the delta (upload new + changed only).
    embedded_chunks = 0
    failures = 0

    for article, path, chash in added:
        try:
            upload_article(client, store.name, path, article, chash, chunking_config)
            embedded_chunks += estimate_chunks(
                count_tokens(article.markdown, encoder), MAX_TOKENS, OVERLAP)
            print(f"  + added   {path.name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ! FAILED add {path.name}: {exc}", file=sys.stderr)
        time.sleep(THROTTLE_SECONDS)

    for article, path, chash, old_doc in updated:
        try:
            if old_doc:
                client.file_search_stores.documents.delete(
                    name=old_doc, config={"force": True})
            upload_article(client, store.name, path, article, chash, chunking_config)
            embedded_chunks += estimate_chunks(
                count_tokens(article.markdown, encoder), MAX_TOKENS, OVERLAP)
            print(f"  ~ updated {path.name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ! FAILED update {path.name}: {exc}", file=sys.stderr)
        time.sleep(THROTTLE_SECONDS)

    # 4. Report.
    elapsed = time.time() - started
    summary = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "store_name": store.name,
        "store_display_name": STORE_DISPLAY_NAME,
        "scraped": scraped,
        "added": len(added),
        "updated": len(updated),
        "skipped": len(skipped),
        "failed": failures,
        "chunks_embedded_est": embedded_chunks,
        "chunking": {"max_tokens_per_chunk": MAX_TOKENS, "max_overlap_tokens": OVERLAP},
        "elapsed_seconds": round(elapsed, 1),
    }

    print("\n" + "=" * 60)
    print(f"DELTA SYNC COMPLETE  ({elapsed:.1f}s)")
    print(f"  added   : {len(added)}")
    print(f"  updated : {len(updated)}")
    print(f"  skipped : {len(skipped)}")
    print(f"  failed  : {failures}")
    print(f"  chunks embedded (est) : {embedded_chunks}")
    print(f"  store   : {store.name}")
    print("=" * 60)

    Path(RUN_LOG_FILE).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote run summary to '{RUN_LOG_FILE}'.")

    # Exit 0 on success; non-zero only if uploads failed.
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
