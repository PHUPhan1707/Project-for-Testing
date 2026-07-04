"""
OptiBot Mini-Clone — Step 1: Scrape ⇒ Markdown

Pulls published articles from the OptiSigns support site (a Zendesk Help
Center) via the public Zendesk Help Center API and normalizes each article
into clean Markdown.

Why the API instead of raw HTML scraping?
  The public Help Center endpoint returns the article body as focused HTML
  (title + content only) with no site chrome, so nav bars, footers, cookie
  banners and ads are excluded by construction. We then convert that HTML to
  Markdown, preserving headings, links, lists, code blocks and images.

Usage:
    python scraper.py                 # scrape all published articles
    python scraper.py --max 30        # stop after 30 articles (min for the test)
    python scraper.py --out articles  # choose output directory
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://support.optisigns.com"
LOCALE = "en-us"
API_TEMPLATE = f"{BASE_URL}/api/v2/help_center/{LOCALE}/articles.json"
PER_PAGE = 100  # Zendesk max page size
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2.0  # seconds, multiplied per attempt

USER_AGENT = "OptiBot-MiniClone-Scraper/1.0 (+take-home-test)"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Article:
    id: int
    title: str
    url: str
    slug: str
    updated_at: str
    markdown: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str, fallback: str) -> str:
    """Turn an article title into a filesystem-safe slug."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)      # drop punctuation
    text = re.sub(r"[\s_-]+", "-", text)      # collapse whitespace to hyphens
    text = text.strip("-")
    return text or fallback


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def fetch_json(session: requests.Session, url: str) -> dict:
    """GET a URL with simple retry/backoff and return parsed JSON."""
    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_error = exc
            wait = RETRY_BACKOFF * attempt
            print(f"  ! request failed (attempt {attempt}/{RETRY_ATTEMPTS}): {exc}. "
                  f"retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {url}: {last_error}")


def iter_articles(session: requests.Session) -> Iterator[dict]:
    """Yield raw article dicts, following Zendesk pagination."""
    url = f"{API_TEMPLATE}?per_page={PER_PAGE}"
    page = 0
    while url:
        page += 1
        print(f"Fetching page {page}: {url}")
        data = fetch_json(session, url)
        for article in data.get("articles", []):
            yield article
        url = data.get("next_page")  # None when exhausted


# ---------------------------------------------------------------------------
# HTML -> Markdown normalization
# ---------------------------------------------------------------------------


def clean_html(html: str) -> str:
    """Strip elements that are noise even inside an article body."""
    soup = BeautifulSoup(html or "", "html.parser")

    # Remove scripts/styles and anything explicitly hidden.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Zendesk sometimes embeds request-a-demo / related-article widgets.
    for tag in soup.select("[class*='related'], [class*='callout'], iframe"):
        # Keep video iframes' link text but drop the embed itself.
        tag.decompose()

    return str(soup)


def html_to_markdown(html: str) -> str:
    """Convert cleaned article HTML into normalized Markdown."""
    cleaned = clean_html(html)
    markdown = md(
        cleaned,
        heading_style="ATX",          # use "# heading" style
        bullets="-",                  # consistent bullet char
        code_language="",             # keep fenced code blocks
        strip=["span"],               # unwrap noise spans
    )
    # Collapse 3+ blank lines into a single blank line.
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"


def build_article(raw: dict) -> Article:
    title = (raw.get("title") or "untitled").strip()
    article_id = int(raw["id"])
    slug = slugify(title, fallback=str(article_id))
    body_md = html_to_markdown(raw.get("body", ""))

    frontmatter = (
        "---\n"
        f"id: {article_id}\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"source_url: {raw.get('html_url', '')}\n"
        f"updated_at: {raw.get('updated_at', '')}\n"
        "---\n\n"
    )
    # The "Article URL:" line is embedded in the body (not just frontmatter) so
    # the assistant can read and cite it verbatim per the OptiBot system prompt.
    document = (
        f"{frontmatter}"
        f"# {title}\n\n"
        f"Article URL: {raw.get('html_url', '')}\n\n"
        f"{body_md}"
    )

    return Article(
        id=article_id,
        title=title,
        url=raw.get("html_url", ""),
        slug=slug,
        updated_at=raw.get("updated_at", ""),
        markdown=document,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def write_article(article: Article, out_dir: Path, used_names: set[str]) -> Path:
    """Write one article to <slug>.md, disambiguating slug collisions."""
    name = article.slug
    if name in used_names:
        name = f"{article.slug}-{article.id}"
    used_names.add(name)

    path = out_dir / f"{name}.md"
    path.write_text(article.markdown, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape OptiSigns support articles to Markdown.")
    parser.add_argument("--out", default="articles", help="output directory (default: articles)")
    parser.add_argument("--max", type=int, default=None,
                        help="maximum number of articles to save (default: all)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = make_session()
    used_names: set[str] = set()
    manifest: list[dict] = []
    saved = 0

    for raw in iter_articles(session):
        article = build_article(raw)
        path = write_article(article, out_dir, used_names)
        saved += 1
        manifest.append({
            "id": article.id,
            "title": article.title,
            "slug": path.stem,
            "file": path.name,
            "source_url": article.url,
            "updated_at": article.updated_at,
        })
        print(f"  [{saved}] {path.name}")

        if args.max is not None and saved >= args.max:
            print(f"Reached --max limit of {args.max}.")
            break

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nDone. Saved {saved} articles to '{out_dir}/'.")
    print(f"Manifest written to '{out_dir / 'manifest.json'}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
