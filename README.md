# Infectious Disease Outbreak News Monitoring & India Map

This tool collects news about infectious disease outbreaks in India, works out which articles actually report an outbreak, finds where it happened, and plots it on an interactive map of India. It also pulls in the official weekly outbreak reports published by NCDC (IDSP), so news and official records can be seen side by side.

It is a news-based monitoring prototype, not an official surveillance or early-warning system. News coverage can be incomplete or delayed, and the number of articles says nothing about how many people are ill.

Everything runs locally in Python with open-source libraries. No paid APIs or language models are used.

## What it does

1. Every 30 minutes (or when you press *Refresh now*) it reads RSS feeds, sitemaps and topic pages from 10 Indian news sites and keeps only articles that mention a monitored disease.
2. For each article it stores the headline, source, publication date, URL and text, and records which disease keywords matched.
3. A rule-based classifier decides whether the article reports an outbreak (or a public-health response to one), and whether the report is new, suspected or historical.
4. The outbreak location is resolved to state, district or town, ignoring things like the newspaper's dateline.
5. The dashboard shows the reports on a map of India, with filters, an article list, and a column where you can mark articles relevant or irrelevant.

Diseases, keywords and sources are all in YAML files under `config/`, so they can be changed without touching the code.

## Quick start (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python -m outbreak_monitor.collector     # first run collects the last 45 days (takes a while)
streamlit run app.py                     # dashboard at http://localhost:8501
```

Other useful commands:

```powershell
python -m outbreak_monitor.scheduler              # collect now and then every 30 minutes (Ctrl+C to stop)
python -m outbreak_monitor.collector --backfill   # re-read every source in depth over the last 45 days
python -m outbreak_monitor.official               # fetch only the official IDSP reports
python -m outbreak_monitor.reprocess              # re-classify and re-locate all stored articles
python -m outbreak_monitor.export_samples         # write sample records and the schema to samples/
python -m evaluation.evaluate                     # accuracy against the labelled articles
python -m evaluation.location_eval                # location accuracy on a second labelled set
python -m evaluation.recall_check                 # compare coverage with Bing News (evaluation only)
python -m outbreak_monitor.gazetteer              # rebuild the place reference data (rarely needed)
pytest                                            # tests
```

### With Docker

```powershell
docker compose up --build     # dashboard at http://localhost:8501, collection every 30 minutes
docker compose down           # stop; the database is kept in the "outbreak-data" volume
```

This starts two containers from the same image: the dashboard and the scheduled collector, sharing one database. The `config/` folder is mounted, so keyword and source changes don't need a rebuild. The port is only open to the local machine. Commands can be run inside a container, for example `docker compose exec collector python -m outbreak_monitor.collector --backfill`.

The Docker setup has not been tested on a machine with Docker yet.

## Diseases monitored

19 diseases in 5 groups, each with alternative names and spellings in `config/diseases.yaml`:

| Group | Diseases |
|---|---|
| Vector-borne | Dengue, Malaria, Chikungunya, Japanese Encephalitis / AES (incl. "brain fever", "chamki bukhar", Chandipura) |
| Water- and food-borne | Cholera, Acute Diarrhoeal Disease (incl. gastroenteritis, food poisoning), Typhoid, Hepatitis A / E (incl. jaundice outbreaks) |
| Emerging and zoonotic | Nipah, Leptospirosis ("rat fever"), Scrub Typhus, Kyasanur Forest Disease ("monkey fever"), Rabies, other zoonotic outbreaks (bird flu, anthrax, brucellosis, plague, CCHF, mpox) |
| Respiratory and vaccine-preventable | Influenza (H1N1, H3N2, swine flu), COVID-19, Measles (incl. rubella), Diphtheria |
| Other epidemic-prone | Unexplained fever, deaths or clusters ("mystery fever", "unknown disease"…) |

Words like "outbreak", "surge in cases", "cluster", "suspected cases" or "deaths" are not used to collect articles. They only help decide whether an article that already names a disease is reporting an outbreak.

Some keywords are deliberately narrow. Plain "jaundice" also matched articles about liver disease and newborn care, so only phrases like "jaundice cases" or "jaundice outbreak" count. Plain "plague" mostly appeared as a figure of speech, so only "bubonic plague", "plague cases" and similar are used.

## Sources

### News sites

The collector uses four ways of finding articles, and checks robots.txt before every request:

| Method | What it is | When |
|---|---|---|
| RSS | Feeds the publisher provides for automated reading | every run |
| News sitemap | XML listing every article from the last ~48 hours | every run |
| Archive sitemap | Daily, weekly or monthly lists of all articles (URLs only) | when catching up |
| Topic page | The site's own page for a disease, e.g. `/topic/dengue` | first page every run, more when catching up |

| Source | RSS | News sitemap | Archive | Topic pages | Notes |
|---|---|---|---|---|---|
| The Hindu | 26 feeds (national, health, states, cities) | ✓ | — | — | Topic pages redirect and search is disallowed by robots.txt |
| Times of India | India | ✓ | weekly | `/topic/{term}/news` | Topic pages only show more articles with JavaScript |
| The Indian Express | India, Health | ✓ | daily | `/about/{term}/` | |
| Hindustan Times | India, Health | ✓ | monthly | `/topic/{term}/news/page-{n}` | |
| NDTV | 5 feeds | — | — | — | Sitemaps and topic pages return 403 |
| India Today | home, India | ✓ | — | — | |
| News18 | India | ✓ | — | `/topics/{term}/page-{n}/` | |
| Deccan Chronicle | All news | ✓ | — | `/tags/{term}` | |
| Scroll | All articles | ✓ | — | `/topic/{term}` | |
| ET HealthWorld | Top stories | ✓ | monthly | `/tag/{term}/{n}` | |

Sources can be added, paused (`enabled: false`) or removed in `config/sources.yaml`. A new source is backfilled automatically on the next run.

### Official reports (NCDC / IDSP)

NCDC publishes a PDF every week listing each outbreak reported by the states through IDSP: state, district, disease, cases, deaths, dates, status and the action taken. These are read automatically and kept in their own table (`official_reports`). The reports appear about two months after the week they cover, and the latest 12 weeks are kept.

Reading these PDFs reliably took some care:
- each outbreak starts at its unique ID (e.g. `AS/BAJ/2026/34/1369`), and text that wraps across lines or pages is joined back to it;
- columns are matched by their header text, because the tables don't all have the same layout;
- dates and status are recognised by their content, since narrow columns sometimes push them into the next column;
- if state, district and disease have shifted by one column, they are put back using the place names;
- diseases are taken only from the disease column, never from the comments, which often mention diseases that were ruled out.

Values are kept as printed, including a few obvious errors in the source PDFs. Where a value is cut off by a page break, the field is left empty rather than guessed. On a check of six weekly reports (245 outbreaks), 244 records were complete and 243 were located to the right district.

Other official sources were looked at but not used: the MoHFW site needs a full browser, the IDSP site doesn't respond, PIB's feed mixes all ministries and is mostly in Hindi, and state health department bulletins differ too much from state to state.

## How it works

### Collection

Each site is handled by its own worker so a slow site doesn't hold up the others. A run:

1. reads the site's feeds, sitemaps and topic pages, keeping only links on that site that look like articles;
2. skips links it has already seen, items older than 45 days, and feed or sitemap items without a disease keyword in the title, summary or URL;
3. downloads each remaining article (if robots.txt allows) and extracts the text, canonical URL and date with Trafilatura;
4. stores it, unless it is a duplicate.

Normally a run only needs the feeds and the 48-hour sitemaps. If the computer was off for more than 36 hours, the next run catches up automatically by reading the archives and topic pages back to the last successful run. A newly added source or search term is backfilled over 45 days the same way.

**Duplicates.** An article isn't stored again if its canonical URL is known, if its text is identical to a stored one, or if the same site has published an almost identical headline with the same numbers within 3 days. "Cases rise to 48" is therefore treated as a new update, not a copy of "cases rise to 40". The same story from different publishers is stored for each one but grouped under one `story_id`, so the map shows a single marker listing all sources.

**When things go wrong.** Requests are retried with backoff, each site gets at most one request per second, and no response may take longer than 60 seconds. Failed downloads are retried in later runs (up to three times). One broken page or site never stops the run. Each run is logged in the `collection_runs` table with its counts and errors, and in `data/logs/collector.log`.

**Disease tags** come only from the headline and the article text. For every article the exact keywords that matched are saved, e.g. `{"Dengue": {"headline": ["dengue"], "text": ["dengue cases"]}}`.

When `config/diseases.yaml`, `config/relevance.yaml` or `config/locations.yaml` changes, the next run re-tags, re-classifies and re-locates all stored articles, and searches the sites for any new terms.

### Relevance and event type

The classifier is rule-based, so every decision comes with a reason (stored in `relevance_reasons`). It uses spaCy to split sentences and the cue words in `config/relevance.yaml`.

- A disease counts as the article's subject if it's in the headline, in three or more sentences, or early in the article and mentioned again.
- Only sentences about that disease are checked for case counts ("40 new cases", "87 jawans hospitalised", "tally rises to 70"), outbreak words (outbreak, deaths, surge, suspected) and response words (fogging, alert, surveillance drive).
- The headline is checked first for research, awareness and explainer pieces, crime stories, and stories about past events such as COVID compensation.
- A disease mentioned only as a comparison ("Ebola is like Covid") doesn't count.

| Type | Meaning | Relevant? |
|---|---|---|
| `outbreak_report` | cases, deaths or an outbreak are reported | yes |
| `public_health_response` | a response to disease in an area: fogging, alerts, drives | yes |
| `research` | studies, vaccines, drugs | no |
| `awareness` | explainers, tips, symptoms | no |
| `passing_mention` | the disease is not what the article is about | no |
| `other` | none of the above, crime, past events, or sections like sport and entertainment | no |

Relevant articles also get an event status: **new** (current cases reported), **suspected**, **historical** (clearly about the past) or **unclear** (a response without a case report).

A person's review in the dashboard (`relevance_status`: pending, relevant or irrelevant) always overrides the automatic result and is never changed by the code.

### Location

Each article is placed at the most specific level it supports: state, district or town. A report that only names a district is mapped at the district, and no more precise point is invented.

- Place names are found with spaCy and matched against reference data built from [GeoNames](https://www.geonames.org): all 36 states and UTs, 763 districts and about 6,600 towns, with spelling variants (Gurgaon/Gurugram, Trichy/Tiruchirappalli) and abbreviations like "TN" or "J&K".
- Mentions are weighted: a place in the headline counts most, then places in sentences about the outbreak.
- The dateline ("PUNE:") and bylines are ignored, unless the outbreak is clearly reported there. Places named only as the lab that tested samples, or as part of a hospital's name, are down-weighted.
- Ambiguous names (Aurangabad in Bihar or Maharashtra) are resolved using the state the article mentions. If that's not possible, the location is flagged for review.
- Articles about outbreaks abroad are recognised and kept off the map.
- Villages that aren't in the reference data are looked up with OpenStreetMap Nominatim, but only for relevant articles. The result must lie in the same state. There is at most one request per second, and every answer is cached.

Each article stores the place in its own words, the level, a confidence (high/medium/low), a review flag, and the reasons for the choice.

## Dashboard

`streamlit run app.py` opens it at http://localhost:8501.

- **Map** of India only (the official boundary; everything outside India is masked). There is one marker per story, coloured by event status, and nearby markers group together when zoomed out. Clicking a marker shows the disease, headline, location, date and links to every source that reported it. Official IDSP records appear as green squares on their own layer.
- **Filters** in the sidebar: disease group, disease, state, source and publication date. You can also include reports from outside India (list only) and turn official reports on or off. *Zoom to district* jumps to a district.
- **Figures** at the top: matching articles, stories on the map, distinct locations, and the time of the last successful refresh.
- **Beside the map**, the latest reports and the states with the most reports. These are story counts, not case numbers.
- **Article register** lists every matching article with its location, confidence, event status and a link to the original. The *Your review* column marks an article relevant or irrelevant, and the table can be downloaded as CSV.
- **Official reports** tab: every IDSP record with all its fields and a link to the weekly PDF.
- **Refresh now** runs a collection straight away.

The dashboard only listens on `localhost`, because it can save reviews and start collection runs. Put it behind a login before opening it to a network.

## Evaluation

The full write-up is in [`evaluation/REPORT.md`](evaluation/REPORT.md). 120 articles were picked at random from the database and labelled by reading them (`evaluation/labels.csv`). The system's results were then compared with the labels:

| | Result |
|---|---|
| Relevance: precision / recall / F1 | 83% / 93% / 0.88 |
| Event status | 96% |
| Disease (at least one correct / all correct) | 100% / 81% |
| State / district | 98% / 82% |

A second set of 60 articles, labelled for location only, gives 95% for state and 92% for district. Most of the false positives are dengue or rabies drives with no case numbers, which may or may not count as relevant depending on how strict you want to be. The report lists every disagreement.

The labels were made with AI assistance and still need to be checked by a person. After checking them, run `python -m evaluation.evaluate` to get the final numbers.

## Data

The data is stored in SQLite (`data/outbreaks.db`) through SQLAlchemy, using portable column types so it can move to PostgreSQL later.

| Table | What's in it |
|---|---|
| `articles` | headline, source, URLs, publication and collection time, text, disease tags and matched keywords, classification and reasons, story ID, review status, and location (country, state, district, locality, coordinates, level, confidence, review flag, reasons) |
| `official_reports` | one row per IDSP outbreak: ID, report week and PDF link, state, district, disease (as printed and mapped), cases, deaths, dates, status, action taken, coordinates |
| `seen_links` | links checked but not stored (no keyword, too old, duplicate, failed download) |
| `listing_status` | when each feed, sitemap or topic page was last read, and its last error |
| `geocode_cache` | Nominatim answers, so each place is looked up only once |
| `collection_runs` | one row per run: mode, times, status, counts and errors |

New columns are added to an existing database automatically. `samples/` has 25 example articles (metadata only, without the publishers' text), 25 official records and the full schema (`schema.sql`).

To switch to PostgreSQL, install a driver (`pip install "psycopg[binary]"`) and set `OUTBREAK_DB_URL=postgresql+psycopg://user:password@host/dbname`. The tables are created on first use. For spatial queries, PostGIS can add a point column built from `latitude` and `longitude`.

## Configuration

| File | What it controls |
|---|---|
| `config/diseases.yaml` | diseases, their group, keywords and search terms; lookback period (45 days) and refresh interval (30 minutes) |
| `config/relevance.yaml` | cue words for outbreaks, responses, research, awareness, crime, past events and comparisons; URL sections to ignore |
| `config/locations.yaml` | place abbreviations and spellings, display names, geocoding settings |
| `config/sources.yaml` | news sites and how to read them, plus the official report series |

Changes take effect on the next run. Stored articles are re-classified automatically.

## Responsible use

- Only what sites publish for machines (feeds and the sitemaps listed in robots.txt) and public topic pages are read.
- robots.txt is respected, there is at most one request per second per site, and the collector identifies itself with a User-Agent.
- There are no logins, no paywall workarounds and no full-site crawling.
- Article text is stored only for matching and classification and is never shown. The dashboard shows the headline, source and date, and links to the original.

NCDC's copyright policy allows its content to be reused with the source clearly acknowledged; the dashboard names IDSP/NCDC and links every record to its PDF. The news sites' terms of use still need to be read and recorded here before the tool is shared more widely. Any site that doesn't allow automated reading should be disabled in `config/sources.yaml`.

| Source | Terms page (URL, date checked) | Automated access / RSS allowed? | Decision |
|---|---|---|---|
| The Hindu | | | |
| Times of India | | | |
| The Indian Express | | | |
| Hindustan Times | | | |
| NDTV | | | |
| India Today | | | |
| News18 | | | |
| Deccan Chronicle | | | |
| Scroll | | | |
| ET HealthWorld | | | |

Third-party data and services:

| | Used for | Terms |
|---|---|---|
| [GeoNames](https://www.geonames.org) | place reference data | CC BY 4.0 |
| [DataMeet India maps](https://github.com/datameet/maps/tree/master/Country) | India's official boundary on the map | CC0 |
| [OpenStreetMap Nominatim](https://operations.osmfoundation.org/policies/nominatim/) | locating villages | max 1 request/second, cached; data © OpenStreetMap contributors (ODbL) |
| [OpenStreetMap tiles](https://operations.osmfoundation.org/policies/tiles/) | base map | light use only; switch to a tile provider for heavier or public use (one line in `app.py`) |
| [NCDC](https://ncdc.mohfw.gov.in/) | official outbreak reports | reuse with acknowledgement |
| Bing News RSS | coverage check in `evaluation/` only, never for collection | Microsoft terms |

## Project layout

```
app.py                     Streamlit dashboard (.streamlit/config.toml has its settings)
outbreak_monitor/
  discovery.py             finds article links in feeds, sitemaps and topic pages
  extractor.py             downloads articles and extracts the text
  processing.py            headline cleaning, keyword tagging, story grouping
  relevance.py             relevance and event classification
  gazetteer.py, location.py  place reference data and outbreak location
  official.py              NCDC/IDSP weekly reports
  collector.py             one collection run; scheduler.py runs it every 30 minutes
  reprocess.py             re-applies the configuration to stored articles
  dashboard_data.py        data behind the dashboard
  export_samples.py        sample records and schema
config/                    diseases, relevance cues, locations, sources
reference/                 place data and India boundary
evaluation/                evaluation report, labels and scripts
samples/                   example records and database schema
tests/                     pytest tests
Dockerfile, docker-compose.yml
data/                      database and logs (not in git)
```

## Troubleshooting

| Problem | What to do |
|---|---|
| The dashboard shows an old error after code or config changes | Stop Streamlit (Ctrl+C) and start it again; it keeps old modules loaded |
| `database is locked` | Another collection is running (e.g. the scheduler and the Refresh button). Wait for it to finish |
| A source keeps failing | Check the errors in `collection_runs` or `listing_status`. Temporary errors are retried; a site that keeps failing can be disabled in `config/sources.yaml` |
| No official reports | The NCDC site can be slow. Run `python -m outbreak_monitor.official` and check the log |
| A keyword change has no effect | It applies from the next run, or run `python -m outbreak_monitor.reprocess` |
| `ModuleNotFoundError` | Activate the virtual environment and run `pip install -r requirements.txt` |

## Known limitations

- The classifier is rule-based. Headlines that don't name the disease (e.g. a series of child deaths) can be missed, and borderline pieces can go either way. That's what the review column is for.
- NDTV, The Hindu, India Today and Deccan Chronicle have no archives the collector can use, so after a long gap they only reach back as far as their feeds. Regular scheduled runs cover them fully.
- Archive sitemaps don't include headlines, so when catching up, articles from them are found only if a keyword is in the URL or they appear on a topic page.
- Location rules are heuristics. National stories issued from a city can pick up a state, and where a patient lives versus where they were treated isn't always clear. Uncertain cases are flagged for review.
- IDSP reports arrive about two months late. Districts created recently (e.g. Bengaluru South) aren't in GeoNames and are mapped at state level.
- Changing the refresh interval needs a scheduler restart.
