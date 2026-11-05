"""Outbreak news dashboard: interactive India map, filters, article register and review.

    streamlit run app.py
"""

import base64
import html
from datetime import timedelta
from pathlib import Path

import folium
import streamlit as st
from folium.plugins import Fullscreen, MarkerCluster
from streamlit_folium import st_folium

from outbreak_monitor.config import load_disease_config
from outbreak_monitor.dashboard_data import (EVENT_COLOURS, EVENT_LABELS, OFFICIAL_COLOUR, REVIEW_OPTIONS, VIEWS,
                                             Filters, apply_filters, district_zoom_options, filter_official,
                                             latest_reports, load_articles, load_official, official_popup_html,
                                             options, reports_by_state, save_reviews, story_markers, summary, to_ist)
from outbreak_monitor.db import get_session_factory
from outbreak_monitor.gazetteer import india_mask, load_boundary

INDIA_BOUNDS = [[6.5, 68.0], [37.2, 97.5]]
PAN_LIMITS = [[3.0, 63.0], [40.0, 102.0]]   # keep the view on India
MAP_BACKGROUND = "#EEF2F7"                   # colour outside India's boundary
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


@st.cache_resource(show_spinner=False)
def india_shapes():
    """India's official boundary (outline) and a mask that hides everything outside it."""
    boundary = load_boundary()
    return boundary, india_mask(boundary)


def run_refresh():
    from outbreak_monitor.collector import run_and_report  # heavy imports only when refreshing
    with st.spinner("Collecting news and official reports… this can take a few minutes."):
        result = run_and_report(factory)
    st.cache_data.clear()
    st.session_state["refresh_result"] = result


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


articles = load()
official = load_official_reports()
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
    group_of = {d.name: d.category for d in disease_config.diseases}
    groups = list(dict.fromkeys(d.category for d in disease_config.diseases))  # config order
    chosen_groups = st.multiselect("Disease group", groups, placeholder="All disease groups")
    disease_options = [d for d in opts["diseases"] if not chosen_groups or group_of.get(d) in chosen_groups]
    chosen_diseases = st.multiselect("Disease", disease_options, placeholder="All diseases")
    # A group with no disease picked means every disease in that group.
    diseases = chosen_diseases or (disease_options if chosen_groups else [])
    states = st.multiselect("State / UT", opts["states"], placeholder="All states")
    sources = st.multiselect("Source", opts["sources"], placeholder="All sources")
    lookback = disease_config.settings.lookback_days
    if not articles.empty and articles["published_date"].notna().any():
        latest = max(articles["published_date"].dropna())
        dates = st.date_input("Publication date", (latest - timedelta(days=lookback), latest), format="DD/MM/YYYY")
    else:
        dates = ()
    st.markdown('<div class="section">Map layers</div>', unsafe_allow_html=True)
    show_official = st.toggle("Official IDSP reports", value=True,
                              help="Weekly outbreak reports of the Integrated Disease Surveillance Programme "
                                   "(NCDC). Published about two months late, so the date filter does not apply.")
    include_abroad = st.toggle("Reports outside India (list only)", value=False)
    st.markdown('<div class="section">Data</div>', unsafe_allow_html=True)
    if st.button("Refresh now", icon=":material/refresh:", width="stretch", type="primary",
                 help="Collect new articles and official reports now"):
        run_refresh()
    if result := st.session_state.get("refresh_result"):
        st.success(f"Refresh finished ({result['status']}): {result['new']} new article(s).")

start, end = (dates[0], dates[1]) if isinstance(dates, (tuple, list)) and len(dates) == 2 else (None, None)
filters = Filters(view=view, diseases=diseases, states=states, sources=sources, start=start, end=end,
                  include_abroad=include_abroad)
matching = apply_filters(articles, filters)
markers = story_markers(matching)
official_matching = filter_official(official, diseases, states)
official_mapped = (official_matching[official_matching["latitude"].notna()] if show_official
                   and not official_matching.empty else official_matching.iloc[0:0])

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
measure of disease incidence or outbreak severity.</div></div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- key figures
st.markdown('<div class="kpis">'
            + kpi("news", "#1554A6", "#E8F0FB", "Articles", f"{totals['articles']:,}", "matching the filters")
            + kpi("layers", "#D64545", "#FDECEC", "Stories mapped", f"{len(markers):,}",
                  "one marker per story")
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
    zoom_choice = st.selectbox("Focus the map", ["All of India"] + list(district_centres),
                               label_visibility="collapsed")

    fmap = folium.Map(location=[22.5, 80.0], zoom_start=5, tiles=None, control_scale=True, min_zoom=4,
                      max_bounds=True, min_lat=PAN_LIMITS[0][0], max_lat=PAN_LIMITS[1][0],
                      min_lon=PAN_LIMITS[0][1], max_lon=PAN_LIMITS[1][1],
                      zoomSnap=0.25, zoomDelta=0.5)  # fractional zoom so India fills the frame
    # Free tiles, no API key: a light base map by default, the full-colour OpenStreetMap as an option.
    folium.TileLayer("CartoDB positron", name="Light").add_to(fmap)
    folium.TileLayer("OpenStreetMap", name="Street map").add_to(fmap)
    fmap.get_root().header.add_child(folium.Element(
        f"<style>.leaflet-container {{ background: {MAP_BACKGROUND}; font-family: Inter, 'Segoe UI', Arial, sans-serif; }}"
        ".leaflet-popup-content-wrapper { border-radius: 10px; padding: 0; overflow: hidden;"
        " box-shadow: 0 8px 24px rgba(16,24,40,.18); }"
        ".leaflet-popup-content { margin: 0; }"
        ".leaflet-bar a, .leaflet-control-layers { border-radius: 8px !important; }"
        ".leaflet-control-attribution { font-size: 10px; background: rgba(255,255,255,.85) !important; }</style>"))
    # Only India: everything outside the official boundary is covered in the background colour, and India is outlined.
    boundary, mask = india_shapes()
    folium.GeoJson(mask, name="Outside India", control=False, interactive=False,
                   style_function=lambda _: {"fillColor": MAP_BACKGROUND, "fillOpacity": 1.0,
                                             "stroke": False}).add_to(fmap)
    folium.GeoJson(boundary, name="India boundary", control=False, interactive=False,
                   style_function=lambda _: {"fill": False, "color": "#13396B", "weight": 1.6,
                                             "opacity": 0.9}).add_to(fmap)
    cluster = MarkerCluster(
        name="Outbreak reports",
        icon_create_function="""
          function(cluster) {
            var n = cluster.getChildCount();
            var size = n < 10 ? 32 : (n < 50 ? 40 : 48);
            return L.divIcon({
              html: '<div style="width:' + size + 'px;height:' + size + 'px;line-height:' + size + 'px;'
                  + 'border-radius:50%;background:#1554A6;color:#fff;text-align:center;'
                  + 'font:600 13px Inter,Segoe UI,Arial,sans-serif;'
                  + 'box-shadow:0 0 0 5px rgba(21,84,166,.22),0 2px 6px rgba(0,0,0,.25)">'
                  + n + '</div>',
              className: '', iconSize: L.point(size, size)
            });
          }""").add_to(fmap)
    for m in markers:
        colour = EVENT_COLOURS.get(m.event_status, EVENT_COLOURS["unclear"])
        folium.Marker(
            [m.latitude, m.longitude],
            popup=folium.Popup(m.popup_html, max_width=340),
            tooltip=html.escape(m.headline if len(m.headline) <= 60 else m.headline[:57] + "…"),
            icon=folium.DivIcon(
                html=f'<div style="width:14px;height:14px;border-radius:50%;background:{colour};'
                     'border:2px solid #FFFFFF;box-shadow:0 0 0 1px rgba(0,0,0,.2),0 1px 4px rgba(0,0,0,.3)"></div>',
                icon_size=(18, 18), icon_anchor=(9, 9)),
        ).add_to(cluster)
    official_layer = folium.FeatureGroup(name="Official reports (IDSP)", show=True)
    for r in official_mapped.itertuples():
        folium.Marker(
            [r.latitude, r.longitude],
            popup=folium.Popup(official_popup_html(r), max_width=340),
            tooltip=html.escape(f"Official: {r.disease_raw or '—'} · {r.district or r.state or ''}"),
            icon=folium.DivIcon(
                html=f'<div style="width:12px;height:12px;background:{OFFICIAL_COLOUR};border:2px solid #FFFFFF;'
                     'border-radius:3px;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 1px 4px rgba(0,0,0,.3)"></div>',
                icon_size=(16, 16), icon_anchor=(8, 8)),
        ).add_to(official_layer)
    official_layer.add_to(fmap)
    legend = "".join(f'<div style="margin:4px 0;display:flex;align-items:center"><span style="display:inline-block;'
                     f'width:11px;height:11px;border-radius:50%;background:{EVENT_COLOURS[k]};border:2px solid #fff;'
                     f'box-shadow:0 0 0 1px rgba(0,0,0,.15);margin-right:8px"></span>{EVENT_LABELS[k]}</div>'
                     for k in EVENT_COLOURS)
    fmap.get_root().html.add_child(folium.Element(
        '<div style="position:absolute;bottom:28px;left:12px;z-index:9999;background:rgba(255,255,255,.96);'
        'border:1px solid #E2E8F0;border-radius:12px;padding:10px 14px;font:12px Inter,\'Segoe UI\',Arial,sans-serif;'
        'color:#172033;box-shadow:0 4px 14px rgba(16,24,40,.12)"><div style="font-weight:700;font-size:11px;'
        f'letter-spacing:.08em;text-transform:uppercase;color:#5A687D;margin-bottom:4px">News reports</div>{legend}'
        '<div style="margin:8px 0 2px 0;border-top:1px solid #E2E8F0;padding-top:7px;display:flex;align-items:center">'
        f'<span style="display:inline-block;width:11px;height:11px;background:{OFFICIAL_COLOUR};border:2px solid #fff;'
        'border-radius:3px;box-shadow:0 0 0 1px rgba(0,0,0,.15);margin-right:8px"></span>Official report (IDSP)</div>'
        '</div>'))
    Fullscreen(position="topright").add_to(fmap)
    folium.LayerControl(position="topright", collapsed=True).add_to(fmap)

    if zoom_choice in district_centres:
        lat, lon = district_centres[zoom_choice]
        fmap.fit_bounds([[lat - 0.35, lon - 0.35], [lat + 0.35, lon + 0.35]])
    elif markers and (states or chosen_diseases or sources):
        lats, lons = [m.latitude for m in markers], [m.longitude for m in markers]
        fmap.fit_bounds([[min(lats) - 0.5, min(lons) - 0.5], [max(lats) + 0.5, max(lons) + 0.5]], max_zoom=9)
    else:
        fmap.fit_bounds(INDIA_BOUNDS)

    st_folium(fmap, height=720, use_container_width=True, returned_objects=[], key="map")
    st.caption("Circles: news stories (one per story; the same report from several publishers is listed in one "
               "popup), at the most specific place the article supports. Green squares: official IDSP outbreak "
               "records, at the district centre.")

with side_col:
    reports = latest_reports(matching, limit=8)
    cards = []
    for r in reports:
        colour = EVENT_COLOURS[r["event_status"]]
        cards.append(
            f'<div class="report" style="border-left-color:{colour}">'
            f'<span class="chip" style="background:{colour}1A;color:{colour}">{EVENT_LABELS[r["event_status"]]}</span> '
            f'<span class="chip" style="background:#F1F4F9;color:#334155">{html.escape(r["diseases"] or "—")}</span>'
            f'<div class="title"><a href="{html.escape(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(r["headline"])}</a></div>'
            f'<div class="meta">📍 {html.escape(r["place"])}<br>📅 {fmt_ist(r["published"], "%d %b %Y")}'
            f' · {html.escape(r["source"])}</div></div>')
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
register_tab, official_tab, about_tab = st.tabs([f":material/table_rows: Article register ({len(matching)})",
                                                 f":material/verified: Official reports ({len(official_matching)})",
                                                 ":material/info: About the data"])

with register_tab:
    if matching.empty:
        st.info("No articles match the current filters.")
    else:
        table = matching.assign(
            published=[fmt_ist(to_ist(t.to_pydatetime()), "%d %b %Y %H:%M") if t == t else ""
                       for t in matching["published_at"]],
            diseases_text=matching["diseases"].map(", ".join),
            review_flag=matching["location_review"].map(lambda v: "⚠️" if v else ""),
            event_label=matching["event_status"].map(lambda s: EVENT_LABELS.get(s, "—")),
            location_level=matching["location_level"].map(lambda v: v if isinstance(v, str) and v != "country" else "—"),
            location_confidence=matching["location_confidence"].map(
                lambda v: v if isinstance(v, str) and v != "none" else "—"),
        )[["article_id", "published", "headline", "diseases_text", "place", "location_level",
           "location_confidence", "review_flag", "event_label", "content_type", "source_name", "article_url",
           "relevance", "relevance_status"]]
        edited = st.data_editor(
            table, hide_index=True, width="stretch", height=460, key="articles",
            disabled=[c for c in table.columns if c != "relevance_status"],
            column_config={
                "article_id": None,
                "published": "Published (IST)",
                "headline": st.column_config.TextColumn("Headline", width="large"),
                "diseases_text": "Disease",
                "place": "Reported location",
                "location_level": "Level",
                "location_confidence": "Location confidence",
                "review_flag": st.column_config.TextColumn("Check", help="Location flagged for review"),
                "event_label": "Event",
                "content_type": "Type",
                "source_name": "Source",
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
**How articles are collected.** Ten Indian news sites are read every 30 minutes through their RSS feeds,
news sitemaps and topic pages, respecting each site's robots.txt and limiting requests to one per second per site.
Only articles that mention a configured disease keyword are kept.

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

**Limits.** Classification and location are automatic and can be wrong; uncertain locations are flagged for review.
Coverage depends on what the configured outlets publish.

**Data and attribution.** Map tiles © OpenStreetMap contributors, © CARTO. India's boundary: official boundary of
India as per the Survey of India, compiled by the DataMeet India community (CC0). Place data © GeoNames (CC BY 4.0).
Geocoding © OpenStreetMap contributors (ODbL), via Nominatim. Articles © their publishers; follow the links to read
the originals.
""")

st.markdown('<div class="footer"><span>Infectious Disease Outbreak Monitor · prototype</span>'
            '<span>Not an official surveillance or early-warning system. Articles © their publishers.</span></div>',
            unsafe_allow_html=True)
