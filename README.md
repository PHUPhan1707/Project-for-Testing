# OptiBot Mini-Clone — Step 1: Scrape ⇒ Markdown

Ingests articles from the OptiSigns support site
([support.optisigns.com](https://support.optisigns.com)) and normalizes each
one into clean Markdown, ready to feed a retrieval/QA bot.

## Approach

The support site is a **Zendesk Help Center**, which exposes a public JSON API:

```
https://support.optisigns.com/api/v2/help_center/en-us/articles.json
```

Using the API (instead of scraping rendered HTML pages) is both more reliable
and cleaner:

- The response gives the article **body as focused HTML** (title + content),
  so nav bars, footers, cookie banners and ads are excluded by construction.
- Pagination is built in (`next_page`), so we can walk **all** published
  articles (400+ at the time of writing).

Each article's HTML body is then:

1. Lightly cleaned with BeautifulSoup (drop `script`/`style`, related-article
   widgets, and raw `iframe` embeds).
2. Converted to Markdown with `markdownify`, preserving **headings, links,
   lists, images, and fenced code blocks**.
3. Wrapped with a small YAML frontmatter block (id, title, source URL,
   last-updated) and saved as `<slug>.md`.

## Setup

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```bash
# Scrape every published article into ./articles/
python scraper.py

# Scrape just the first 30 (minimum required by the test)
python scraper.py --max 30

# Choose a different output directory
python scraper.py --out output
```

Output:

- `articles/<slug>.md` — one clean Markdown file per article.
- `articles/manifest.json` — index of everything scraped (id, title, slug,
  source URL, updated_at).

## Output format

Each `.md` file looks like:

```markdown
---
id: 123456789
title: "How to add a new screen"
source_url: https://support.optisigns.com/hc/en-us/articles/123456789-...
updated_at: 2025-01-01T00:00:00Z
---

# How to add a new screen

...clean article body in Markdown...
```
