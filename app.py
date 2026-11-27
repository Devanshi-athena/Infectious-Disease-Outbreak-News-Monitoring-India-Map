"""Outbreak news dashboard: interactive India map, filters, article register and review.

    streamlit run app.py
"""

import base64
import html
import logging
import math
import threading
from datetime import datetime, timedelta
from pathlib import Path

import altair as alt
import streamlit as st
from streamlit_folium import st_folium

from outbreak_monitor.config import load_disease_config
from outbreak_monitor.dashboard_data import (BASELINE_WEEKS, EVENT_COLOURS, EVENT_LABELS, FIRST_REPORT_WEEKS, IST,
                                             OFFICIAL_COLOUR, REVIEW_OPTIONS, RISE_FACTOR, RISE_MIN_REPORTS, VIEWS,
                                             Filters, apply_filters, district_zoom_options, filter_official, href,
                                             latest_reports, lgd_label, load_articles, load_official, not_on_map,
                                             states_without_reports,
                                             official_state_disease_table, options,
                                             reports_by_state, save_reviews, state_disease_table,
                                             outbreak_alerts, outbreak_events, summary, to_ist, event_text)
from outbreak_monitor.db import get_session_factory
from outbreak_monitor.lgd import load as load_lgd
from outbreak_monitor.map_view import (MODES, alert_districts, boundary_of, build_map, district_counts,
                                       news_items, official_items, state_bounds)

LOGO = Path(__file__).parent / "assets" / "aplyd_logo.png"
st.set_page_config(page_title="Outbreak News Monitor · India", page_icon="🩺", layout="wide")
factory = get_session_factory()

# Static styling only (no data is interpolated into this block). No blank lines inside: Markdown would
# end the HTML block there and print the rest as text.
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --navy: #0B2545; --blue: #1554A6; --line: #E2E8F0; --muted: #5A687D; --text: #172033;
          --card: #FFFFFF; --soft: #F1F4F9; --shadow: 0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.08); }
  html, body, [class*="css"] { font-family: Inter, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; }
  .block-container { padding-top: 1.6rem; max-width: 1560px; }
  [data-testid="stSidebarHeader"] { height: 2.25rem; padding-top: .5rem; padding-bottom: 0; }
  [data-testid="stSidebarUserContent"] { padding-top: .25rem; }
  header[data-testid="stHeader"] { background: transparent; }
  /* header band */
  .hero { background: linear-gradient(120deg, #0B2545 0%, #13396B 60%, #1554A6 100%); color: #FFFFFF;
          border-radius: 16px; padding: 1.4rem 1.7rem 1.3rem 1.7rem; box-shadow: var(--shadow);
          display: flex; justify-content: space-between; align-items: flex-end; gap: 1.5rem; flex-wrap: wrap; }
  .hero .eyebrow { font-size: .72rem; font-weight: 600; letter-spacing: .16em; text-transform: uppercase;
          color: #9CC3F5; }
  .hero h1 { color: #FFFFFF; font-size: 2rem; font-weight: 700; letter-spacing: -.02em; margin: .3rem 0 .25rem 0;
          padding: 0; line-height: 1.15; }
  .hero .sub { color: #D3E2F5; font-size: .98rem; max-width: 760px; }
  .hero .status { display: flex; flex-direction: column; align-items: flex-start; gap: .45rem; }
  .pill { display: inline-flex; align-items: center; gap: .45rem; background: rgba(255,255,255,.12);
          border: 1px solid rgba(255,255,255,.22); color: #FFFFFF; border-radius: 999px; padding: .32rem .8rem;
          font-size: .8rem; font-weight: 500; white-space: nowrap; }
  .pill .dot { width: 8px; height: 8px; border-radius: 50%; background: #4ADE80;
          box-shadow: 0 0 0 3px rgba(74,222,128,.25); }
  .notice { display: flex; gap: .6rem; align-items: flex-start; background: #FFF8EB; border: 1px solid #F6DFB0;
          border-radius: 12px; padding: .6rem .9rem; font-size: .85rem; color: #6B4E16; margin: .8rem 0 1.1rem 0; }
  /* key figures */
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); gap: .85rem;
          margin-bottom: 1.1rem; }
  .kpi { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: .9rem 1rem;
          box-shadow: var(--shadow); display: flex; gap: .85rem; align-items: center; }
  .kpi .icon { flex: 0 0 42px; height: 42px; border-radius: 11px; display: flex; align-items: center;
          justify-content: center; }
  .kpi .icon svg { width: 21px; height: 21px; }
  .kpi > div:last-child { min-width: 0; }
  .kpi .label { font-size: .72rem; font-weight: 600; letter-spacing: .05em; text-transform: uppercase;
          color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .kpi .value { font-size: 1.55rem; font-weight: 700; color: var(--text); letter-spacing: -.02em; line-height: 1.2; }
  .kpi .value.small { font-size: 1.05rem; padding-top: .2rem; white-space: nowrap; }
  .kpi .hint { font-size: .75rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* cards and panels */
  .panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 1rem 1.05rem;
          box-shadow: var(--shadow); margin-bottom: .9rem; }
  .panel-title { font-size: .95rem; font-weight: 700; color: var(--text); margin: 0 0 .75rem 0;
          display: flex; justify-content: space-between; align-items: baseline; }
  .panel-title small { font-weight: 500; color: var(--muted); font-size: .75rem; white-space: nowrap; }
  .panel.scroll { max-height: 440px; overflow-y: auto; }   /* keeps the side panel no taller than the map */
  .report { border: 1px solid var(--line); border-left-width: 4px; border-radius: 10px; padding: .6rem .75rem;
          margin-bottom: .55rem; background: #FFFFFF; transition: box-shadow .15s ease, transform .15s ease; }
  .report:hover { box-shadow: 0 4px 12px rgba(16,24,40,.08); transform: translateY(-1px); }
  .chip { display: inline-block; font-size: .68rem; font-weight: 600; letter-spacing: .03em; border-radius: 999px;
          padding: 2px 9px; }
  .report .title { font-size: .92rem; font-weight: 600; line-height: 1.35; margin: .4rem 0 .3rem 0; }
  .report .title a { color: var(--text); text-decoration: none; }
  .report .title a:hover { color: var(--blue); }
  .report .meta { font-size: .78rem; color: var(--muted); }
  .bar-row { margin-bottom: .55rem; }
  .bar-row .top { display: flex; justify-content: space-between; font-size: .84rem; color: var(--text);
          margin-bottom: .2rem; }
  .bar-row .top b { font-weight: 600; }
  .bar { height: 7px; background: var(--soft); border-radius: 999px; overflow: hidden; }
  .bar span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #1554A6, #4F8FE0); }
  .caveat { font-size: .74rem; color: #8592A6; margin-top: .5rem; }
  /* map frame */
  div[data-testid="stCustomComponentV1"] iframe, iframe[title="streamlit_folium.st_folium"] {
          border-radius: 14px; border: 1px solid var(--line); box-shadow: var(--shadow); }
  /* tabs */
  .stTabs [data-baseweb="tab-list"] { gap: .4rem; border-bottom: 1px solid var(--line); }
  .stTabs [data-baseweb="tab"] { font-weight: 600; padding: .55rem .9rem; border-radius: 10px 10px 0 0; }
  .stTabs [aria-selected="true"] { background: #FFFFFF; color: var(--blue); }
  /* sidebar */
  section[data-testid="stSidebar"] .logo { display: block; width: 150px; max-width: 70%; margin: 0 0 1rem 0; }
  section[data-testid="stSidebar"] .brand { display: flex; gap: .65rem; align-items: center; padding: .2rem 0 .9rem 0;
          border-bottom: 1px solid var(--line); margin-bottom: .9rem; }
  section[data-testid="stSidebar"] .brand .mark { width: 38px; height: 38px; border-radius: 10px;
          background: linear-gradient(135deg, #0B2545, #1554A6); display: flex; align-items: center;
          justify-content: center; }
  section[data-testid="stSidebar"] .brand .name { font-weight: 700; font-size: .98rem; color: var(--text);
          line-height: 1.2; }
  section[data-testid="stSidebar"] .brand .tag { font-size: .74rem; color: var(--muted); }
  section[data-testid="stSidebar"] .section { font-size: .72rem; font-weight: 700; letter-spacing: .1em;
          text-transform: uppercase; color: var(--muted); margin: 1rem 0 .3rem 0; }
  .footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: .5rem; border-top: 1px solid var(--line);
          margin-top: 2rem; padding-top: .9rem; font-size: .78rem; color: #8592A6; }
</style>
""", unsafe_allow_html=True)

# Small line icons (static SVG).
ICONS = {
    "news": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round"><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2'
            'Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8M15 18h-5M10 6h8v4h-8z"/></svg>',
    "pin": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
           'stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>'
           '<circle cx="12" cy="10" r="3"/></svg>',
    "layers": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round"><path d="m12 2 10 5-10 5L2 7l10-5Z"/><path d="m2 17 10 5 10-5"/>'
              '<path d="m2 12 10 5 10-5"/></svg>',
    "shield": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
              'stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/>'
              '<path d="m9 12 2 2 4-4"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
             'stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
    "pulse": '<svg viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" '
             'stroke-linejoin="round" width="20" height="20"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
}


@st.cache_data(ttl=60, show_spinner=False)
def load():
    with factory() as session:
        return load_articles(session)


@st.cache_data(ttl=60, show_spinner=False)
def load_official_reports():
    with factory() as session:
        return load_official(session)


# Map data and alerts for a set of filters: cached, so the page does not recompute them on every click
@st.cache_data(ttl=60, show_spinner=False)
def map_news_items(key: tuple) -> list[dict]:
    return news_items(apply_filters(load(), Filters(*key)))


@st.cache_data(ttl=60, show_spinner=False)
def map_official_items(diseases: tuple, states: tuple) -> list[dict]:
    return official_items(filter_official(load_official_reports(), list(diseases), list(states)))


@st.cache_data(ttl=60, show_spinner=False)
def cached_alerts(diseases: tuple, states: tuple, as_of=None):
    """Alerts for the disease and state filters, as of the end of the chosen period (default: the latest report)."""
    return outbreak_alerts(apply_filters(load(), Filters(diseases=list(diseases), states=list(states))), as_of)



@st.cache_resource(show_spinner=False)
def refresh_job() -> dict:
    """The "Refresh now" run in the background, shared by everyone using this dashboard. done_id counts finished
    runs: a page whose data is older than the last finished run reloads it."""
    return {"thread": None, "started": None, "finished": None, "result": None, "done_id": 0,
            "lock": threading.Lock()}


def refresh_running() -> bool:
    job = refresh_job()
    return job["thread"] is not None and job["thread"].is_alive()


def start_refresh():
    """A quick run (feeds and news sitemaps since the last run) in a background thread, so the dashboard stays
    usable. A second click while it runs does nothing; a scheduled run already in progress makes it skip."""
    job = refresh_job()

    def work():
        try:
            from outbreak_monitor.collector import run_and_report  # heavy imports only when refreshing
            job["result"] = run_and_report(factory, quick=True)
        except Exception as exc:  # shown in the sidebar
            logging.getLogger(__name__).exception("Refresh now failed")
            job["result"] = {"status": "failed", "new": 0, "error": f"{type(exc).__name__}: {exc}"}
        job["finished"] = datetime.now(IST)
        job["done_id"] += 1

    with job["lock"]:   # two clicks at once (two browser tabs) start one run
        if refresh_running():
            return
        job.update(result=None, started=datetime.now(IST),
                   thread=threading.Thread(target=work, name="refresh-now", daemon=True))
        job["thread"].start()


REFRESH_MESSAGE_FOR = timedelta(minutes=10)   # how long "Refresh done" stays in the sidebar


@st.fragment(run_every=5)
def refresh_panel():
    """The refresh button and its progress; checks every 5 seconds and reloads the data once a run finishes."""
    job, running = refresh_job(), refresh_running()
    if st.button("Refresh now", icon=":material/refresh:", width="stretch", type="primary", disabled=running,
                 help="Collect the articles published since the last refresh (news feeds and sitemaps). "
                      "The full collection, with topic pages, runs every 30 minutes."):
        start_refresh()
        running = True
    if running:
        st.info(f"Collecting new articles (started {job['started']:%H:%M}). You can keep using the dashboard; "
                "it updates when the refresh is done.", icon=":material/sync:")
        return
    result = job["result"]
    if st.session_state["refresh_seen"] != job["done_id"]:   # a run finished after this page loaded its data
        st.session_state["refresh_seen"] = job["done_id"]
        st.cache_data.clear()
        st.rerun(scope="app")
    if result is None or datetime.now(IST) - job["finished"] > REFRESH_MESSAGE_FOR:
        return
    if result["status"] == "skipped":
        st.info("A scheduled collection was already running; its articles appear when it finishes.",
                icon=":material/schedule:")
    elif result["status"] == "failed":
        st.error(f"Refresh failed: {result.get('error') or 'see the log'}")
    else:
        st.success(f"Refresh done: {result['new']} new article(s).")


@st.cache_resource(show_spinner=False)
def logo_uri():
    """The APLYD logo as a data URI, so it can sit inside the sidebar's HTML."""
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()


def fmt_ist(dt, pattern="%d %b %Y, %H:%M IST"):
    return dt.strftime(pattern) if dt else "—"


def kpi(icon, colour, tint, label, value, hint, small=False):
    return (f'<div class="kpi"><div class="icon" style="color:{colour};background:{tint}">{ICONS[icon]}</div>'
            f'<div><div class="label">{label}</div><div class="value{" small" if small else ""}">{value}</div>'
            f'<div class="hint">{hint}</div></div></div>')


st.session_state.setdefault("refresh_seen", refresh_job()["done_id"])
articles = load()
official = load_official_reports()
no_news_states = states_without_reports(articles, load_lgd("states"))
opts = options(articles)
if not official.empty:  # official records can name diseases and states no article mentions yet
    opts["diseases"] = sorted(set(opts["diseases"]) | {d for ds in official["diseases"] for d in ds})
    opts["states"] = sorted(set(opts["states"]) | set(official["state"].dropna()))

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown(f'<img class="logo" src="{logo_uri()}" alt="APLYD by Athena Infonomics">'
                f'<div class="brand"><div class="mark">{ICONS["pulse"]}</div><div>'
                '<div class="name">Outbreak Monitor</div><div class="tag">India · news and IDSP reports</div>'
                '</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="section">View</div>', unsafe_allow_html=True)
    view = st.radio("Show", list(VIEWS), format_func=VIEWS.get, index=0, label_visibility="collapsed")
    st.markdown('<div class="section">Filters</div>', unsafe_allow_html=True)
    disease_config = load_disease_config()
    group_of = {d.name: d.category for d in disease_config.monitored}
    groups = list(dict.fromkeys(d.category for d in disease_config.monitored))  # config order
    chosen_groups = st.multiselect("Disease group", groups, placeholder="All disease groups")
    disease_options = [d for d in opts["diseases"] if not chosen_groups or group_of.get(d) in chosen_groups]
    chosen_diseases = st.multiselect("Disease", disease_options, placeholder="All diseases")
    # A group with no disease picked means every disease in that group.
    diseases = chosen_diseases or (disease_options if chosen_groups else [])
    states = st.multiselect("State / UT", opts["states"], placeholder="All states")
    languages = st.multiselect("Language", opts["languages"], placeholder="All languages",
                               help="Language the article was published in. Articles in Indian languages are "
                                    "shown with an English translation of the headline.")
    sources = st.multiselect("Source", opts["sources"], placeholder="All sources")
    lookback = disease_config.settings.lookback_days
    if not articles.empty and articles["published_date"].notna().any():
        latest = max(articles["published_date"].dropna())
        dates = st.date_input("Publication date", (latest - timedelta(days=lookback), latest), format="DD/MM/YYYY")
    else:
        dates = ()
    st.markdown('<div class="section">Map layers</div>', unsafe_allow_html=True)
    map_mode = st.radio("Map shows", list(MODES), format_func=MODES.get, index=0, horizontal=False,
                        help="Official IDSP records are the weekly outbreak reports of the Integrated Disease "
                             "Surveillance Programme (NCDC). They are published about two months late, so the "
                             "date filter does not apply to them.")
    include_abroad = st.toggle("Reports outside India (list only)", value=False)
    st.markdown('<div class="section">Data</div>', unsafe_allow_html=True)
    refresh_panel()

start, end = (dates[0], dates[1]) if isinstance(dates, (tuple, list)) and len(dates) == 2 else (None, None)
filters = Filters(view=view, diseases=diseases, states=states, sources=sources, languages=languages, start=start,
                  end=end, include_abroad=include_abroad)
matching = apply_filters(articles, filters)
filter_key = (view, list(diseases), list(states), list(sources), list(languages), start, end, include_abroad)
news_on_map = map_news_items(tuple(tuple(v) if isinstance(v, list) else v for v in filter_key))
unmapped = not_on_map(matching)
official_matching = filter_official(official, diseases, states)
official_on_map = map_official_items(tuple(diseases), tuple(states))
alerts = cached_alerts(tuple(diseases), tuple(states), end)

with factory() as session:
    totals = summary(matching, session)
with st.sidebar:
    st.caption(f"Last successful refresh: **{fmt_ist(totals['last_refresh'])}**")

# ---------------------------------------------------------------- header
st.markdown(f"""
<div class="hero">
  <div>
    <div class="eyebrow">Public health news monitoring · India</div>
    <h1>Infectious Disease Outbreak Monitor</h1>
    <div class="sub">Outbreak reports from Indian news media and the weekly IDSP outbreak reports, located and
    mapped by state, district and locality.</div>
  </div>
  <div class="status">
    <span class="pill"><span class="dot"></span>Updated {fmt_ist(totals['last_refresh'], "%d %b %Y, %H:%M IST")}</span>
    <span class="pill">Refreshes every {disease_config.settings.refresh_interval_minutes} minutes</span>
  </div>
</div>
<div class="notice"><span>ⓘ</span><div><b>Prototype.</b> A news-based monitoring tool, not an official surveillance
or early-warning system. News coverage can be incomplete or delayed, and the number of articles is <b>not</b> a
measure of disease incidence or outbreak severity. Sources cover English and 12 Indian languages; see
<i>About the data</i> for coverage gaps.</div></div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- key figures
st.markdown('<div class="kpis">'
            + kpi("news", "#1554A6", "#E8F0FB", "Articles", f"{totals['articles']:,}", "matching the filters")
            + kpi("layers", "#D64545", "#FDECEC", "Outbreaks mapped", f"{len(news_on_map):,}",
                  "a disease in one place in one week, however many reports")
            + kpi("pin", "#7C3AED", "#F1EBFE", "Locations", f"{totals['locations']:,}", "states, districts, towns")
            + kpi("shield", OFFICIAL_COLOUR, "#E6F4EE", "Official records", f"{len(official_matching):,}",
                  "IDSP weekly reports")
            + kpi("clock", "#E0870B", "#FDF3E3", "Last refresh", fmt_ist(totals["last_refresh"], "%d %b, %H:%M"),
                  "Indian Standard Time", small=True)
            + '</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- map + side panel
map_col, side_col = st.columns([2.4, 1], gap="large")

with map_col:
    district_centres = district_zoom_options(matching)
    bounds_of_state = state_bounds()
    focus_states = [s for s in opts["states"] if s in bounds_of_state]
    zoom_choice = st.selectbox("Focus the map", ["All of India"] + focus_states + list(district_centres),
                               label_visibility="collapsed")
    selected_district = None
    if zoom_choice in district_centres:
        lat, lon, code = district_centres[zoom_choice]
        focus, focus_zoom = [[lat - 0.45, lon - 0.45], [lat + 0.45, lon + 0.45]], None
        selected_district = boundary_of(code, lat, lon)   # opens selected, with its reports
    elif zoom_choice in bounds_of_state:
        focus, focus_zoom = bounds_of_state[zoom_choice], 9
    elif len(states) == 1 and states[0] in bounds_of_state:
        focus, focus_zoom = bounds_of_state[states[0]], 9
    elif news_on_map and (states or chosen_diseases or sources or languages):
        lats, lons = [m["la"] for m in news_on_map], [m["lo"] for m in news_on_map]
        focus, focus_zoom = [[min(lats) - 0.5, min(lons) - 0.5], [max(lats) + 0.5, max(lons) + 0.5]], 9
    else:
        focus, focus_zoom = None, None
    fmap = build_map(news_on_map, official_on_map,
                     district_counts(news_on_map), district_counts(official_on_map),
                     alert_districts(alerts), map_mode, bounds=focus, max_zoom=focus_zoom,
                     select=selected_district)
    st_folium(fmap, height=740, use_container_width=True, returned_objects=[], key="map")
    st.caption("Districts are shaded by the number of reports (darker = more); districts where news reports are "
               "rising in the last 7 days are outlined in red. Hover a district for its numbers and latest reports; "
               "click it to see each report at its place on the map, with the full list beside it. A report carried "
               "by several publishers counts once. Boundaries follow the official map of India; all names are in "
               "English. The layer button (top right) switches the background to terrain or satellite."
               + (f" **{len(unmapped)} matching article(s) from India name no state or district and are not on the "
                  "map**; they are listed in the *Not on the map* tab below." if not unmapped.empty else ""))

with side_col:
    reports = latest_reports(matching, limit=8)
    cards = []
    for r in reports:
        colour = EVENT_COLOURS[r["event_status"]]
        cards.append(
            f'<div class="report" style="border-left-color:{colour}">'
            f'<span class="chip" style="background:{colour}1A;color:{colour}">{EVENT_LABELS[r["event_status"]]}</span> '
            f'<span class="chip" style="background:#F1F4F9;color:#334155">{html.escape(r["diseases"] or "—")}</span>'
            f'<div class="title"><a href="{href(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(r["headline"])}</a></div>'
            + (f'<div class="meta" style="margin:-.15rem 0 .3rem 0">{html.escape(r["headline_original"])}</div>'
               if r["headline_original"] else "")
            + f'<div class="meta">📍 {html.escape(r["place"])}<br>📅 {fmt_ist(r["published"], "%d %b %Y")}'
            f' · {html.escape(r["source"])} · {html.escape(r["language"])}</div></div>')
    st.markdown('<div class="panel scroll"><div class="panel-title">Latest reports<small>newest first</small></div>'
                + ("".join(cards) or '<div class="meta">No reports match the current filters.</div>')
                + '</div>', unsafe_allow_html=True)

    by_state = reports_by_state(matching)
    if by_state:
        top = by_state[0][1]
        bars = "".join(f'<div class="bar-row"><div class="top"><span>{html.escape(s)}</span><b>{n}</b></div>'
                       f'<div class="bar"><span style="width:{max(4, round(100 * n / top))}%"></span></div></div>'
                       for s, n in by_state)
        st.markdown('<div class="panel"><div class="panel-title">Most reported states<small>stories</small></div>'
                    + bars + '<div class="caveat">Number of news stories, not cases. Coverage differs between '
                    'regions and outlets.</div></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- register + about
events = outbreak_events(matching)
alerts_tab, events_tab, register_tab, states_tab, unmapped_tab, official_tab, about_tab = st.tabs([
    f":material/notifications_active: Alerts ({len(alerts)})",
    f":material/hub: Outbreaks ({len(events)})",
    f":material/table_rows: Article register ({len(matching)})",
    ":material/grid_on: States × diseases",
    f":material/location_off: Not on the map ({len(unmapped)})",
    f":material/verified: Official reports ({len(official_matching)})",
    ":material/info: About the data"])

# Sequential blue ramp (light = few, dark = many); text switches to white on the two darkest steps.
HEAT_STEPS = ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#104281"]


def heat_style(value, top: int) -> str:
    """Cell colour on a log scale (one state's dengue count can be 100x the others), blank for zero."""
    if not value:
        return "color: transparent"
    step = min(len(HEAT_STEPS) - 1, int(len(HEAT_STEPS) * math.log1p(value) / math.log1p(max(top, 1)) - 1e-9))
    return f"background-color: {HEAT_STEPS[step]}; color: {'#FFFFFF' if step >= 3 else '#172033'}"


ALERT_TINTS = {"Rise": "background-color: #FDE8E8; color: #9B1C1C; font-weight: 600",
               "First report": "background-color: #FEF3C7; color: #92400E; font-weight: 600"}
CONTENT_TYPES = {"outbreak_report": "Outbreak report", "public_health_response": "Response / preparedness",
                 "research": "Research", "awareness": "Awareness", "passing_mention": "Passing mention",
                 "other": "Other", "no_keyword": "No disease keyword"}
LINK = st.column_config.LinkColumn("Original", display_text="open ↗", width="small")
LGD_COLUMN = st.column_config.TextColumn("LGD code", width="small",
                                         help="Local Government Directory: state / district")


def day(d, pattern="%d %b %Y") -> str:
    return f"{d:{pattern}}" if d == d and d is not None else ""


with alerts_tab:
    if alerts.empty:
        st.info("No alerts for the selected diseases and states.")
    else:
        rises = int((alerts["alert"] == "Rise").sum())
        st.caption(f"News reports of cases in the 7 days up to **{day(alerts['as_of'].iloc[0])}** (the latest "
                   f"report), compared with the weeks before. **Rise** ({rises}): at least {RISE_MIN_REPORTS} reports "
                   f"and at least {RISE_FACTOR:g}× the weekly average of the {BASELINE_WEEKS} weeks before. **First "
                   f"report** ({len(alerts) - rises}): the disease is reported in a district where it was not in "
                   f"the {FIRST_REPORT_WEEKS} weeks before. A report seen at several publishers counts once; "
                   "preparedness and response news and past outbreaks are not counted. Follows the disease and "
                   "state filters, not the date filter. Alerts point to news coverage, to be checked against "
                   "official data, not confirmed outbreaks.")
        shown = alerts.assign(latest=alerts["latest"].map(day), lgd=[lgd_label(r) for r in alerts.itertuples()])[
            ["alert", "disease", "place", "reports", "weekly_average_before", "latest", "headline", "lgd",
             "article_url"]]
        st.dataframe(
            shown.style.map(lambda v: ALERT_TINTS.get(v, ""), subset=["alert"]),
            hide_index=True, width="stretch", height=min(35 * len(shown) + 38, 520),
            column_config={
                "alert": st.column_config.TextColumn("Alert", width=110),
                "disease": st.column_config.TextColumn("Disease", width="medium"),
                "place": st.column_config.TextColumn("Place", width="medium"),
                "reports": st.column_config.NumberColumn("Reports, 7 days", format="%d", width=110,
                                                         help="Each story once, however many publishers carried it"),
                "weekly_average_before": st.column_config.NumberColumn(
                    "Weekly avg. before", format="%.1f", width=150,
                    help=f"Average reports per week in the {BASELINE_WEEKS} weeks before"),
                "latest": st.column_config.TextColumn("Latest report", width=110),
                "headline": st.column_config.TextColumn("Latest headline", width="large"),
                "lgd": LGD_COLUMN, "article_url": LINK,
            })

with events_tab:
    if events.empty:
        st.info("No outbreaks match the current filters.")
    else:
        st.caption("Relevant reports of the **same disease in the same place in the same week** (Monday to Sunday) "
                   "are grouped as one outbreak, whatever the source or language. The place is the district, or "
                   "the state when a report names no district. Newest week first, most reported first. Follows "
                   "all filters in the sidebar.")
        shown = events.assign(
            week=events["week"].map(day),
            dates=[day(a, "%d %b") if a == b else f"{day(a, '%d %b')} – {day(b, '%d %b')}"
                   for a, b in zip(events["first_seen"], events["last_seen"])],
            event_label=events["event_status"].map(event_text),
            lgd=[lgd_label(r) for r in events.itertuples()])[
            ["week", "disease", "place", "reports", "sources", "languages", "dates", "event_label", "headline", "lgd",
             "article_url"]]
        st.dataframe(
            shown.style.map(lambda v: "font-weight: 700" if v > 1 else "color: #64748B", subset=["reports"]),
            hide_index=True, width="stretch", height=480,
            column_config={
                "week": st.column_config.TextColumn("Week of", width=105),
                "disease": st.column_config.TextColumn("Disease", width="medium"),
                "place": st.column_config.TextColumn("Place", width="medium"),
                "reports": st.column_config.NumberColumn("Reports", format="%d", width=75,
                                                         help="Each story once, however many publishers carried it"),
                "sources": st.column_config.NumberColumn("Sources", format="%d", width=75),
                "languages": st.column_config.TextColumn("Languages", width=150),
                "dates": st.column_config.TextColumn("Reported", width=125),
                "event_label": st.column_config.TextColumn("Event", width=125),
                "headline": st.column_config.TextColumn("First headline", width="large"),
                "lgd": LGD_COLUMN, "article_url": LINK,
            })

with states_tab:
    disease_order = [d.name for d in disease_config.monitored]
    measure = st.radio("Count", ["News reports", "Official IDSP records"], horizontal=True,
                       label_visibility="collapsed",
                       help="News reports count each story once (the same report from several publishers is one "
                            "report). Official records are outbreaks in the IDSP weekly reports.")
    if measure == "News reports":
        table = state_disease_table(matching, disease_order)
        st.caption("Number of news reports per state and disease, for the articles matching the view and filters in "
                   "the sidebar. Only reports placed in a state are counted (see *Not on the map* for the others). "
                   "This measures news coverage, not the number of cases.")
    else:
        table = official_state_disease_table(official_matching, disease_order)
        st.caption("Number of outbreaks in the IDSP weekly reports per state and disease, for the disease and state "
                   "filters in the sidebar (the publication-date filter does not apply).")
    if table.empty:
        st.info("No reports match the current filters.")
    else:
        top = int(table.drop(columns="Total").to_numpy().max())
        styled = (table.style
                  .map(lambda v: heat_style(v, top), subset=[c for c in table.columns if c != "Total"])
                  .map(lambda _: "font-weight: 600", subset=["Total"]))
        st.dataframe(styled, width="stretch", height=min(38 * len(table) + 40, 560))
        legend = "".join(f'<span style="display:inline-block;width:22px;height:12px;background:{c};'
                         f'margin-right:2px;border-radius:2px"></span>' for c in HEAT_STEPS)
        st.markdown(f'<div class="caveat">Fewer {legend} more reports (shading on a log scale; empty cells: none).'
                    '</div>', unsafe_allow_html=True)

        st.markdown("#### States with the most reports of one disease")
        ranked_diseases = [d for d in table.columns if d != "Total"]
        chosen = st.selectbox("Disease", ranked_diseases, label_visibility="collapsed",
                              index=ranked_diseases.index(max(ranked_diseases, key=lambda d: table[d].sum())))
        ranking = table[chosen][table[chosen] > 0].sort_values(ascending=False).rename("reports").reset_index()
        chart = (alt.Chart(ranking)
                 .mark_bar(color="#2a78d6", cornerRadiusEnd=4, size=14)
                 .encode(x=alt.X("reports:Q", title="Reports", axis=alt.Axis(grid=True, gridColor="#E2E8F0",
                                                                               tickMinStep=1, format="d")),
                         y=alt.Y("State / UT:N", sort="-x", title=None),
                         tooltip=[alt.Tooltip("State / UT:N", title="State / UT"),
                                  alt.Tooltip("reports:Q", title=f"{chosen} reports")])
                 .properties(height=max(120, 24 * len(ranking))))
        labels = chart.mark_text(align="left", dx=4, color="#5A687D", fontSize=11).encode(text="reports:Q")
        st.altair_chart((chart + labels).configure_view(stroke=None), width="stretch")

with unmapped_tab:
    if unmapped.empty:
        st.info("Every matching article from India has been placed on the map.")
    else:
        st.caption("Articles from India that match the filters but are **not on the map**, because they name no "
                   "state or district (national stories, or the place could not be read). With *Relevant outbreak "
                   "reports* selected, these are relevant reports too. They have no state, so they are hidden "
                   "while a state is selected in the filters.")
        st.dataframe(
            unmapped.assign(
                published=[fmt_ist(to_ist(t.to_pydatetime()), "%d %b %Y %H:%M") if t == t else ""
                           for t in unmapped["published_at"]],
                diseases_text=unmapped["diseases"].map(", ".join),
                event_label=unmapped["event_status"].map(event_text),
                headline_original=unmapped["headline_original"].map(lambda v: v if isinstance(v, str) else ""),
            )[["published", "diseases_text", "event_label", "headline", "headline_original", "why_unmapped",
               "source_name", "language_name", "article_url"]],
            hide_index=True, width="stretch", height=420,
            column_config={
                "published": "Published (IST)",
                "headline": st.column_config.TextColumn("Headline", width="large"),
                "headline_original": st.column_config.TextColumn("Original headline", width="medium"),
                "diseases_text": "Disease", "event_label": "Event",
                "why_unmapped": st.column_config.TextColumn("Why not on the map", width="medium"),
                "source_name": "Source", "language_name": "Language",
                "article_url": st.column_config.LinkColumn("Original", display_text="open ↗"),
            })

with register_tab:
    if matching.empty:
        st.info("No articles match the current filters.")
    else:
        table = matching.assign(
            published=[fmt_ist(to_ist(t.to_pydatetime()), "%d %b %Y %H:%M") if t == t else ""
                       for t in matching["published_at"]],
            diseases_text=matching["diseases"].map(", ".join),
            review_flag=matching["location_review"].map(lambda v: "⚠️" if v else ""),
            event_label=matching["event_status"].map(event_text),
            content_type=matching["content_type"].map(lambda v: CONTENT_TYPES.get(v, "—")),
            location_level=matching["location_level"].map(lambda v: v if isinstance(v, str) and v != "country" else "—"),
            location_confidence=matching["location_confidence"].map(
                lambda v: v if isinstance(v, str) and v != "none" else "—"),
            headline_original=matching["headline_original"].map(lambda v: v if isinstance(v, str) else ""),
            lgd=[lgd_label(r) for r in matching.itertuples()],
        )[["article_id", "published", "diseases_text", "place", "event_label", "headline", "headline_original",
           "source_name", "language_name", "article_url", "lgd", "location_level", "location_confidence",
           "review_flag", "content_type", "relevance", "relevance_status"]]
        edited = st.data_editor(
            table, hide_index=True, width="stretch", height=460,
            key=f"articles-{hash(tuple(table['article_id']))}",   # edits never move to another article
            disabled=[c for c in table.columns if c != "relevance_status"],
            column_config={
                "article_id": None,
                "published": "Published (IST)",
                "headline": st.column_config.TextColumn("Headline", width="large",
                                                        help="English; translated for articles in Indian languages"),
                "headline_original": st.column_config.TextColumn("Original headline", width="medium",
                                                                 help="As published, for translated articles"),
                "diseases_text": "Disease",
                "place": "Reported location",
                "lgd": st.column_config.TextColumn("LGD code", help="Local Government Directory codes: state / "
                                                   "district / sub-district / village (where identified)"),
                "location_level": "Level",
                "location_confidence": "Location confidence",
                "review_flag": st.column_config.TextColumn("Check", help="Location flagged for review"),
                "event_label": "Event",
                "content_type": "Type",
                "source_name": "Source",
                "language_name": "Language",
                "article_url": st.column_config.LinkColumn("Original", display_text="open ↗"),
                "relevance": st.column_config.TextColumn("Auto relevance"),
                "relevance_status": st.column_config.SelectboxColumn(
                    "Your review", options=list(REVIEW_OPTIONS),
                    help="Mark an article relevant or irrelevant. Your decision overrides the automatic one."),
            })
        changes = {int(a): s for a, s, old in zip(edited["article_id"], edited["relevance_status"],
                                                  table["relevance_status"]) if s != old}
        if st.button(f"Save review changes ({len(changes)})", icon=":material/save:", disabled=not changes,
                     type="primary"):
            with factory() as session:
                saved = save_reviews(session, changes)
            st.cache_data.clear()
            st.toast(f"Saved {saved} review decision(s).")
            st.rerun()

with official_tab:
    if official_matching.empty:
        st.info("No official outbreak records match the current filters (or none have been collected yet).")
    else:
        weeks = official_matching["week_label"].unique()
        st.caption(f"IDSP weekly outbreak reports (NCDC), {weeks[-1]} to {weeks[0]}. Each row is one outbreak "
                   "as reported by the state; open the weekly PDF to verify. Disease and state filters apply, the "
                   "publication-date filter does not.")
        official_table = official_matching.assign(
            mapped=official_matching["diseases"].map(", ".join),
            place_level=official_matching["location_level"].fillna("not located"))
        st.dataframe(
            official_table[["report_id", "week_label", "state", "district", "disease_raw", "mapped", "cases",
                            "deaths", "onset_date", "reported_date", "status", "place_level", "action_taken",
                            "report_url"]],
            hide_index=True, width="stretch", height=460,
            column_config={
                "report_id": "IDSP ID", "week_label": "Report week", "state": "State / UT", "district": "District",
                "disease_raw": "Disease (as reported)", "mapped": "Monitored disease", "cases": "Cases",
                "deaths": "Deaths",
                "onset_date": st.column_config.DateColumn("Outbreak start", format="DD/MM/YYYY"),
                "reported_date": st.column_config.DateColumn("Reported", format="DD/MM/YYYY"),
                "status": "Status", "place_level": "Mapped at",
                "action_taken": st.column_config.TextColumn("Comments / action taken", width="large"),
                "report_url": st.column_config.LinkColumn("Source", display_text="PDF ↗"),
            })

with about_tab:
    st.markdown("""
**How articles are collected.** Indian news sites in English and 12 Indian languages (Hindi, Bengali, Marathi,
Telugu, Tamil, Gujarati, Urdu, Kannada, Odia, Malayalam, Punjabi and Assamese) are read every 30 minutes through
their RSS feeds, news sitemaps and topic pages, respecting each site's robots.txt and limiting requests to one per
second per site. Only articles that mention a configured disease keyword are kept.

**How Indian-language articles are handled.** Articles are first screened in their own language with disease names
and general health words (fever, epidemic, jaundice) in that language. Only those that pass are translated to
English, offline with the open-source IndicTrans2 model (AI4Bharat, IIT Madras): the headline, the opening and the
sentences that mention a disease. Classification and location use the translation; the original headline is shown
alongside it. Translations are automatic and can contain errors.

**How they are classified.** Rule-based analysis of each article's sentences separates outbreak reports and
public-health responses from research, awareness pieces and passing mentions, and marks reports as new,
suspected, historical or unclear. Your review decisions override the automatic classification.

**How they are located.** Place names are recognised with spaCy and matched against GeoNames reference data for
all states, districts and towns. Datelines (the publisher's office) are ignored. Each report is mapped at the most
specific level the article supports. Villages are located with OpenStreetMap Nominatim.

**Official reports.** The weekly outbreak reports of the Integrated Disease Surveillance Programme (IDSP),
published by NCDC as PDFs, are read automatically: one record per outbreak with state, district, disease, cases,
deaths, dates, status and the action taken. They appear about two months after the reporting week and are shown
as their own layer, so they can be compared with the news reports.

**Diseases.** The conditions of the Integrated Disease Surveillance Programme (IDSP) list: the IHIP S, P and L
forms, plus food poisoning and COVID-19. Bird flu is counted only when a person is affected; bird flu in poultry
and wild birds is animal health and is not shown.

**The map.** India only, with its official boundary; everything outside it is covered. State and district
boundaries are linked to the Local Government Directory (LGD) codes, and every name on the map is written by this
tool in English: the background shows terrain or satellite imagery without any labels. Districts are shaded by the
number of news reports placed in them; reports placed only at state level are not counted in a district. District
boundaries are of 2023: the 15 districts created since (in Ladakh, Delhi, Andhra Pradesh, Gujarat, Haryana, Goa and
Nagaland) are counted in the district they were carved from, and Delhi is shown with its earlier 11 districts.

**Data and attribution.** India's boundary: official boundary of India as per the Survey of India, compiled by the
DataMeet India community (CC0). District boundaries: INDIAN-SHAPEFILES by Akula Datta (MIT licence), matched to LGD.
Terrain and satellite background © Esri. Place data © GeoNames (CC BY 4.0).
Geocoding © OpenStreetMap contributors (ODbL), via Nominatim. Translation: IndicTrans2 by AI4Bharat (MIT licence).
Articles © their publishers; follow the links to read the originals.
""")
    no_news = ", ".join(no_news_states) or "none"
    st.warning(f"""
**Disclaimer.** This is a prototype that reads public news. It shows what the news reports, not confirmed outbreaks
or case numbers; check every report against official IDSP data before acting on it. Classification, location and
translation are automatic and can be wrong; uncertain locations are flagged for review.

**Coverage.** News sites in English and 12 Indian languages. Languages such as Nepali, Khasi and Mizo are not
covered yet. States and union territories with no relevant news report so far: {no_news}. A place with no reports
on the map is not necessarily free of disease.
""", icon=":material/info:")

st.markdown('<div class="footer"><span>Infectious Disease Outbreak Monitor · prototype</span>'
            '<span>Not an official surveillance or early-warning system. Articles © their publishers.</span></div>',
            unsafe_allow_html=True)
