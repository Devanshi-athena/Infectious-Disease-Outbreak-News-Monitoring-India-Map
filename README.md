# Infectious Disease Outbreak News Monitoring & India Map

A news-based outbreak monitoring prototype. It is not an official surveillance or early-warning system. News coverage can be incomplete or delayed, and article counts do not measure disease incidence.

## Project status

| Area (requirements document) | Status |
|---|---|
| A. News collection and extraction | Done. The dashboard "Refresh" button comes with section D |
| B. Configurable keywords and relevance | Done. The control for setting the review status comes with section D |
| C. Location extraction and geocoding | Done |
| D. Interactive India map and dashboard | Done (includes the Refresh button and review controls) |
| E. Data storage (SQLite, PostgreSQL-ready) | Done |
| Official sources (manager's request) | IDSP weekly outbreak reports (NCDC) integrated as a separate layer; other official sources explored (see below) |
| Evaluation report, Docker, sample records | Later phases |

The disease configuration is the list agreed with the project manager: 19 diseases in 5 groups. Sources: the 10 news sites plus one official source (IDSP weekly outbreak reports), 11 in total. Both are configuration files (see [Configuration](#configuration)) and will be refined using the relevance and location results.

## Monitored diseases

| Group | Diseases (each with alternative names and spellings in `config/diseases.yaml`) |
|---|---|
| Vector-borne | Dengue · Malaria · Chikungunya · Japanese Encephalitis / AES (incl. "brain fever", "chamki bukhar", Chandipura) |
| Water- and food-borne | Cholera · Acute Diarrhoeal Disease (incl. gastroenteritis, food poisoning) · Typhoid · Hepatitis A / E (incl. "jaundice cases/outbreak/deaths"; "jaundice" alone also matched liver disease and newborn care) |
| Emerging and zoonotic | Nipah · Leptospirosis ("rat fever") · Scrub Typhus · Kyasanur Forest Disease ("monkey fever") · Rabies · Other zoonotic outbreaks (bird flu/H5N1, anthrax, brucellosis, bubonic/pneumonic plague, CCHF, mpox) |
| Respiratory and vaccine-preventable | Influenza (incl. H1N1, H3N2, swine flu) · COVID-19 · Measles (incl. rubella) · Diphtheria |
| Other epidemic-prone | Unexplained fever / deaths / clusters ("mystery fever", "unknown disease", "unexplained deaths", "fever outbreak"…) |

Outbreak words such as "outbreak", "surge in cases", "cluster", "suspected cases" and "deaths" are **not** disease keywords. They are relevance cues in `config/relevance.yaml`, looked for only in sentences that already name a configured disease. So they decide *whether* an article about a disease reports an outbreak, but never cause an article to be collected on their own.

**Kept for verification**, for every article:
- the source URL (canonical, plus the listing link);
- the headline and the extracted article text (or summary);
- the publication date and where it came from;
- the reported location (with the article's own words, the reasons and the confidence);
- the disease tags with the exact keywords matched;
- the relevance and event classification with its reasons.

All of these appear in the dashboard's article register, which can be downloaded as CSV from the table's toolbar. Official records keep their IDSP unique ID, the weekly PDF's URL, and every column of the report.

## Official sources

| Source | What is available | Status |
|---|---|---|
| **NCDC: IDSP Weekly Outbreak Reports** (ncdc.mohfw.gov.in → "Weekly Outbreaks") | One PDF per week since 2009, with a table per outbreak: state, district, disease, cases, deaths, start and report dates, status, and action taken. About 48 outbreaks a week. The PDF tables can be parsed reliably | **Integrated** (`outbreak_monitor/official.py`, table `official_reports`, its own map layer and tab). Published about 2 months after the reporting week (week 34, 17–23 Aug 2026, was the latest on 27 Oct). The latest `weeks` reports (default 12) are kept |
| NCDC announcements | Health advisories as PDFs (e.g. leptospirosis, monsoon) | Advisories, not outbreak reports; low value for the map |
| IDSP (idsp.mohfw.gov.in) | Site not reachable (connection timeout) | Reports now appear on NCDC |
| MoHFW (mohfw.gov.in) | Pages are built in the browser with JavaScript; no feed or machine-readable listing | Not accessible without a full browser |
| PIB press releases (pib.gov.in RSS) | RSS works, but it returns Hindi releases from all ministries, whatever language parameter is used | Not useful yet; an English health-ministry feed was not found |
| State health departments (e.g. Kerala DHS) | Advisories and bulletins as PDFs, partly in regional languages; formats vary by state | Case by case; no common machine-readable format |

**How the IDSP reports are read.** Each run lists the weekly PDFs on the NCDC page and reads only those not stored yet (robots.txt is respected; one request per second).
- `pdfplumber` extracts the tables. Every outbreak starts at its unique ID (e.g. `AS/BAJ/2026/34/1369`); text that wraps over several lines or pages is joined back to that record.
- Columns are mapped by their header text, per table, since the main table and the "reported late" table differ.
- Dates and status are read by their content, since narrow layouts push them into neighbouring columns. A date whose year was cut off by a page break takes the year from the report ID.
- A row whose state, district and disease moved one column is repaired from what the values are (a state name in the disease column, a disease appended to the district).
- States and districts are matched against the GeoNames reference data: exact names, then spelling variants (no partial matches, so "Balodabazar" never becomes "Balod"). Unknown districts are mapped at the state centre.
- Diseases are mapped to the configured diseases from the report's disease column only, never from the comment (which also names diseases that were ruled out). Other diseases (chickenpox, mumps, GBS…) are kept under their reported name.
- Values are stored as printed: a few reports list a start date after the reporting date, or a comment that describes another district. These are errors in the source PDFs and are not "corrected".

Checked on six weekly reports (245 outbreaks, 2025–2026): 244 complete (one has its status cut off by a page break and is left empty, not guessed), 243 located to the district, 1 at state level, and 1 not located (a row where the PDF merges two outbreaks without a second ID; it is kept, but not mapped).

## Setup (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

```powershell
streamlit run app.py                              # dashboard at http://localhost:8501
python -m outbreak_monitor.collector              # one collection run (news, then official reports)
python -m outbreak_monitor.official               # official reports only
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

## Dashboard

`streamlit run app.py` opens the dashboard at http://localhost:8501. It is built with Streamlit, Folium (Leaflet) and streamlit-folium. The page is in `app.py`; the data logic is in `outbreak_monitor/dashboard_data.py`.

**Sidebar**
- **Show:**
  - *Relevant outbreak reports* (default);
  - *Relevant, location needs review*;
  - *All collected articles*.
- **Filters:**
  - disease group (choosing a group without a disease means every disease in it) and disease;
  - state/UT, source, and publication date (default: the lookback window);
  - "include reports outside India" (these appear in the list only);
  - "show official IDSP reports" (the disease and state filters apply to them; the date filter does not, since they are published about two months late).
- **Refresh now:** runs a collection immediately. The scheduler keeps running separately.

**Look:** a classic "atlas" style. Theme colours, fonts and borders are set in `.streamlit/config.toml`, the small extra CSS is at the top of `app.py`. The palette is ivory and parchment, with navy accents, serif headings (Georgia) and a sans-serif body.

**Key figures:** matching articles, stories on the map, distinct reported locations, and the last successful refresh (IST).

**Map** (India in focus, markers only, no heatmap):
- **One marker per story.** The same report from several publishers shares a marker, and its popup lists every source.
- **Marker colour = event status:** crimson = new, amber = suspected, grey = historical, steel blue = unclear. An on-map legend gives the labels, and popups always state the status in text too.
- **Popup:** a card with disease and event status, headline, reported location (with level, confidence and any review flag), publication date (IST), and links to each source's original article.
- **India only:** everything outside India's official boundary is covered in the page colour, and India is outlined in navy. Panning is limited to India, and fractional zoom lets India fill the frame.
  - The boundary is `reference/india_boundary.geojson`: the official boundary of India as per the Survey of India, including all of Jammu & Kashmir and Ladakh, compiled by the [DataMeet India community](https://github.com/datameet/maps/tree/master/Country) (`india-composite.geojson`, CC0).
  - It is simplified to about 1 km (85 KB). `python -m outbreak_monitor.gazetteer` rebuilds it.
- **Basemap:** OpenStreetMap tiles. "Classic (muted)" is the default (toned down with CSS); "Street map (full colour)" is available from the layer button. There is also a fullscreen button. CARTO's light basemap now needs an API key, so it is not used.

**Beside the map:** the four latest reports as cards, and "Most reported states". These are story counts, labelled as not being case numbers.
- **Placement:** markers sit at the coordinates of the level the article supports (district centre, state centre, or locality). Nearby markers cluster when zoomed out.
- **Zoom:** the map zooms to the filtered results; *Zoom to district* goes straight to a district.
- Reports outside India are never mapped.
- **Official reports** are green squares at the district centre (a separate layer in the layer button). Their popup gives disease, cases and deaths, dates, status, the IDSP ID and a link to the weekly PDF.

**Official reports** (second tab) lists every matching IDSP record with all its columns and a link to the PDF.

**Article register** (first tab below the map; the third tab, "About the data", summarises the method and attributions) shows every matching article: date, headline, disease, reported location, level, confidence, a ⚠️ review flag, event status, type, source, and a link to the original. The **Your review** column sets `relevance_status` (pending / relevant / irrelevant); save with *Save review changes*. A review decision overrides the automatic classification everywhere in the dashboard and is never changed by the code.

**Access:** `.streamlit/config.toml` restricts the server to `localhost`, because the page can save reviews and start collection runs. Expose it on a network only behind proper access control. The Streamlit "Deploy" button is hidden.

The article count is not a measure of disease incidence or outbreak severity; the page says so at the top.

## Data model and migration

SQLite (`data/outbreaks.db`) is used for the prototype, through SQLAlchemy with portable column types (no SQLite-specific SQL).

**The document's fields** (all in the `articles` table):

| Field | Column(s) |
|---|---|
| article_id | `article_id` (primary key) |
| headline | `headline` |
| source_name | `source_name` |
| article_url | `article_url` (canonical, unique); `link_url` is the link as listed |
| published_at / collected_at | `published_at` (UTC, with `date_source`), `collected_at` (UTC) |
| article_text or excerpt | `article_text`, `excerpt` |
| disease_tags | `disease_tags` (+ `matched_keywords`, `relevant_diseases`) |
| state / district / locality | `state`, `district`, `locality` (+ `country`, `location_level`, `location_text`) |
| latitude / longitude | `latitude`, `longitude` |
| location_confidence | `location_confidence` (+ `location_review`, `location_reasons`) |
| relevance_status | `relevance_status` (manual review: pending / relevant / irrelevant; the automatic result is in `relevance`) |
| event_status | `event_status` (new / suspected / historical / unclear) |

**Official records** are in `official_reports` (one row per IDSP unique ID): `report_id`, `source_name`, `report_url`, `report_year`, `report_week`, `state`, `district`, `disease_raw` (as printed), `diseases` (configured diseases it maps to), `cases`, `deaths`, `onset_date`, `reported_date`, `status`, `action_taken`, `latitude`, `longitude`, `location_level`, `location_confidence`.

**Moving to PostgreSQL/PostGIS:**
1. Install a driver (`pip install "psycopg[binary]"`).
2. Set `OUTBREAK_DB_URL=postgresql+psycopg://user:password@host/dbname`. Every command and the dashboard then use it, and the tables are created automatically.
3. To copy existing data, read each table from SQLite and insert it into PostgreSQL. The column types (text, integer, float, boolean, timestamp, JSON) map directly.
4. **For PostGIS (optional):** run `CREATE EXTENSION postgis;`, then `ALTER TABLE articles ADD COLUMN geom geometry(Point, 4326);` and `UPDATE articles SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326);`. This enables spatial queries such as "reports within 50 km" or joins with district boundaries.

## Configuration

Changing any of these files needs no code changes. Edits take effect from the next run.

- **`config/diseases.yaml`** contains:
  - `category`: the disease group, used for the dashboard's "Disease group" filter.
  - `keywords`: phrases that mark an article as being about each disease. Matching is whole-word and case-insensitive.
  - `search_terms`: the terms used to open each site's topic or search page.
  - `settings`: `lookback_days`, `refresh_interval_minutes`, `backfill_max_pages` and `country`.
- **`config/relevance.yaml`** holds the cue words for relevance and event classification, crime words that rule a headline out (a "food poisoning" that turns out to be murder), and the URL sections to exclude.
- **`config/locations.yaml`** holds place abbreviations and extra spellings, display names, and the geocoding settings (enable/disable, User-Agent, request delay).
- **`config/sources.yaml`** lists each site with its `domain` and any of `rss`, `sitemaps`, `archive_sitemaps` (+ `archive_child_pattern`) and `keyword_pages` (+ `article_pattern`). To pause a source, set `enabled: false`. A newly added source is backfilled automatically. The file's header comments explain each field. Its `official_reports` section lists official report series (`listing`, `weeks` to keep, `enabled`).

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
| [NCDC IDSP weekly outbreak reports](https://ncdc.mohfw.gov.in/) | Official outbreak records | Public Government of India reports; credited on the dashboard with a link to each PDF |
| [GeoNames](https://www.geonames.org) | `reference/` place data (built once, kept in the repo) | CC BY 4.0. Attribution: GeoNames |
| [DataMeet India boundaries](https://github.com/datameet/maps/tree/master/Country) | `reference/india_boundary.geojson`: the India-only map outline (official boundary as per the Survey of India) | CC0 (public domain); credited on the dashboard |
| [OpenStreetMap Nominatim](https://operations.osmfoundation.org/policies/nominatim/) via geopy | Locating villages and neighbourhoods | Usage policy: max 1 request/second, identifying User-Agent, results cached. Data © OpenStreetMap contributors, ODbL |
| Bing News RSS | **Evaluation only** (`evaluation/recall_check.py`), never for collection | Microsoft/Bing terms |
| OpenStreetMap standard tiles (via Folium/Leaflet) | Dashboard base map | [Tile usage policy](https://operations.osmfoundation.org/policies/tiles/): light, interactive use with attribution (shown on the map). For heavier or public use, switch to a commercial or self-hosted tile provider (one line in `app.py`) |

## Database (SQLite, via SQLAlchemy)

| Table | Contents |
|---|---|
| `articles` | headline, source, canonical and listing URLs, collection method, publication time (+ `date_source`) and collection time, excerpt, article text, extraction status, content hash, `disease_tags` and `matched_keywords`, classification (`relevance`, `content_type`, `event_status`, `relevant_diseases`, `relevance_reasons`), `story_id`, manual review (`relevance_status`), and location (`country`, `state`, `district`, `locality`, `latitude`, `longitude`, `location_level`, `location_text`, `location_confidence`, `location_review`, `location_source`, `location_reasons`, `location_candidates`) |
| `geocode_cache` | Nominatim answers per query ({} = not found), so each place is looked up once |
| `seen_links` | links checked but not stored: `no_keyword`, `too_old`, `duplicate`, or `fetch_failed` (with `attempts`, retried up to 3 times) |
| `listing_status` | per listing: last success, last in-depth read, last error |
| `official_reports` | IDSP outbreak records (see [Data model](#data-model-and-migration)) |
| `collection_runs` | one row per run: mode, times, status, config fingerprint, statistics and errors |

New columns are added to an existing database automatically.

## Layout

- `app.py`: the Streamlit dashboard; `.streamlit/config.toml`: dashboard server settings
- `outbreak_monitor/`: application code
  - `discovery.py` finds articles
  - `extractor.py` gets article text
  - `processing.py` cleans headlines, tags keywords and matches stories
  - `relevance.py` classifies articles
  - `gazetteer.py` builds and loads the reference place data; `location.py` finds the outbreak location
  - `official.py` reads the IDSP weekly outbreak reports
  - `dashboard_data.py` loads, filters and groups articles and official records for the dashboard
  - `collector.py` runs the pipeline
  - `reprocess.py` re-applies the configuration to stored articles
  - `scheduler.py` runs the pipeline on a schedule
- `config/`: diseases and keywords, relevance cues, locations, and news sources (YAML)
- `reference/`: India and foreign place reference data (from GeoNames)
- `evaluation/`: coverage check (`recall_check.py`), location accuracy (`location_eval.py` + `location_labels.csv`)
- `tests/`: pytest tests
- `data/`: SQLite database and logs (not committed)

## Troubleshooting

| Problem | Fix |
|---|---|
| Dashboard shows an old error after a code or config change (e.g. `'Disease' object has no attribute 'category'`) | Stop Streamlit (Ctrl+C) and start it again: `streamlit run app.py`. Streamlit keeps already-imported modules |
| `database is locked` | Another collection is running (scheduler and Refresh button at once). Wait for it to finish; SQLite waits 30 s before giving up |
| A source shows `listings_failed` | Check `listing_status.last_error` or the run's `errors`. Temporary errors are retried next run; a site that keeps failing can be paused with `enabled: false` in `config/sources.yaml` |
| No official reports | The NCDC site can be slow. Run `python -m outbreak_monitor.official` and check the log; reports already stored are not downloaded again |
| Changed keywords have no effect | They apply from the next run (stored articles are re-classified automatically), or run `python -m outbreak_monitor.reprocess` |
| `ModuleNotFoundError` | Activate the virtual environment (`.\.venv\Scripts\Activate.ps1`) and run `pip install -r requirements.txt` |

## Known limitations

- Classification is rule-based. Headlines that don't name the disease (e.g. "Balaghat child deaths…") and borderline cases (a health department "sensitising the public") can be misclassified. These are what manual review is for.
- NDTV, The Hindu, India Today and Deccan Chronicle have no archive sitemaps (and the first three no keyword pages) we can use. Their backfill and catch-up only reach back as far as their RSS feeds and 48-hour sitemaps. Continuous scheduled runs cover them fully.
- Archive sitemaps have no headlines. During backfill, an article from those sites is found only if a keyword appears in its URL slug, or if it is on a keyword page.
- `refresh_interval_minutes` is read when the scheduler starts, so changing it requires a restart.
- Location rules are heuristics. National or regional pieces (e.g. a WHO statement issued in New Delhi) can get a state. A headline about a whole state whose body describes one district stays state-level. Where a patient lives vs where they were treated is only partly handled. Review flags and manual review cover these cases.
- The small spaCy model sometimes misses or mislabels place names. The reference-data match and review flags compensate, but a village that neither recognises is not found.
- IDSP reports appear about two months after the outbreak, and only the latest `weeks` reports are read. Districts created recently (e.g. Bengaluru South, 2025) are not in GeoNames and are mapped at state level.
