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
python -m outbreak_monitor.collector              # one collection run (manual refresh)
python -m outbreak_monitor.collector --backfill   # force an in-depth read of every listing over lookback_days
python -m outbreak_monitor.scheduler              # run now, then every refresh_interval_minutes (Ctrl+C to stop)
python -m outbreak_monitor.reprocess              # re-tag and re-classify all stored articles (normally automatic)
python -m evaluation.recall_check                 # coverage check against Bing News (evaluation only)
pytest                                            # run tests
```

## What each run reads

Every run reads RSS feeds, news sitemaps (the last ~48 hours) and **page 1** of each keyword page. Some listings are also read **in depth**: archive sitemaps back to a cutoff date, and keyword pages up to `backfill_max_pages` pages. The run's mode depends on which listings are read in depth:

| Mode | When | Read in depth | Back to |
|---|---|---|---|
| **backfill** | a listing has never been read in depth: first run, a newly added source or search term, an archive that failed before; or `--backfill` | those listings (all of them with `--backfill`) | `lookback_days` (45 days) |
| **catch-up** | the last successful run is more than 36 hours old (e.g. the machine was off), so the 48-hour news sitemaps no longer cover the gap | all archives and keyword pages | the last successful run, minus 12 hours |
| **incremental** | otherwise (normal scheduled runs) | nothing | — |

The status of each listing (last success, last in-depth read, last error) is kept in the `listing_status` table. This is how a failed archive is retried automatically, and how a newly added source or search term is backfilled without any manual step.

## How collection works

Each source is handled by its own worker, in parallel. Each worker only contacts its own site. Each run has these steps:

1. **Discovery.** Reads the listings described above. Only links on the source's own domain, and (for sitemaps and keyword pages) only article URLs matching `article_pattern`, are kept.
2. **Pre-filter.** Skips links already stored or rejected, and items published before `lookback_days`. RSS and sitemap items must contain a disease keyword in the headline, summary, the publisher's sitemap keywords or the URL. These fields are only used to *find* candidates. Links from keyword pages always continue.
3. **Extraction.** Downloads each remaining article page, if robots.txt allows it, and extracts the text, canonical URL, title and date with Trafilatura.
4. **Store.** Each source is stored as soon as its worker finishes:
   - **Headline:** the page title for keyword-page and archive links, otherwise the listing headline. Site suffixes such as "| Pune News - The Times of India" are removed.
   - **Disease tags:** taken **only from the headline and the article text** (or the RSS summary if there is no text). `matched_keywords` records exactly which keywords matched and where, e.g. `{"Dengue": {"headline": ["dengue"], "text": ["dengue cases"]}}`. An article with no configured keyword in its headline or text is not stored.
   - **Date:** the listing's date (`date_source = listing`), otherwise the date on the page (`date_source = page`). All dates are UTC.
   - **Duplicates are not stored:**
     - a known canonical URL;
     - an identical text fingerprint (e.g. the same wire story on two URLs);
     - a headline from the **same source** that is ≥95% similar, has **the same figures**, and was published within 3 days.

     A daily update such as "cases rise to 48" is therefore new, not a duplicate of "cases rise to 40".
   - **Same story, other publisher:** a ≥90% similar headline with the same figures, published within 3 days, is stored but given the same `story_id`. The map can then show one marker listing every source.
   - **Classification:** see the next section.

**Download failures.**

| Problem | `extraction_status` | What happens |
|---|---|---|
| Temporary (network error, timeout, HTTP 429/5xx, robots.txt unreadable) | `failed` | Retried in the next runs, up to 3 attempts. After that, the article is stored with its listing data only if its headline contains a keyword. |
| Permanent (robots.txt disallows the page) | `blocked` | The article is stored with its listing data if its headline or summary contains a keyword. |
| Permanent (HTTP 403/404) | `unavailable` | As above. |

**Reliability.**
- Requests are retried up to 3 times with exponential backoff.
- Each response may take at most 60 s in total. Responses slower than 10 s are logged.
- At most one request per second goes to any single site (thread-safe).
- robots.txt is re-read daily. If it can't be read, it is retried after 10 minutes.
- If one file of a sitemap index fails, the other files are still used.
- An error on one article is logged and the run continues.
- SQLite waits up to 30 s for a lock held by another process.

**Configuration changes.** The run records a fingerprint of `config/diseases.yaml` and `config/relevance.yaml`. If either file changes, the next run automatically:
- re-tags and re-classifies every stored article (articles whose keywords were removed become `content_type = no_keyword`);
- re-checks links that were rejected for having no keyword;
- backfills new search terms.

**Monitoring.** Each run is recorded in `collection_runs`: mode, times, status (`success`, `partial` or `failed`), counts, failed URLs and the config fingerprint. This table also gives the last successful refresh time. Logs go to the console and to `data/logs/collector.log` (rotating, 5 MB × 3).

## Relevance and event classification

Every stored article is classified when it is collected. The classifier is rule-based and explainable: it uses spaCy sentence splitting, regular expressions and the cue words in `config/relevance.yaml`, and no paid API or language model.

1. **Which diseases are central?** A disease is central if it appears in the headline, or in 3 or more sentences, or in the lead (the first 3 sentences) and 2 or more sentences. If no disease is central, the article is a `passing_mention`.
2. **What do the sentences that mention a central disease say?** Cues are only looked for in these sentences, so a passing remark elsewhere in the article doesn't count. The classifier looks for:
   - **case counts**, e.g. "40 new cases", "87 jawans hospitalised", "tally rises to 70", "cases jump 31%". Years and names such as "COVID-19" or "H1N1" are not counted.
   - **event cues**, e.g. outbreak, deaths, surge, confirmed
   - **response cues**, e.g. fogging, surveillance, alert, drive
3. **What kind of article is it?** The headline is checked first.

| `content_type` | Meaning | `relevance` |
|---|---|---|
| `outbreak_report` | cases, deaths or an outbreak are reported | relevant |
| `public_health_response` | a response to disease in an area (fogging, alerts, drives, preparedness) | relevant |
| `research` | studies, vaccines, drugs, trials | irrelevant |
| `awareness` | explainers, tips, symptoms; question headlines without a response cue | irrelevant |
| `passing_mention` | no disease is central | irrelevant |
| `other` | central but no event or response, or an excluded URL section (sports, entertainment, opinion…) | irrelevant |
| `no_keyword` | after a keyword change, the article no longer contains any configured keyword | irrelevant |

4. **What is the event status?** For relevant articles only:
   - `historical`: the headline points to the past (e.g. "years ago", or a past year) and the lead doesn't mark the report as current
   - `suspected`: "suspected"/"probable" appears in the headline, or in the event sentences when no case counts are given
   - `new`: a current report with cases
   - `unclear`: a response with no case report

The classifier also stores `relevant_diseases` (the diseases the event is about) and `relevance_reasons` (why it decided, e.g. `"Dengue event: '12 new dengue cases'"`).

**Manual review.** `relevance_status` (`pending`, `relevant` or `irrelevant`) is reserved for a person to set and is never changed by the classifier, including when articles are re-classified. The interface for setting it will come with the dashboard.

**Outbreaks abroad** (e.g. US measles) are classified as outbreak reports. Keeping only events in India is handled by location extraction in the next phase.

## Configuration

Changing any of these files needs no code changes. Edits take effect from the next run.

- **`config/diseases.yaml`** contains:
  - `keywords`: phrases that mark an article as being about each disease. Matching is whole-word and case-insensitive.
  - `search_terms`: the terms used to open each site's topic or search page.
  - `settings`: `lookback_days`, `refresh_interval_minutes`, `backfill_max_pages` and `country`.
- **`config/relevance.yaml`** holds the cue words for relevance and event classification, plus the URL sections to exclude.
- **`config/sources.yaml`** lists each site with its `domain` and any of `rss`, `sitemaps`, `archive_sitemaps` (+ `archive_child_pattern`) and `keyword_pages` (+ `article_pattern`). To pause a source, set `enabled: false`. A newly added source is backfilled automatically. The file's header comments explain each field.

## Sources and collection methods

There are four collection methods. robots.txt is checked before every request.

| Method | Tools | What it is | Used in |
|---|---|---|---|
| **RSS** | requests, feedparser | Feeds the publisher provides for automated reading, e.g. section, city and state feeds | every run |
| **News sitemap** | requests, BeautifulSoup4 (lxml) | XML for search crawlers: every article from the last ~48 hours, usually with headline, date and keywords | every run |
| **Archive sitemap** | requests, BeautifulSoup4 (lxml) | Daily, weekly or monthly XML lists of all articles (URLs only, matched on the URL slug) | backfill / catch-up |
| **Keyword page** (HTML) | requests, BeautifulSoup4 | The site's own topic, tag or search page for a disease term, e.g. `/topic/dengue`. It lists articles the publisher tagged with that disease | page 1 every run; more pages in backfill / catch-up |

| Source | RSS | News sitemap | Archive sitemap | Keyword page | Notes |
|---|---|---|---|---|---|
| The Hindu | 26 (health, national, states, cities, 6 states, 16 cities) | ✓ | — | — | `/topic/` redirects to the home page; `/search/` disallowed by robots.txt; no archive, so backfill reaches back only as far as the feeds (3–6 days, longer for some state feeds) |
| Times of India | India | today/yesterday (with titles) | weekly | `/topic/{term}/news` (1 page) | Topic pages load more articles with JavaScript only |
| The Indian Express | India, Health | ✓ | daily | `/about/{term}/` (1 page) | as above |
| Hindustan Times | India, Health | ✓ | monthly | `/topic/{term}/news/page-{n}` | Some links lack `https://`; these are handled |
| NDTV | latest, India, cities, south, top | — | — | — | Sitemaps and topic pages return 403. The "latest" feed (~1 day) makes incremental runs complete, but there is no backfill beyond what the feeds hold |
| India Today | home, India | ✓ | — | — | `/topic/` redirects to an index; no archive |
| News18 | India | ✓ (+ yesterday) | — | `/topics/{term}/page-{n}/` | |
| Deccan Chronicle | All (~500 items, ~2 days) | ✓ | — | `/tags/{term}` (1 page) | Replaced ThePrint, whose server repeatedly timed out. No archive, so backfill reaches back ~2 days plus the tag pages |
| Scroll | All articles | ✓ | — | `/topic/{term}` (1 page) | |
| ET HealthWorld | Top stories | 48-hour | monthly | `/tag/{term}/{n}` | |

The collector does not log in, bypass paywalls or crawl whole sites.

## Database (SQLite, via SQLAlchemy)

| Table | Contents |
|---|---|
| `articles` | headline, source, canonical and listing URLs, collection method, publication time (+ `date_source`) and collection time, excerpt, article text, extraction status, content hash, `disease_tags` and `matched_keywords`, classification (`relevance`, `content_type`, `event_status`, `relevant_diseases`, `relevance_reasons`), `story_id`, manual review (`relevance_status`) and location columns (filled in the next phase) |
| `seen_links` | links checked but not stored: `no_keyword`, `too_old`, `duplicate`, or `fetch_failed` (with `attempts`, retried up to 3 times) |
| `listing_status` | per listing: last success, last in-depth read, last error |
| `collection_runs` | one row per run: mode, times, status, config fingerprint, statistics and errors |

New columns are added to an existing database automatically.

## Layout

- `outbreak_monitor/`: application code
  - `discovery.py` finds articles
  - `extractor.py` gets article text
  - `processing.py` cleans headlines, tags keywords and matches stories
  - `relevance.py` classifies articles
  - `collector.py` runs the pipeline
  - `reprocess.py` re-applies the configuration to stored articles
  - `scheduler.py` runs the pipeline on a schedule
- `config/`: diseases and keywords, relevance cues, and news sources (YAML)
- `evaluation/`: coverage check (`recall_check.py`)
- `tests/`: pytest tests
- `data/`: SQLite database and logs (not committed)

## Known limitations

- Classification is rule-based. Headlines that don't name the disease (e.g. "Balaghat child deaths…") and borderline cases (a health department "sensitising the public") can be misclassified. These are what manual review is for.
- NDTV, The Hindu, India Today and Deccan Chronicle have no archive sitemaps (and the first three no keyword pages) we can use. Their backfill and catch-up only reach back as far as their RSS feeds and 48-hour sitemaps. Continuous scheduled runs cover them fully.
- Archive sitemaps have no headlines. During backfill, an article from those sites is found only if a keyword appears in its URL slug, or if it is on a keyword page.
- `refresh_interval_minutes` is read when the scheduler starts, so changing it requires a restart.
