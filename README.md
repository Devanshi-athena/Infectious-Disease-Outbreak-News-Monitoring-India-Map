# Infectious Disease Outbreak News Monitoring & India Map

This tool collects news about infectious disease outbreaks in India, works out which articles actually report an outbreak, finds where it happened, and plots it on an interactive map of India. It also pulls in the official weekly outbreak reports published by NCDC (IDSP), so news and official records can be seen side by side.

It is a news-based monitoring prototype, not an official surveillance or early-warning system. News coverage can be incomplete or delayed, and the number of articles says nothing about how many people are ill.

Collection, classification and translation run locally in Python with open-source libraries. No paid APIs or large language models are used. News in 12 Indian languages is translated to English offline with the open-source IndicTrans2 model. The only online services are the news sites, NCDC, OpenStreetMap Nominatim (for village lookups) and the optional Esri map backgrounds.

## What it does

1. Every 30 minutes it reads RSS feeds and news sitemaps (and topic pages at most every 3 hours) from 22 English and 113 Indian-language news sites, and keeps only articles that mention a monitored disease. Articles in Indian languages are translated to English.
2. For each article it stores the headline, source, publication date, URL and text, and records which disease keywords matched.
3. A rule-based classifier decides whether the article reports an outbreak (or a public-health response to one), and whether the report is new, suspected or historical.
4. The outbreak location is resolved to state, district or town, ignoring things like the newspaper's dateline, and given its official LGD code.
5. The dashboard shows the reports on a map of India, with alerts for rising or newly reported diseases, reports grouped into outbreaks, filters, an article list, and a column where you can mark articles relevant or irrelevant.

Diseases, keywords and sources are all in YAML files under `config/`, so they can be changed without touching the code.

## Quick start (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

hf auth login                                     # once; see "Translation model" below
python -m outbreak_monitor.translate --download   # once: the translation model (~0.9 GB) into models/
python -m outbreak_monitor.collector              # first run collects the last 45 days (takes a while)
streamlit run app.py                              # dashboard at http://localhost:8501
python -m outbreak_monitor.scheduler              # in a second terminal: keeps collecting every 30 minutes
```

Collection every 30 minutes happens only while the scheduler (or the Docker setup below) is running; the dashboard's *Refresh now* button runs a quick collection on request.

**Translation model.** Indian-language articles are translated with [IndicTrans2](https://huggingface.co/ai4bharat/indictrans2-indic-en-dist-200M) (AI4Bharat, MIT licence). The model is free, but Hugging Face asks you to accept its terms first: sign in at huggingface.co, open the model page and request access (granted immediately), create a read token under *Settings → Access Tokens*, and run `hf auth login`. Without the model, English sources are still collected and the regional-language sources are skipped (never stored untranslated).

Other useful commands:

```powershell
python -m outbreak_monitor.scheduler              # collect now and then every 30 minutes (Ctrl+C to stop)
python -m outbreak_monitor.collector --quick      # one quick run: feeds and news sitemaps since the last run
python -m outbreak_monitor.collector --backfill   # re-read every source in depth over the last 45 days
python -m outbreak_monitor.official               # fetch only the official IDSP reports
python -m outbreak_monitor.official --reparse     # read the stored IDSP weeks again (after a parser change)
python -m outbreak_monitor.reprocess              # re-classify and re-locate all stored articles
python -m outbreak_monitor.export_samples         # write sample records and the schema to samples/
python -m evaluation.evaluate                     # accuracy against the labelled articles
python -m evaluation.location_eval                # location accuracy on a second labelled set
python -m evaluation.recall_check                 # compare coverage with Bing News (evaluation only)
python -m outbreak_monitor.gazetteer              # rebuild the place reference data (rarely needed)
python -m outbreak_monitor.lgd --download         # refresh the LGD code lists (rarely needed)
python -m outbreak_monitor.boundaries             # rebuild the state and district boundaries of the map (rarely needed)
python -m outbreak_monitor.lgd --new-districts    # add LGD districts missing from GeoNames, located with Nominatim
python -m outbreak_monitor.lgd --villages         # download the LGD village list (about 3 hours, resumable)
python -m outbreak_monitor.translate --language ta "டெங்கு காய்ச்சலால் 3 பேர் உயிரிழப்பு"   # try the translation
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

The list follows the **Integrated Disease Surveillance Programme (IDSP)**: the conditions on the IHIP reporting forms S (syndromic), P (presumptive) and L (laboratory-confirmed) and in the IDSP case definitions of 2024 (NCDC; the copies used are published on [NHM Himachal Pradesh's IDSP page](https://nhm.hp.gov.in/idsp/reporting-format)). Each condition has a stable code and a note of where it is on the IDSP list, with alternative names and spellings in English and the 12 Indian languages, in `config/diseases.yaml`:

| Group | Conditions |
|---|---|
| Water- and food-borne | Acute Diarrhoeal Disease (incl. acute gastroenteritis), Food poisoning, Dysentery, Cholera, Shigellosis, Non-typhoidal salmonellosis, Enteric fever (Typhoid), Acute Viral Hepatitis, Hepatitis A, Hepatitis E |
| Vector-borne | Malaria, Dengue, Chikungunya, Kala-azar, Acute Encephalitis Syndrome (incl. "brain fever", Chandipura), Japanese Encephalitis, West Nile Virus, Zika Virus Disease, Yellow Fever |
| Respiratory | Influenza-like illness (ILI), Severe acute respiratory infection (SARI), Influenza (H1N1, H3N2, swine flu), Bird flu in humans (H5N1, H9N2), COVID-19, MERS-CoV |
| Vaccine-preventable | Measles, Rubella, Diphtheria, Pertussis, Polio, Mumps, Chickenpox, Meningitis |
| Zoonotic | Human Rabies, Leptospirosis ("rat fever"), Scrub Typhus, Kyasanur Forest Disease ("monkey fever"), Nipah Virus Disease, Brucellosis, Anthrax, Plague, CCHF, Mpox |
| Imported and haemorrhagic fevers | Ebola Virus Disease, Marburg Virus Disease |
| Syndromes (S form) | Fever (no diagnosis), Fever with rash, Fever with bleeding, Fever with altered sensorium, Cough (up to 2 weeks), Cough (more than 2 weeks), Jaundice (< 4 weeks), Acute Flaccid Paralysis, Animal bite, Snake bite, Other unusual syndrome or health condition |

Two entries are not on the IDSP forms: **Food poisoning**, the most frequent term in IDSP's own weekly outbreak reports, and **COVID-19**, kept on purpose. **Bird flu** is counted only when the text names a person ("first human case of H5N1", "girl dies of bird flu"); bird flu in poultry and wild birds is animal health, not an IDSP condition, and is recognised but not monitored. Plain "cough" is never used, since it mostly appears in cough-syrup news; only phrases such as "cough and cold cases" or "cases of persistent cough" count.

Words like "outbreak", "surge in cases", "cluster", "suspected cases" or "deaths" are not used to collect articles. They only help decide whether an article that already names a disease is reporting an outbreak.

A condition is only recorded when the text actually names it, and the same rule applies to news articles and official reports:
- jaundice alone is not taken to mean hepatitis A or E. A jaundice outbreak without a named cause is the S-form syndrome *Jaundice (< 4 weeks)*; hepatitis without its type is *Acute Viral Hepatitis*. Both are dropped as soon as hepatitis A or E is named;
- the syndromes are dropped when a diagnosis is named ("mystery fever… dengue confirmed" is dengue), and an animal bite is dropped when human rabies is named;
- a suspected "brain fever" is Acute Encephalitis Syndrome, not Japanese Encephalitis; where keywords overlap, the more specific one wins.

A few keywords are also narrowed to avoid false matches. Plain "plague" mostly appears as a figure of speech, so only "bubonic plague", "plague cases" and similar are used.

## Sources

### News sites

The collector uses four ways of finding articles, and checks robots.txt before every request:

| Method | What it is | When |
|---|---|---|
| RSS | Feeds the publisher provides for automated reading | every run |
| News sitemap | XML listing every article from the last ~48 hours | every run |
| Archive sitemap | Daily, weekly or monthly lists of all articles (URLs only) | when catching up or backfilling (first run, new source) |
| Topic page | The site's own page for a disease, e.g. `/topic/dengue` | first page at most every 3 hours; more pages when catching up or backfilling |

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

English regional papers, added for states the national papers cover thinly:

| Source | Region | RSS | News sitemap | Archive | Notes |
|---|---|---|---|---|---|
| The New Indian Express | South India | ✓ | ✓ | daily | |
| Deccan Herald | Karnataka | ✓ | ✓ | daily | |
| The Tribune | Punjab, Haryana, Himachal, Chandigarh, J&K | — | — | — | RSS refuses access; 14 regional section sitemaps (latest 100 articles each, URLs only) |
| Telangana Today | Telangana | ✓ | ✓ | — | |
| OdishaTV | Odisha | ✓ | ✓ | daily | |
| The Sentinel | Assam | ✓ | ✓ | daily | |
| Northeast Now | North-East | ✓ | — | post sitemaps | |
| EastMojo | North-East | ✓ | — | post sitemaps | |
| The Shillong Times | Meghalaya | ✓ | — | — | |
| Nagaland Post | Nagaland | ✓ | — | — | |
| Eastern Mirror | Nagaland | — | ✓ | — | |
| Tripura Times | Tripura | ✓ | ✓ | about a month | |

Arunachal Times and Imphal Free Press were checked but have no feed or sitemap; The Hills Times is no longer updated.

Sources can be added, paused (`enabled: false`) or removed in `config/sources.yaml` (English) and `config/regional_sources.yaml` (Indian languages). A new source is backfilled over 45 days on the next scheduled run, as far as its archive sitemaps and topic pages allow; a source with only a feed or news sitemap starts from its last ~48 hours.

### Indian-language news sites

113 sites in 12 languages, in `config/regional_sources.yaml`. They are read through their news sitemaps and RSS feeds (no topic pages), and every one was checked live before it was added. Where a site splits its news sitemap into parts (Dainik Bhaskar has about 26, covering every district edition), all parts are read. The 22 ETV Bharat editions (21 state editions and the national Urdu edition), Patrika, Navbharat Times and Live Hindustan also publish archive sitemaps (daily or monthly), which give them a full 45-day backfill.

| Language | Sites | Sources |
|---|---|---|
| Hindi | 15 | Dainik Bhaskar, Dainik Jagran, Amar Ujala, Live Hindustan, Patrika, Navbharat Times, Prabhat Khabar, Naidunia, ETV Bharat (10 states), NDTV India, Aaj Tak, News18 Hindi, ABP Live, TV9 Bharatvarsh, Jansatta |
| Marathi | 12 | Lokmat, Loksatta, Maharashtra Times, Sakal, Divya Marathi, Pudhari, ETV Bharat, ABP Majha, TV9 Marathi, News18 Lokmat, Saam TV, Lokshahi |
| Bengali | 8 | Bartaman, Sangbad Pratidin, Ei Samay, ETV Bharat, ABP Ananda, News18 Bangla, TV9 Bangla, Uttarbanga Sambad |
| Telugu | 12 | Eenadu, Sakshi, Andhra Jyothy, Namasthe Telangana, ETV Bharat (Andhra Pradesh, Telangana), TV9 Telugu, NTV, Samayam Telugu, ABP Desam, News18 Telugu, Asianet News Telugu |
| Tamil | 13 | Daily Thanthi, Dinamalar, Dinakaran, Dinamani, Hindu Tamil Thisai, Maalaimalar, ETV Bharat, Vikatan, Puthiya Thalaimurai, News18 Tamil, Samayam Tamil, ABP Nadu, OneIndia Tamil |
| Gujarati | 8 | Divya Bhaskar, Gujarat Samachar, ETV Bharat, Indian Express Gujarati, TV9 Gujarati, GSTV, I Am Gujarat, News18 Gujarati |
| Urdu | 3 | ETV Bharat, News18 Urdu, Qaumi Awaz |
| Kannada | 9 | Prajavani, Vijay Karnataka, Kannada Prabha, Vijayavani, ETV Bharat, TV9 Kannada, Public TV, Asianet Suvarna News, News18 Kannada |
| Odia | 7 | Sambad, Dharitri, Prameya (its feed is in English), ETV Bharat, Kanak News, News18 Odia, Argus News |
| Malayalam | 16 | Malayala Manorama, Mathrubhumi, Madhyamam, Kerala Kaumudi, Deshabhimani, Asianet News, ETV Bharat, Mangalam, Suprabhaatham, Janmabhumi, Samakalika Malayalam, 24 News, MediaOne, Reporter Live, News18 Malayalam, Samayam Malayalam |
| Punjabi | 6 | Punjabi Jagran, Jagbani, Punjabi Tribune, ETV Bharat, ABP Sanjha, News18 Punjab |
| Assamese | 4 | Asomiya Pratidin, ETV Bharat, News18 Assam, DY365 |

Anandabazar Patrika (Bengali) is listed but disabled: its sitemap refuses automated access (HTTP 403). Some sites were left out after checking: their robots.txt blocks crawlers (OneIndia Hindi and Kannada), their feeds are stale (Sandesh, Inquilab, Roznama Sahara), or they refuse access (Kalinga TV).

### Official reports (NCDC / IDSP)

NCDC publishes a PDF every week listing each outbreak reported by the states through IDSP: state, district, disease, cases, deaths, dates, status and the action taken. These are read automatically and kept in their own table (`official_reports`). The reports appear about two months after the week they cover. Each run checks the latest 12 weekly reports and reads any it hasn't stored yet.

Reading these PDFs reliably took some care:
- each outbreak starts at its unique ID (e.g. `AS/BAJ/2026/34/1369`, sometimes printed without the week, `KN/BEN/2026/1026`), and text that wraps across lines or onto the next page is joined back to it, including an ID cut by a page break;
- columns are matched by their header text, because the tables don't all have the same layout;
- dates and status are recognised by their content, since narrow columns sometimes push them into the next column;
- if state, district and disease have shifted by one column, they are put back using the place names;
- diseases are taken only from the disease column, never from the comments, which often mention diseases that were ruled out.

The report's own disease name is always kept as printed. It is linked to an IDSP condition only through the explicit table in `config/official_diseases.yaml` (for example "Acute Gastroenteritis" → Acute Diarrhoeal Disease, "Chandipura Virus" → Acute Encephalitis Syndrome, "Hepatitis A & E" → Hepatitis A and Hepatitis E). Terms are never stretched: "Jaundice" is the jaundice syndrome rather than hepatitis A/E, "Acute Hepatitis" is Acute Viral Hepatitis, a plain "Fever" outbreak is the fever syndrome, and conditions that IDSP reports outside its list (Guillain-Barré syndrome, hand, foot and mouth disease) go to *Other unusual syndrome or health condition*. This is the same standard used for news articles.

Values are kept as printed, including a few obvious errors in the source PDFs. Districts renamed since the reference data (Dharashiv, Ahilyanagar, Sribhumi) are recognised, and a district that cannot be found keeps the report's own name. All 681 records of the 16 weekly reports stored (weeks 23–38 of 2026) are located to a district. After a change to the parser, `python -m outbreak_monitor.official --reparse` reads the stored weeks again.

Other official sources were checked but not added, because they don't publish outbreak reports in a form that can be collected reliably:

| Source | Why it isn't used |
|---|---|
| MoHFW | pages are built in the browser; no feed or listing |
| IDSP website | doesn't respond (the reports are on NCDC) |
| PIB | the feed mixes all ministries and is mostly in Hindi |
| ICMR | press releases are research calls, grants and workshops; no outbreak reports, no feed |
| DGHS Delhi | news pages hold old COVID orders and bulletins (latest 2023); no feed |
| NHM | programme guidelines and surveys only; robots.txt doesn't respond |
| CHEB | site times out; content is health education |
| Puducherry Health Department | site fails with a certificate (SSL) error |
| Other state health departments | bulletins are PDFs in different formats and languages per state |

## How it works

### Collection

Each site is handled by its own worker so a slow site doesn't hold up the others. A run:

1. reads the site's feeds, sitemaps and topic pages, keeping only links on that site that look like articles;
2. skips links it has already seen, items older than 45 days, and feed or sitemap items without a disease keyword in the title, summary or URL;
3. downloads each remaining article (if robots.txt allows) and extracts the text, canonical URL and date with Trafilatura;
4. stores it, unless it is a duplicate.

Normally a run only needs the feeds and the 48-hour sitemaps. Each sitemap is read from shortly before its own last successful read (6 hours' overlap), so a site that was down for a while is read back to where it left off. Topic pages are read at most every 3 hours. If more than 36 hours have passed since the last full run (e.g. the computer was off), the next run catches up by reading the archives and topic pages back to shortly before it; quick refreshes don't count here, since they read no archives. A newly added source or search term is backfilled over 45 days the same way.

**One run at a time.** A run that would start while another is in progress (a click on *Refresh now* during a scheduled run) does not start; it is recorded as "skipped". A running collection records a heartbeat every minute; a run left "running" without one for 30 minutes (its process was stopped, e.g. the laptop shut down) no longer blocks the next. Ctrl+C or an error marks a run as failed straight away. After the computer wakes from sleep, the scheduler runs once rather than skipping the missed run.

**Duplicates.** An article isn't stored again if its canonical URL is known, if its text is identical to a stored one, or if the same site has published an almost identical headline with the same numbers within 3 days. "Cases rise to 48" is therefore treated as a new update, not a copy of "cases rise to 40". The same story from different publishers is stored for each one but grouped under one `story_id`, so the map shows a single marker listing all sources.

**When things go wrong.** Requests are retried with backoff, each site gets at most one request per second, and no response may take longer than 60 seconds. Failed downloads are retried in later runs (up to three times). One broken page or site never stops the run. Each run is logged in the `collection_runs` table with its counts and errors, and in `data/logs/collector.log`.

**Disease tags** come only from the headline and the article text. For every article the exact keywords that matched are saved, e.g. `{"Dengue": {"headline": ["dengue"], "text": ["dengue cases"]}}`.

### Indian-language articles

Articles from the regional-language sources go through the same steps, with screening and translation in between:

1. **Screening in the original language.** Each disease has its names in the 12 languages (`native_keywords` in `config/diseases.yaml`, e.g. डेंगू, ডেঙ্গু, டெங்கு for dengue). Each language also has a few general words that may point to an outbreak without naming a disease: fever, epidemic, jaundice, loose motions (`screening_words`). An article is downloaded only if its title, summary or URL contains one of them. It is translated only if its headline or text does. Matching follows each script's word boundaries, and allows word endings (डेंगू also matches डेंगूने). Short words must stand alone, so Bengali হাম (measles) doesn't match হামলা (attack). Screening words apply only to their own language: Marathi साथ (epidemic) means "with" in Hindi.
2. **Selective translation.** Only the headline, the first two sentences (they usually say where and when) and the sentences that contain a disease name or screening word are translated, up to 12 per article. This uses IndicTrans2, on the computer's processor, with no internet needed. A typical article takes 10–20 seconds.
3. **Tagging and classification.** Diseases are tagged from both the original text and the English translation. The classifier and the location finder then work on the translation, exactly as for English articles. If a sentence names a disease in the original but the translation lost it (for example, a local word for jaundice translated literally), the English disease name is added to that sentence for classification. The stored translation is not changed.
4. **Storage.** The headline is stored in English, with the original headline, the original text, the language and every translated sentence next to its original. Articles in English on a regional site are recognised by their script and handled as English. Text in another language on an English site is never stored untranslated.

When a configuration file that decides tags, classification or location changes (`diseases.yaml`, `relevance.yaml`, `locations.yaml`, `lgd.yaml`, or the reference place data), the next scheduled run re-tags, re-classifies and re-locates all stored articles (Indian-language articles from their saved translation, without translating again), and searches the sites for any new terms. *Refresh now* leaves this to the scheduled run.

### Relevance and event type

The classifier is rule-based, so every decision comes with a reason (stored in `relevance_reasons`). It uses spaCy to split sentences and the cue words in `config/relevance.yaml`.

- A disease counts as the article's subject if it's in the headline, in three or more sentences, or early in the article and mentioned again.
- Only sentences about that disease are checked for case counts ("40 new cases", "87 jawans hospitalised", "tally rises to 70"), outbreak words (outbreak, deaths, surge, suspected) and response words (fogging, alert, surveillance drive).
- The headline is checked first for research, awareness and explainer pieces, crime stories, and stories about past events such as COVID compensation or "post-Covid" trends. A study, explainer or question headline still counts as a report when it gives a count ("12 new cases") or a strong event word ("dies", "confirmed", "dengue cases in Pune"); weaker words ("Why are dengue cases rising?") don't.
- A headline that counts people taken ill without naming the disease ("100 hospitalised after eating chowmein") reports the disease named early in the article. Counts of something else don't count: "5 things people should know", "5,000 children vaccinated", "38 patients treated at a health camp", "70 per cent of people".
- Words that only look like outbreak words are ignored: "BJP hits back", "death anniversary", "surgeon".
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

- Place names are found with spaCy and matched against reference data built from [GeoNames](https://www.geonames.org): all 36 states and UTs, 763 districts and about 6,600 towns, plus the 30 newer districts that are in LGD but not yet in GeoNames (`reference/lgd_new_districts.csv`, e.g. Kotputli-Behror, Beawar, Pandhurna), with spelling variants (Gurgaon/Gurugram, Trichy/Tiruchirappalli) and abbreviations like "TN" or "J&K".
- Hyphenated names (Kotputli-Behror) are matched as written, not split into two towns.
- Place names used as people's names are skipped: after initials ("N Anand") or after "said", "Dr", "Minister" and similar words when followed by a surname ("said Sayan Chakrabarty"). "Anand district" is still a place.
- Mentions are weighted: a place in the headline counts most, then places in sentences about the outbreak.
- The dateline ("PUNE:") and bylines are ignored, unless the outbreak is clearly reported there. Places named only as the lab that tested samples, or as part of a hospital's name, are down-weighted.
- Ambiguous names (Aurangabad in Bihar or Maharashtra) are resolved using the state the article mentions. If that's not possible, the location is flagged for review. A name followed by "district" ("Banda district") is the district, not a same-named town elsewhere.
- Abbreviations that also mean something else count as the state only in context: "in MP", "MP govt" and "AP CM" do, but "BJP MP" (a Member of Parliament), "(AP)" (the news agency), "Raj Thackeray" and "Maha Aarti" don't.
- Articles about outbreaks abroad are recognised and kept off the map. A name shared with a foreign place ("Kochi" is also in Japan, "Mon" in Myanmar) is taken as Indian when it is an Indian state, district or large town, unless the foreign country is named in the same sentence.
- Villages that aren't in the reference data are looked up with OpenStreetMap Nominatim, but only for relevant articles. The result must lie in the same state. There is at most one request per second, and every answer is cached.

Each article stores the place in its own words, the level, a confidence (high/medium/low), a review flag, and the reasons for the choice.

**LGD codes.** Every place in India also gets its official code from the **Local Government Directory** (LGD, Ministry of Panchayati Raj): the state and district code, and the sub-district code when the place found is the name of exactly one sub-district of that district. The LGD lists of 36 states, 790 districts and 7,112 sub-districts are downloaded from LGD's public web service into `reference/lgd/`. All reference states and districts are matched to LGD: by name, by other spellings, by a close spelling within the state, or, for renamed districts (Aurangabad → Chhatrapati Sambhajinagar, Karimganj → Sribhumi) and places LGD keeps as sub-districts (Shahdara, Mahe, Yanam), through `config/lgd.yaml`. LGD's 677,660 villages are kept in `reference/lgd/villages.csv.gz`. A locality gets a village code only when all of these hold, so that no village code is guessed:
- it is not a town or city in the reference data, nor the district itself;
- exactly one village of the district has that name;
- its address from OpenStreetMap names the same sub-district (tahsil, mandal, taluk) as that LGD village.

LGD still lists many city areas as villages (Hadapsar in Pune), and the same village name often occurs in a neighbouring district, so most localities in the news get no village code. The codes are stored on articles and official IDSP records and shown in the article register and the map popups.

## Dashboard

`streamlit run app.py` opens it at http://localhost:8501.

- **Map** of India only, on the Survey of India boundaries (everything outside India is covered). Every name on the map is written by the tool in English, and the optional terrain and satellite backgrounds have no labels, so no foreign-script place names appear.
  - Districts are shaded by the number of reports (darker = more); districts where news reports are rising are outlined in red. Hovering a district shows its numbers, diseases, alerts and latest headlines.
  - Clicking a district makes it pop out (the rest of India dims and the map zooms to it): its reports appear as dots at their places, coloured by event status, with the full list in a panel on the right of the map. Clicking outside the district, the panel's ×, or Esc goes back. Districts with no reports aren't clickable.
  - When zoomed in to a state, reports that name only the state appear as a pill under its name; click it for the list.
  - *Map shows* in the sidebar chooses news reports (districts shaded violet, report dots coloured by event status), official IDSP records (shaded green, records as green squares), or both. In *Both*, districts with IDSP records are striped in green over the news shading (on white where there is no news).
  - The layer button (top right) switches the background between plain, terrain and satellite, and can hide the shading, the red outlines, the place names or the stripes; there is also a full-screen button.
  - *Focus the map*, above the map, zooms to a state or opens a district as if it had been clicked.
- **Filters** in the sidebar: *View* (relevant outbreak reports, the default; relevant reports whose location needs review; or all collected articles), disease group, disease, state, language, source and publication date. You can also include reports from outside India (list only).
- **Figures** at the top: matching articles, stories on the map, distinct locations, official IDSP records matching the filters, and the time of the last successful refresh.
- **Beside the map**, the latest reports and the states with the most reports. These are story counts, not case numbers.
- **Translated articles** show the English headline with the original headline and its language on the map and in the side panel, and in their own columns in the register.
- **Alerts** compare the 7 days up to the end of the chosen period (by default the latest report) with the weeks before, for each disease and place (district, or the state when no district is named). **Rise**: at least 3 reports and at least twice the weekly average of the 4 weeks before. **First report**: the disease is reported in a district where it was not in the 8 weeks before. Only news reports of cases count (a new or suspected outbreak), each story once; preparedness news ("state on alert over Ebola"), fogging drives and past outbreaks don't. Dates are in Indian time. Alerts point to news coverage to be checked against official data, not to confirmed outbreaks.
- **Outbreaks** groups the relevant reports of the same disease, in the same place and calendar week, as one outbreak, whatever the source or language, with the number of reports, sources and languages and the first headline.
- **Article register** lists every matching article with its location, confidence, event status, language and a link to the original. The *Your review* column marks an article relevant or irrelevant; press *Save review changes* to store it. The table can be downloaded as CSV.
- **States × diseases** tab: how many reports each state has for each disease, as a table shaded from light (few) to dark (many), either for news reports (each story counted once, following the sidebar filters) or for the official IDSP records. Below it, a bar chart ranks the states for one chosen disease. These are counts of reports, not of cases.
- **Not on the map** tab: articles from India that match the filters but name no state or district (national stories, or the place could not be read), with the reason. With *Relevant outbreak reports* selected, these are relevant reports that simply have no place on the map. The caption under the map says how many there are.
- **Official reports** tab: every IDSP record with all its fields and a link to the weekly PDF.
- **About the data** tab: how articles are collected, translated, classified and located, data attribution, and the current list of states and union territories with no relevant report.
- **Refresh now** collects what was published since the last refresh (feeds and news sitemaps only, plus a check for new IDSP weekly reports), in the background: the dashboard stays usable, and every open page updates itself when it is done. Topic pages, archives and any re-processing after a configuration change are left to the scheduled run (`outbreak_monitor.scheduler`).

The dashboard only listens on `localhost`, because it can save reviews and start collection runs. Put it behind a login before opening it to a network.

## Evaluation

The full write-up is in [`evaluation/REPORT.md`](evaluation/REPORT.md). 120 English-language articles published from 9 September to 28 October 2026 were picked at random from the database, labelled by reading them (`evaluation/labels.csv`, checked by Devanshi), and compared with what the system decided. Event status and disease are measured on the 61 relevant articles, location on the 57 of them with a place. Indian-language articles have not been evaluated yet.

| | Result |
|---|---|
| Relevance: precision / recall / F1 | 97% / 98% / 0.98 |
| Event status | 95% |
| Disease (at least one correct / all correct) | 98% / 82% |
| State / district | 96% / 81% |

Concrete disease-control actions (a dengue ward, fines for mosquito breeding, an anti-rabies drive) count as relevant even without case numbers. A second set of 60 articles, labelled for location only, gives 95% for state and 92% for district. The report lists every disagreement.

## Data

The data is stored in SQLite (`data/outbreaks.db`) through SQLAlchemy, using portable column types so it can move to PostgreSQL later.

| Table | What's in it |
|---|---|
| `articles` | headline (in English), source, URLs, publication and collection time, text (as published), language, original headline and translated sentences, disease tags and matched keywords, classification and reasons, story ID, review status, and location (country, state, district, locality, coordinates, LGD codes, level, confidence, review flag, reasons) |
| `official_reports` | one row per IDSP outbreak: ID, report week and PDF link, state, district (with LGD codes), disease (as printed and mapped), cases, deaths, dates, status, action taken, coordinates |
| `seen_links` | links checked but not stored (no keyword, too old, duplicate, failed download) |
| `listing_status` | when each feed, sitemap or topic page was last read, and its last error |
| `geocode_cache` | Nominatim answers, so each place is looked up only once |
| `collection_runs` | one row per run: mode (backfill, catch-up, incremental, quick), times, heartbeat, status (success, partial, failed, skipped), configuration fingerprint, counts and errors; manual re-processing is recorded too |

New columns are added to an existing database automatically. `samples/` has 25 example articles (metadata only, without the publishers' text), 25 official records and the full schema (`schema.sql`).

To switch to PostgreSQL, install a driver (`pip install "psycopg[binary]"`) and set `OUTBREAK_DB_URL=postgresql+psycopg://user:password@host/dbname`. The tables are created on first use. For spatial queries, PostGIS can add a point column built from `latitude` and `longitude`.

## Configuration

| File | What it controls |
|---|---|
| `config/diseases.yaml` | the IDSP conditions with their code, group, keywords, names in the 12 Indian languages and search terms; screening words per language; lookback period (45 days) and refresh interval (30 minutes) |
| `config/relevance.yaml` | cue words for outbreaks, responses, research, awareness, crime, past events and comparisons; strong event words for headlines; phrases that only look like outbreak words; URL sections to ignore |
| `config/locations.yaml` | place abbreviations (and which of them need context), spellings, display names, geocoding settings |
| `config/lgd.yaml` | reference districts whose LGD name differs (renamed districts, LGD sub-districts) |
| `config/sources.yaml` | English news sites and how to read them, plus the official report series |
| `config/regional_sources.yaml` | Indian-language news sites, with their language |
| `config/official_diseases.yaml` | which terms of the IDSP weekly outbreak reports map to which condition |

Changes take effect on the next run, and stored articles are re-processed on the next scheduled run (see Collection). A new refresh interval needs the scheduler to be restarted.

## Responsible use

- Only what sites publish for machines (feeds and the sitemaps listed in robots.txt) and public topic pages are read.
- robots.txt is respected, there is at most one request per second per site, and the collector identifies itself with a User-Agent.
- There are no logins, no paywall workarounds and no full-site crawling.
- Article text is stored only for matching and classification and is never shown. The dashboard shows the headline, source and date, and links to the original.

**Terms of use.** The news sites' terms of use were reviewed in November 2026. Most of the sites currently used restrict automated collection of their content. This project is a prototype for evaluation, so collection continues for now, on the limited basis described above. Before any production or public use, each site needs explicit permission (or a licensed news feed), and sites that don't allow it should be disabled in `config/sources.yaml` or replaced with permitted sources.

NCDC's copyright policy allows its content to be reused with the source clearly acknowledged. The dashboard names IDSP/NCDC and links every record to its weekly PDF.

Third-party data and services:

| | Used for | Terms |
|---|---|---|
| [GeoNames](https://www.geonames.org) | place reference data | CC BY 4.0 |
| [Survey of India Administrative Boundary Database](https://onlinemaps.surveyofindia.gov.in/Digital_Product_Show.aspx) (district level, 1:1 million; copy with geometry fixes at [surajreddyr/India-State-District-Administrative-Boundaries-Shapefiles](https://github.com/surajreddyr/India-State-District-Administrative-Boundaries-Shapefiles)) | India's boundary and the state and district boundaries on the map, linked to LGD codes by `python -m outbreak_monitor.boundaries` | free SOI digital product; follow SOI's terms of use |
| [OpenStreetMap Nominatim](https://operations.osmfoundation.org/policies/nominatim/) | locating villages and the newest districts | max 1 request/second, cached; data © OpenStreetMap contributors (ODbL) |
| [Esri World Shaded Relief and World Imagery](https://www.esri.com/en-us/legal/terms/full-master-agreement) | map background (no labels) | free with attribution; check Esri's terms before public deployment |
| [NCDC](https://ncdc.mohfw.gov.in/) | official outbreak reports | reuse with acknowledgement |
| [Local Government Directory](https://lgdirectory.gov.in) (Ministry of Panchayati Raj) | official codes of states, districts, sub-districts and villages | public government directory, read through its web service |
| [IndicTrans2](https://github.com/AI4Bharat/IndicTrans2) (AI4Bharat) | translating Indian-language articles; `indic_processor.py` is from IndicTransToolkit | MIT |
| Bing News RSS | coverage check in `evaluation/` only, never for collection | Microsoft terms |

## Project layout

```
app.py                     Streamlit dashboard (.streamlit/config.toml has its settings)
outbreak_monitor/
  discovery.py             finds article links in feeds, sitemaps and topic pages
  extractor.py             downloads articles and extracts the text
  processing.py            headline cleaning, keyword tagging, story grouping
  matcher.py               disease keywords in English and Indian languages
  translate.py             IndicTrans2 translation; indic_processor.py prepares its input
  relevance.py             relevance and event classification
  gazetteer.py, location.py  place reference data and outbreak location
  official.py              NCDC/IDSP weekly reports
  lgd.py                   LGD codes of states, districts, sub-districts and villages
  collector.py             one collection run; scheduler.py runs it every 30 minutes
  reprocess.py             re-applies the configuration to stored articles
  dashboard_data.py        data behind the dashboard
  boundaries.py            state and district boundaries, matched to LGD codes
  map_view.py              the India map: base map, district shading, borders, labels, report dots
  export_samples.py        sample records and schema
  config.py                reads the YAML configuration
  db.py                    database tables (SQLAlchemy)
  fetch.py                 HTTP requests: rate limit, retries, robots.txt
  urls.py, logs.py         URL normalisation; logging
config/                    YAML settings (see Configuration)
assets/                    dashboard logo
requirements.txt           Python packages
reference/                 place data, India, state and district boundaries, LGD code lists
evaluation/                evaluation report, labels and scripts
samples/                   example records and database schema
tests/                     pytest tests
Dockerfile, docker-compose.yml
data/                      database and logs (not in git)
models/                    translation model (downloaded, not in git)
```

## Troubleshooting

| Problem | What to do |
|---|---|
| The dashboard shows an old error after code or config changes | Stop Streamlit (Ctrl+C) and start it again; it keeps old modules loaded |
| `database is locked` | A long write was in progress, e.g. `outbreak_monitor.reprocess` or `outbreak_monitor.official` run by hand during a collection. Wait for it to finish and try again |
| *Refresh now* says a scheduled collection is already running | Only one collection runs at a time; its articles appear when it finishes |
| A source keeps failing | Check the errors in `collection_runs` or `listing_status`. Temporary errors are retried; a site that keeps failing can be disabled in `config/sources.yaml` |
| No official reports | The NCDC site can be slow. Run `python -m outbreak_monitor.official` and check the log |
| A keyword change has no effect | It applies from the next run, or run `python -m outbreak_monitor.reprocess` |
| "Translation model not installed" in the log | Run `python -m outbreak_monitor.translate --download` (after `hf auth login`, see Quick start). Until then regional-language sources are skipped |
| `ModuleNotFoundError` | Activate the virtual environment and run `pip install -r requirements.txt` |

## Disclaimer

This is a prototype that reads public news. It shows what the news reports, not confirmed outbreaks or case numbers, and every report should be checked against official IDSP data before any action is taken.

Coverage depends on what the news covers. Sources are in English and 12 Indian languages; languages such as Nepali, Khasi and Mizo are not covered yet, and some smaller states and union territories have had no outbreak news in the period collected (as of 24 November 2026: Andaman and Nicobar Islands, Dadra and Nagar Haveli and Daman and Diu, Ladakh, Lakshadweep, Meghalaya, Sikkim). The dashboard's *About the data* tab lists them from the current data. A place with no reports on the map is not necessarily free of disease.

## Known limitations

- Classification and location are rule-based. Some reports are missed or placed wrongly (see Evaluation), so uncertain locations are flagged and the register has a review column.
- Translation is automatic and the names in the 12 languages have not yet been reviewed by native speakers.
- History is limited: most regional sites have no usable archive, so the data goes back about 45 days, and alerts compare against only those weeks.
- IDSP weekly reports arrive about two months late, so news and official records can only be compared later.
- The SOI district boundaries predate the newest districts: the 62 districts created since (e.g. Andhra Pradesh's 2022 districts, Rajasthan's of 2023, Ladakh's and Delhi's latest) are shown inside the district they came from. Few localities get a village code.
- Most news sites' terms of use restrict automated collection; each needs permission before production use (see Responsible use).
