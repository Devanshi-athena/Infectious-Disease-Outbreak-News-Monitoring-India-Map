# Infectious Disease Outbreak News Monitoring & India Map

A news-based outbreak monitoring prototype. It is not an official surveillance or early-warning system. News coverage can be incomplete or delayed, and article counts do not measure disease incidence.

## Project status

| Area (requirements document) | Status |
|---|---|
| A. News collection and extraction | Done. The dashboard "Refresh" button comes with section D |
| B. Configurable keywords and relevance | Done. The control for setting the review status comes with section D |
| C. Location extraction and geocoding | Done |
| D. Interactive India map and dashboard | Next |
| Evaluation report, Docker, sample records | Later phases |

Disease keywords and news sources are placeholders until the final lists are supplied. Both are configuration files (see [Configuration](#configuration)).

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
python -m outbreak_monitor.reprocess              # re-tag, re-classify and re-locate all stored articles (normally automatic)
python -m outbreak_monitor.gazetteer              # rebuild reference/ place data from GeoNames (rarely needed)
python -m evaluation.recall_check                 # coverage check against Bing News (evaluation only)
python -m evaluation.location_eval                # location accuracy on the labelled sample
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

**Configuration changes.** The run records a fingerprint of `config/diseases.yaml`, `config/relevance.yaml`, `config/locations.yaml` and the reference place data. If any of them changes, the next run automatically:
- re-tags, re-classifies and re-locates every stored article (articles whose keywords were removed become `content_type = no_keyword`);
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

## Outbreak location

Every article gets the most specific outbreak location **the article supports**: state/UT → district → locality, with coordinates for map display. The code is in `outbreak_monitor/location.py`.

**Reference data** (`reference/`, built from [GeoNames](https://www.geonames.org), CC BY 4.0):
- `india_places.csv`: all 36 states/UTs, 763 districts and 6,584 towns with 5,000+ people. Each has coordinates and alternative spellings (Gurgaon → Gurugram, Calicut → Kozhikode, Trichy → Tiruchirappalli).
- `foreign_places.csv`: countries, capitals and first-level regions of all other countries.

`config/locations.yaml` adds headline abbreviations ("TN", "UP", "J&K"), extra spellings, display names and the geocoding settings.

**How a location is chosen**
1. **Find place mentions.** spaCy named-entity recognition (GPE/LOC) runs first, then matching against the reference data.
   - These are ignored: lower-case words, generic words ("East", "City"), and names that are part of a person's name.
   - A small town found only by the dictionary must also look like a place ("in X", "X village", or tagged by spaCy). This stops headline words such as "Bed" matching a town.
2. **Weigh each mention by where it appears:** headline 3; a sentence that mentions the article's disease 2 (3 in the first sentences); other lead sentences 1; elsewhere 0.5.
3. **Leave out places that are not the outbreak location:**
   - The **dateline** ("PUNE:", "Mumbai, Oct 20 (PTI) –") and **bylines** weigh 0. This is the publisher's or author's location.
   - Places named right after a **laboratory or institute** ("samples sent to NIHSAD, Bhopal") are down-weighted.
   - So are places inside a **hospital or college** name ("Coimbatore Medical College Hospital").
   - The dateline does count when the article reports the outbreak there: the outbreak sentences name no other place and say "in the city" / "here", or keep naming that place.
4. **Resolve ambiguous names** (Aurangabad: Bihar or Maharashtra), in this order:
   - the state the article names;
   - the higher administrative level (a district or state beats a same-name town elsewhere);
   - a clearly larger population (flagged for review);
   - otherwise unresolved.

   A small-town match in a sentence that names a district in another state is treated as a local village. A foreign country, capital or region (Dhaka, Pennsylvania) beats a same-name small Indian town.
5. **Decide the country.** If a foreign place named in the headline or outbreak sentences outweighs India, the article is `outside_india` (country = that country), and it is not mapped.
6. **Choose the level.**
   - **State:** the best-supported state.
   - **District:** the headline's district, or the single district the outbreak sentences point to. When the headline names both a state and a district, a district written as the event's place ("outbreak in Mon's Tizit") or named first wins.
   - **State-level instead of a district:** if the headline names only the state, or the outbreak sentences cover 3 or more districts.
   - **Locality:** a town in that district named in the outbreak sentences, or a village looked up with Nominatim (below).
7. **Coordinates** are those of the chosen level. A district-level report gets the district's coordinates; nothing more precise is invented.

**Geocoding** (geopy + [OpenStreetMap Nominatim](https://operations.osmfoundation.org/policies/nominatim/), data © OpenStreetMap contributors, ODbL):
- **When:** only for relevant articles, and only for a place named in the headline or outbreak sentences, written like a place ("in Kothrud", "Tizit village"), and missing from the reference data. Names with digits, codes, months or disease names are never sent.
- **Query:** "name, district, state, India". The answer must be a settlement, neighbourhood or administrative area (not a street or clinic) **in the same state**.
- **Usage policy:** at most 1 request per second, an identifying User-Agent, and every answer cached in the `geocode_cache` table, including "not found". Temporary failures are not cached.

**Stored per article:**
- `country`, `state`, `district`, `locality`, `latitude`, `longitude`
- `location_level` (state / district / locality / country)
- `location_text`: the article's own words for the place
- `location_confidence` (high / medium / low)
- `location_review` (true when a person should check it)
- `location_source` (reference / nominatim)
- `location_reasons`: why it was chosen
- `location_candidates`: every Indian place considered, with its weight

**Review flags** are set for:
- an ambiguous name in the headline;
- several states reported with similar weight;
- a spelling-variant or population-based match;
- a place not named in the headline or outbreak sentences;
- no location found.

**Accuracy** on the labelled sample in `evaluation/location_labels.csv` (60 relevant articles; AI-assisted labels, spot-checked by the project owner): **state/UT 95%, district level 92%**. Run `python -m evaluation.location_eval` to recompute.

## Configuration

Changing any of these files needs no code changes. Edits take effect from the next run.

- **`config/diseases.yaml`** contains:
  - `keywords`: phrases that mark an article as being about each disease. Matching is whole-word and case-insensitive.
  - `search_terms`: the terms used to open each site's topic or search page.
  - `settings`: `lookback_days`, `refresh_interval_minutes`, `backfill_max_pages` and `country`.
- **`config/relevance.yaml`** holds the cue words for relevance and event classification, plus the URL sections to exclude.
- **`config/locations.yaml`** holds place abbreviations and extra spellings, display names, and the geocoding settings (enable/disable, User-Agent, request delay).
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

## Sources, usage and compliance

**What the collector does with publishers' sites:**
- It reads only what each site publishes for automated reading (RSS feeds, sitemaps listed in its robots.txt) or as public pages (topic, tag and search pages).
- It checks robots.txt before every request (re-read daily).
- It makes at most one request per second per site and identifies itself with a descriptive User-Agent.
- It retries temporary errors with backoff and never retries 403/404.
- It does not log in, bypass paywalls, or work around blocks. Sites or sections that refuse automated access are left out; see the notes in the sources table.

**What is stored and shown:**
- The full article text is stored only for internal processing (keyword matching, classification and location).
- It is not republished. The dashboard will show the headline, source, date and short metadata, and link to the original article.

**Terms of use:** each publisher's terms of use and copyright policy have **not yet been formally reviewed**. Before production use or wider sharing, the project owner should confirm for each source in `config/sources.yaml` that automated access and internal text processing are permitted, and disable any source where they are not.

**External services and data:**

| Dependency | Used for | Terms / attribution |
|---|---|---|
| Publishers' RSS feeds, sitemaps and pages | Article collection | Each publisher's terms (see above) |
| [GeoNames](https://www.geonames.org) | `reference/` place data (built once, kept in the repo) | CC BY 4.0. Attribution: GeoNames |
| [OpenStreetMap Nominatim](https://operations.osmfoundation.org/policies/nominatim/) via geopy | Locating villages and neighbourhoods | Usage policy: max 1 request/second, identifying User-Agent, results cached. Data © OpenStreetMap contributors, ODbL |
| Bing News RSS | **Evaluation only** (`evaluation/recall_check.py`), never for collection | Microsoft/Bing terms |
| Map tiles | Dashboard map (next phase) | To be documented with the dashboard |

## Database (SQLite, via SQLAlchemy)

| Table | Contents |
|---|---|
| `articles` | headline, source, canonical and listing URLs, collection method, publication time (+ `date_source`) and collection time, excerpt, article text, extraction status, content hash, `disease_tags` and `matched_keywords`, classification (`relevance`, `content_type`, `event_status`, `relevant_diseases`, `relevance_reasons`), `story_id`, manual review (`relevance_status`), and location (`country`, `state`, `district`, `locality`, `latitude`, `longitude`, `location_level`, `location_text`, `location_confidence`, `location_review`, `location_source`, `location_reasons`, `location_candidates`) |
| `geocode_cache` | Nominatim answers per query ({} = not found), so each place is looked up once |
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
  - `gazetteer.py` builds and loads the reference place data; `location.py` finds the outbreak location
  - `collector.py` runs the pipeline
  - `reprocess.py` re-applies the configuration to stored articles
  - `scheduler.py` runs the pipeline on a schedule
- `config/`: diseases and keywords, relevance cues, locations, and news sources (YAML)
- `reference/`: India and foreign place reference data (from GeoNames)
- `evaluation/`: coverage check (`recall_check.py`), location accuracy (`location_eval.py` + `location_labels.csv`)
- `tests/`: pytest tests
- `data/`: SQLite database and logs (not committed)

## Known limitations

- Classification is rule-based. Headlines that don't name the disease (e.g. "Balaghat child deaths…") and borderline cases (a health department "sensitising the public") can be misclassified. These are what manual review is for.
- NDTV, The Hindu, India Today and Deccan Chronicle have no archive sitemaps (and the first three no keyword pages) we can use. Their backfill and catch-up only reach back as far as their RSS feeds and 48-hour sitemaps. Continuous scheduled runs cover them fully.
- Archive sitemaps have no headlines. During backfill, an article from those sites is found only if a keyword appears in its URL slug, or if it is on a keyword page.
- `refresh_interval_minutes` is read when the scheduler starts, so changing it requires a restart.
- Location rules are heuristics. National or regional pieces (e.g. a WHO statement issued in New Delhi) can get a state. A headline about a whole state whose body describes one district stays state-level. Where a patient lives vs where they were treated is only partly handled. Review flags and manual review cover these cases.
- The small spaCy model sometimes misses or mislabels place names. The reference-data match and review flags compensate, but a village that neither recognises is not found.
