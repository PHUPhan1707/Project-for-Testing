# OptiBot Mini-Clone

A small pipeline that clones OptiSigns' support bot, "OptiBot":

1. **Step 1 — Scrape ⇒ Markdown:** ingest every article from
   [support.optisigns.com](https://support.optisigns.com) and normalize it into
   clean Markdown.
2. **Step 2 — Build the assistant & load the vector store:** upload those docs
   to a **Gemini File Search store** via the API and wire them to the OptiBot
   assistant that answers questions with citations. (An OpenAI variant is also
   included.)

## Setup

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

---

# Step 1 — Scrape ⇒ Markdown

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
   last-updated) and saved as `<slug>.md`. An explicit `Article URL:` line is
   also embedded in the body so the assistant can cite it verbatim (Step 2).

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

Article URL: https://support.optisigns.com/hc/en-us/articles/123456789-...

...clean article body in Markdown...
```

At the time of writing this pulls **404 articles**.

---

# Step 2 — Build the assistant & load the vector store

This project uses **Google Gemini** and its **File Search** tool — Gemini's
managed RAG / "vector store" equivalent. File Search chunks, embeds, and indexes
each document so **OptiBot** can ground its answers on the OptiSigns support
docs. Gemini was chosen because it has a genuine **free tier** (no prepaid
balance required); an OpenAI variant is also included (see the end of this doc).

> Gemini File Search pricing: storage and query-time embeddings are free; only
> first-time indexing is billed on paid tiers, and it stays within the free
> tier's limits for this dataset.

## Prerequisite: API key (free)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and
   click **Create API key** (a free Google account is enough).
2. Export it:

```powershell
# Windows PowerShell
$env:GEMINI_API_KEY = "your-key-here"
```
```bash
# macOS/Linux
export GEMINI_API_KEY="your-key-here"
```

## 1. Upload docs to the File Search store (mandatory, API-only)

```bash
python gemini_upload_to_store.py
# small test batch first (recommended on the free tier's 15 req/min limit):
python gemini_upload_to_store.py --max 40
```

This creates a File Search store named `OptiBot Support Docs`, uploads every
`articles/*.md` file with our chunking config, logs the file/chunk counts, and
writes the store name to `gemini_store.json`.

Example log:

```
Files to embed        : 404
Total tokens (approx) : 569,891
Estimated chunks      : ~1,500  (white-space: max=512, overlap=100 — Gemini limit)
...
Files embedded  : 404 (failed: 0)
Chunks embedded : ~1,034 (estimated, white-space strategy)
File Search store: fileSearchStores/...
```

> Note: on the free tier (~15 requests/min) uploading all 404 files takes a
> while because each file is uploaded + indexed individually; the script
> throttles and retries automatically. Use `--max` for a quick run.

## 2. Set up the OptiBot assistant

Gemini has no persistent "assistant" object — the **system prompt** and the
**File Search tool** are supplied on each request. There are two ways to run it:

**Option A — script (reproducible):** `gemini_ask_optibot.py` already embeds the
verbatim system prompt and attaches the store (see step 3).

**Option B — Google AI Studio UI (for the screenshot):**

1. Go to [aistudio.google.com](https://aistudio.google.com) → new prompt.
2. Paste the system prompt below into **System instructions** (verbatim).
3. Enable the **File Search** tool and select the `OptiBot Support Docs` store
   created in step 1.

### System prompt (verbatim)

```
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```

## 3. Sanity check

Ask the required test question:

```bash
python gemini_ask_optibot.py                    # "How do I add a YouTube video?"
python gemini_ask_optibot.py "your own question here"
python gemini_ask_optibot.py --model gemini-2.5-pro   # if the default model errors
```

It prints the answer plus the documents File Search grounded on. For the
deliverable, ask the same question in **AI Studio** and screenshot the answer
**with citations** into `screenshots/`.

## Chunking strategy

We use Gemini's **white-space** chunking with:

| Parameter | Value | Why |
|-----------|-------|-----|
| `max_tokens_per_chunk` | **512** | Gemini's hard limit for File Search chunking (OpenAI allows up to 4096). |
| `max_overlap_tokens` | **100** | ~20% overlap so step lists aren't cut mid-instruction. |

Support articles are short and highly structured (headings + numbered steps), so
a 512-token window with modest overlap indexes each article in a few chunks.
The 404 docs (~570K tokens) produce **~1,500 chunks** (estimated).

**On the reported chunk count:** neither Gemini nor OpenAI returns an exact
server-side chunk total, so the upload scripts compute it locally with
`tiktoken` using the **same** chunking parameters we send to the API — a close,
deterministic estimate rather than a server-reported number.

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | Step 1 — scrape support site to Markdown |
| `gemini_upload_to_store.py` | **Step 2 — upload docs to the Gemini File Search store (API)** |
| `gemini_ask_optibot.py` | **Step 2 — sanity-check query against OptiBot (Gemini)** |
| `gemini_store.json` | Saved File Search store name + counts |
| `requirements.txt` | Pinned dependencies |

## Alternative: OpenAI variant

Equivalent scripts using an OpenAI Vector Store are also included (they need a
paid `OPENAI_API_KEY`):

| File | Purpose |
|------|---------|
| `upload_to_vector_store.py` | Upload docs to an OpenAI Vector Store |
| `create_assistant.py` | Create the OptiBot assistant (Assistants API) |
| `ask_optibot.py` | Sanity-check query against the OpenAI assistant |

Run: `python upload_to_vector_store.py` → `python create_assistant.py` →
`python ask_optibot.py`.

---

# Step 3 — Daily job (scrape + delta upload)

`main.py` wraps the scraper + uploader into a single job that runs once and
exits — meant to be run **daily** by a scheduler.

What each run does:

1. **Re-scrapes** every published article.
2. **Detects new / updated / unchanged** articles via a SHA-256 **content
   hash**.
3. **Uploads only the delta** (new + changed) to the Gemini File Search store.
4. **Logs counts:** `added`, `updated`, `skipped` (and writes `last_run.json`).

**Stateless delta:** the previous hash for each article is stored *inside* the
File Search store as per-document `custom_metadata` (`article_id` +
`content_hash`). So the job needs no local database and works in ephemeral
containers — on each run it reads the store to decide what changed.

## Run locally

```powershell
# 1. Configure your key
copy .env.sample .env      # then edit .env and set GEMINI_API_KEY
# (or: $env:GEMINI_API_KEY = "your-key")

# 2. Run the job once
python main.py

# Quick test with a few articles:
$env:MAX_ARTICLES = "40"; python main.py
```

Example output:

```
Reusing existing store: fileSearchStores/...
Documents already indexed: 404
Scraped 404 articles -> 2 new, 3 updated, 399 unchanged
  + added   how-to-use-new-app.md
  ~ updated accepted-payment-methods.md
============================================================
DELTA SYNC COMPLETE  (78.2s)
  added   : 2
  updated : 3
  skipped : 399
  failed  : 0
```

## Run with Docker

```bash
docker build -t optibot-sync .
docker run --rm -e GEMINI_API_KEY=your-key optibot-sync   # runs once, exits 0
```

## Daily schedule + logs

Scheduled via **GitHub Actions** (`.github/workflows/daily-sync.yml`) — runs at
06:00 UTC daily and on manual dispatch. Add your key as a repo secret named
`GEMINI_API_KEY` (**Settings → Secrets and variables → Actions**).

- **Job logs:** repo **Actions** tab → *Daily support-docs sync* → latest run.
  <!-- TODO: paste the link to a real run here, e.g.
  https://github.com/<user>/<repo>/actions/runs/<id> -->
- **Last-run artifact:** `last_run.json` is uploaded on every run.

> Any other scheduler works too (Render Cron Job, Railway cron, Fly.io, a
> cron'd `docker run`, etc.) — point it at the same image/command with the
> `GEMINI_API_KEY` env var.

## Screenshot

See `screenshots/` for OptiBot answering *"How do I add a YouTube video?"* with
cited `Article URL:` lines.

<!-- Example:
![OptiBot answering with citations](screenshots/youtube-answer.png)
-->

## Deliverables checklist

- [x] GitHub repo with a cryptic name (not "optisigns…") and clear commits
- [x] No hard-coded keys — `.env.sample` + env vars / CI secret
- [x] `Dockerfile` — `docker run -e GEMINI_API_KEY=... ` runs once and exits 0
- [x] `main.py` daily job: re-scrape, hash delta, upload only changes, log
      added/updated/skipped
- [x] Scheduled daily (GitHub Actions) with logs + `last_run.json` artifact
- [ ] Screenshot of the assistant answering with cited URLs (add to `screenshots/`)

## Step 3 files

| File | Purpose |
|------|---------|
| `main.py` | Daily scrape + delta-upload job (runs once, exits) |
| `Dockerfile` | Container that runs `main.py` once |
| `.dockerignore` | Keeps the image small |
| `.env.sample` | Template for env vars (copy to `.env`) |
| `.github/workflows/daily-sync.yml` | Daily schedule + job logs + artifact |
| `last_run.json` | Written each run: added/updated/skipped counts |
