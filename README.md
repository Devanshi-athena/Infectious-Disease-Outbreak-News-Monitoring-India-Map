# Infectious Disease Outbreak News Monitoring & India Map

A news-based outbreak monitoring prototype. It is not an official surveillance or early-warning system. News coverage can be incomplete or delayed, and article counts do not measure disease incidence.

## Setup (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

```powershell
python -m outbreak_monitor.collector              # manual refresh (runs a backfill automatically if none has completed yet)
python -m outbreak_monitor.collector --backfill   # force a full backfill of the lookback window
python -m outbreak_monitor.scheduler              # run now, then every refresh_interval_minutes (Ctrl+C to stop)
python -m evaluation.recall_check                 # coverage check against Bing News (evaluation only)
pytest                                            # run tests
```

## Backfill and incremental runs

| | Backfill | Incremental |
|---|---|---|
| When | First run on an empty database, or `--backfill` | Every later run (scheduler or manual) |
| Covers | The whole `lookback_days` window (45 days) | What was published since the last run |
| Reads | RSS, news sitemaps, **archive sitemaps**, keyword pages **up to `backfill_max_pages` pages** | RSS, news sitemaps, **page 1** of keyword pages |
| Duration | Long (one-off) | About 1–2 minutes |

Incremental runs every 30 minutes are complete for any site with a 48-hour news sitemap or a feed that holds more than 30 minutes of articles. Missing a few runs does no harm, because the sitemaps reach back 48 hours.

## How collection works

Each source is collected in parallel, one worker per site. Each run has four steps:

1. **Discovery.** Reads the source's RSS feeds, sitemaps and keyword pages (see below). Only links on the source's own domain are kept.
2. **Pre-filter.** Skips links already stored or already rejected, and items published before `lookback_days`. RSS and sitemap items must contain a disease keyword in the headline, summary, sitemap keywords or URL. Links from keyword pages always continue to the next step.
3. **Extraction.** Downloads each remaining article page, if robots.txt allows it, and extracts the text, canonical URL, title and date with Trafilatura.
4. **Check and store.** Requires a keyword match on everything, including the article text, then removes duplicates (see below) and saves the article to SQLite. The disease names that matched go into `disease_tags`, and the methods that found the article go into `collection_method`.

**Duplicates** are detected by:
- a link that was already stored or rejected (the `seen_links` table)
- the page's canonical URL
- a fingerprint of the article text, which catches the same wire story on two URLs
- a near-identical headline from the same source (rapidfuzz similarity ≥ 95), which catches one story republished under two sections

**Reliability.** Network errors, timeouts, HTTP 429 and HTTP 5xx are retried up to 3 times with exponential backoff. A failed listing or page is logged and the run continues. A keyword page that doesn't exist (404 or 410) is skipped silently. The collector makes at most one request per second to any single site and identifies itself with a descriptive User-Agent.

**Monitoring.** Each run is recorded in `collection_runs` with times, status (`success`, `partial` or `failed`), counts and failed URLs, and that table supplies the last successful refresh time. Logs go to the console and to `data/logs/collector.log` (rotating, 5 MB × 3).

## Configuration

Changing either file needs no code changes. Edits take effect from the next run.

- **`config/diseases.yaml`** contains:
  - `keywords`: phrases that mark an article as being about each disease. Matching is whole-word and case-insensitive.
  - `search_terms`: the terms used to open each site's topic or search page.
  - `settings`: `lookback_days`, `refresh_interval_minutes`, `backfill_max_pages` and `country`.
- **`config/sources.yaml`** lists each site with its `domain` and any of `rss`, `sitemaps`, `archive_sitemaps` and `keyword_pages` (plus `article_pattern`). To pause a source, set `enabled: false`. The file's header comments explain each field.

## Sources and collection methods

There are four collection methods. robots.txt is checked before every request.

| Method | Tools | What it is | Used in |
|---|---|---|---|
| **RSS** | requests, feedparser | Feeds the publisher provides for automated reading, e.g. section, city and state feeds | every run |
| **News sitemap** | requests, BeautifulSoup4 (lxml) | XML for search crawlers: every article from the last ~48 hours, usually with headline, date and keywords | every run |
| **Archive sitemap** | requests, BeautifulSoup4 (lxml) | Daily, weekly or monthly XML lists of all articles (URLs only, matched on the URL slug) | backfill |
| **Keyword page** (HTML) | requests, BeautifulSoup4 | The site's own topic, tag or search page for a disease term, e.g. `/topic/dengue`. It lists articles the publisher tagged with that disease | page 1 every run; all pages in backfill |

| Source | RSS | News sitemap | Archive sitemap | Keyword page | Notes |
|---|---|---|---|---|---|
| The Hindu | 26 (health, national, states, cities, 6 states, 16 cities) | ✓ | — | — | `/topic/` redirects to the home page; `/search/` disallowed by robots.txt; no archive, so backfill reaches back only as far as the feeds (3–6 days, longer for some state feeds) |
| Times of India | India | today/yesterday (with titles) | weekly | `/topic/{term}/news` (1 page) | Topic pages load more articles with JavaScript only |
| The Indian Express | India, Health | ✓ | daily | `/about/{term}/` (1 page) | as above |
| Hindustan Times | India, Health | ✓ | monthly | `/topic/{term}/news/page-{n}` | Some links lack `https://`; these are handled |
| NDTV | latest, India, cities, south, top | — | — | — | Sitemaps and topic pages return 403. The "latest" feed (~1 day) makes incremental runs complete, but there is no backfill beyond what the feeds hold |
| India Today | home, India | ✓ | — | — | `/topic/` redirects to an index; no archive |
| News18 | India | ✓ (+ yesterday) | — | `/topics/{term}/page-{n}/` | |
| ThePrint | Health | ✓ | post sitemaps | `/page/{n}/?s={term}` | |
| Scroll | All articles | ✓ | — | `/topic/{term}` (1 page) | |
| ET HealthWorld | Top stories | 48-hour | monthly | `/tag/{term}/{n}` | |

The collector does not log in, bypass paywalls or crawl whole sites. If an article page can't be read (paywall, 403 or JavaScript-only), the article is stored with its listing metadata, and `extraction_status` records why.

## Database (SQLite, via SQLAlchemy)

| Table | Contents |
|---|---|
| `articles` | headline, source, canonical and listing URLs, collection method, publication and collection times, excerpt, article text, extraction status, content hash, disease tags. It also has columns for relevance, event status and location, which are filled in later phases. |
| `seen_links` | links that were downloaded but not stored (`no_keyword`, `too_old` or `duplicate`), so they are not downloaded again |
| `collection_runs` | one row per run: times, status, statistics and errors |

## Layout

- `outbreak_monitor/` – application code (`discovery.py` finds articles, `extractor.py` gets article text, `collector.py` runs the pipeline, `scheduler.py` runs it on a schedule)
- `config/` – disease keywords and news sources (YAML)
- `evaluation/` – coverage check (`recall_check.py`)
- `tests/` – pytest tests
- `data/` – SQLite database and logs (not committed)

## Known limitations

- Keyword matching is purely lexical. An article that mentions a disease in passing (e.g. a profile that says someone "contracted malaria") is tagged too. Relevance filtering, which separates outbreak reports from passing mentions, comes in the next phase.
- `seen_links` remembers rejected links permanently. After adding new keywords, delete its `no_keyword` rows so those links are re-checked.
- NDTV, The Hindu and India Today have no archive sitemaps or keyword pages we can use, so their backfill only reaches back as far as their RSS feeds. From the first run on, incremental runs cover them fully.
- Archive sitemaps have no headlines, so during backfill an article from those sites is found only if a keyword appears in its URL slug (or it is on a keyword page).
- `refresh_interval_minutes` is read when the scheduler starts, so changing it requires a restart.
