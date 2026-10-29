"""Outbreak news dashboard: interactive India map, filters, article register and review.

    streamlit run app.py
"""

import html
from datetime import timedelta

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
st.set_page_config(page_title="Outbreak News Monitor · India", page_icon="🗺️", layout="wide")
factory = get_session_factory()

# Static styling only (no data is interpolated into this block).
st.markdown("""
<style>
  .block-container { padding-top: 3.6rem; max-width: 1500px; }
  .masthead { border-bottom: 3px double #C9BFA8; padding-bottom: .9rem; margin-bottom: 1.1rem; }
  .masthead .eyebrow { font-size: .75rem; letter-spacing: .18em; text-transform: uppercase; color: #8C2F39; }
  .masthead h1 { font-family: Georgia, 'Times New Roman', serif; font-weight: 700; font-size: 2.35rem;
                 margin: .15rem 0 .1rem 0; padding: 0; color: #1F3A5F; }
  .masthead .sub { font-size: 1.02rem; color: #4A4A4A; }
  .note { background: #FFFDF7; border: 1px solid #E2D9C6; border-left: 4px solid #C9822B; border-radius: 6px;
          padding: .55rem .85rem; font-size: .88rem; color: #4A4A4A; margin-top: .7rem; }
  div[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E2D9C6; border-radius: 8px;
          padding: .75rem 1rem; box-shadow: 0 1px 2px rgba(60, 50, 30, .06); }
  div[data-testid="stMetricLabel"] p { font-size: .78rem; letter-spacing: .08em; text-transform: uppercase;
          color: #6B6B6B; }
  div[data-testid="stMetricValue"] { font-family: Georgia, 'Times New Roman', serif; color: #1F3A5F; }
  .panel-title { font-family: Georgia, 'Times New Roman', serif; font-size: 1.15rem; color: #1F3A5F;
          border-bottom: 1px solid #E2D9C6; padding-bottom: .3rem; margin: .1rem 0 .6rem 0; }
  .report { background: #FFFFFF; border: 1px solid #E2D9C6; border-radius: 8px; padding: .6rem .75rem;
          margin-bottom: .55rem; }
  .report .tag { display: inline-block; font-size: .68rem; letter-spacing: .08em; text-transform: uppercase;
          color: #FFFFFF; border-radius: 3px; padding: 1px 6px; }
  .report .title { font-family: Georgia, 'Times New Roman', serif; font-size: .98rem; line-height: 1.3;
          margin: .3rem 0 .25rem 0; }
  .report .title a { color: #222831; text-decoration: none; }
  .report .title a:hover { color: #1F3A5F; text-decoration: underline; }
  .report .meta { font-size: .8rem; color: #6B6B6B; }
  .state-row { display: flex; justify-content: space-between; font-size: .9rem; padding: .22rem 0;
          border-bottom: 1px dotted #E2D9C6; }
  .caveat { font-size: .75rem; color: #8A8A8A; margin-top: .4rem; }
  .legend-chip { display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; margin: 0 .3rem 0 .8rem;
          vertical-align: middle; border: 1px solid rgba(0,0,0,.15); }
  .footer { border-top: 1px solid #E2D9C6; margin-top: 2rem; padding-top: .7rem; font-size: .78rem; color: #8A8A8A; }
  section[data-testid="stSidebar"] h2 { font-family: Georgia, 'Times New Roman', serif; color: #1F3A5F; }
</style>
""", unsafe_allow_html=True)


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


def fmt_ist(dt, pattern="%d %b %Y, %H:%M IST"):
    return dt.strftime(pattern) if dt else "—"


# ---------------------------------------------------------------- masthead
st.markdown("""
<div class="masthead">
  <div class="eyebrow">Public news monitoring · India</div>
  <h1>Infectious Disease Outbreak Monitor</h1>
  <div class="sub">Outbreak reports from Indian news media, located and mapped by state, district and locality.</div>
  <div class="note"><b>Please note:</b> this is a news-based monitoring prototype, not an official surveillance or
  early-warning system. News coverage can be incomplete or delayed, and the number of articles is <b>not</b> a
  measure of disease incidence or outbreak severity.</div>
</div>
""", unsafe_allow_html=True)

articles = load()
official = load_official_reports()
opts = options(articles)
if not official.empty:  # official records can name diseases and states no article mentions yet
    opts["diseases"] = sorted(set(opts["diseases"]) | {d for ds in official["diseases"] for d in ds})
    opts["states"] = sorted(set(opts["states"]) | set(official["state"].dropna()))

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Explore")
    view = st.radio("Show", list(VIEWS), format_func=VIEWS.get, index=0)
    st.subheader("Filters")
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
    include_abroad = st.toggle("Include reports outside India (list only)", value=False)
    show_official = st.toggle("Show official IDSP reports", value=True,
                              help="Weekly outbreak reports of the Integrated Disease Surveillance Programme "
                                   "(NCDC). Published about two months late, so the date filter does not apply.")
    st.divider()
    st.subheader("Data")
    if st.button("🔄  Refresh now", width="stretch", help="Collect new articles and official reports now"):
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

# ---------------------------------------------------------------- key figures
k1, k2, k3, k4 = st.columns(4)
k1.metric("Matching articles", f"{totals['articles']:,}")
k2.metric("Stories on the map", f"{len(markers):,}")
k3.metric("Distinct locations", f"{totals['locations']:,}")
k4.metric("Last refresh", fmt_ist(totals["last_refresh"], "%d %b, %H:%M"))

# ---------------------------------------------------------------- map + side panel
map_col, side_col = st.columns([2.35, 1], gap="large")

with map_col:
    district_centres = district_zoom_options(matching)
    zoom_choice = st.selectbox("Zoom to district", ["All of India"] + list(district_centres),
                               label_visibility="collapsed")

    fmap = folium.Map(location=[22.5, 80.0], zoom_start=5, tiles=None, control_scale=True, min_zoom=4,
                      max_bounds=True, min_lat=PAN_LIMITS[0][0], max_lat=PAN_LIMITS[1][0],
                      min_lon=PAN_LIMITS[0][1], max_lon=PAN_LIMITS[1][1],
                      zoomSnap=0.25, zoomDelta=0.5)  # fractional zoom so India fills the frame
    # OpenStreetMap tiles (no API key); the default layer is toned down with CSS for a classic atlas look.
    folium.TileLayer("OpenStreetMap", name="Classic (muted)", className="classic-tiles").add_to(fmap)
    folium.TileLayer("OpenStreetMap", name="Street map (full colour)").add_to(fmap)
    fmap.get_root().header.add_child(folium.Element(
        "<style>.classic-tiles { filter: grayscale(60%) sepia(22%) brightness(104%) contrast(92%); }"
        ".leaflet-container { background: #FBF8F1; }</style>"))
    # Only India: everything outside the official boundary is covered in the page colour, and India is outlined.
    boundary, mask = india_shapes()
    folium.GeoJson(mask, name="Outside India", control=False, interactive=False,
                   style_function=lambda _: {"fillColor": "#FBF8F1", "fillOpacity": 1.0, "stroke": False}).add_to(fmap)
    folium.GeoJson(boundary, name="India boundary", control=False, interactive=False,
                   style_function=lambda _: {"fill": False, "color": "#1F3A5F", "weight": 1.4,
                                             "opacity": 0.9}).add_to(fmap)
    cluster = MarkerCluster(
        name="Outbreak reports",
        icon_create_function="""
          function(cluster) {
            var n = cluster.getChildCount();
            var size = n < 10 ? 30 : (n < 50 ? 38 : 46);
            return L.divIcon({
              html: '<div style="width:' + size + 'px;height:' + size + 'px;line-height:' + size + 'px;'
                  + 'border-radius:50%;background:rgba(31,58,95,.88);color:#fff;text-align:center;'
                  + 'font:600 13px Georgia,serif;border:2px solid #FBF8F1;box-shadow:0 1px 4px rgba(0,0,0,.3)">'
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
                     'border:2px solid #FFFFFF;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 1px 3px rgba(0,0,0,.35)"></div>',
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
                     'box-shadow:0 0 0 1px rgba(0,0,0,.3),0 1px 3px rgba(0,0,0,.35)"></div>',
                icon_size=(16, 16), icon_anchor=(8, 8)),
        ).add_to(official_layer)
    official_layer.add_to(fmap)
    legend = "".join(f'<div style="margin:2px 0"><span style="display:inline-block;width:11px;height:11px;'
                     f'border-radius:50%;background:{EVENT_COLOURS[k]};border:1px solid #fff;margin-right:6px;'
                     f'vertical-align:middle"></span>{EVENT_LABELS[k]}</div>' for k in EVENT_COLOURS)
    fmap.get_root().html.add_child(folium.Element(
        '<div style="position:absolute;bottom:28px;left:12px;z-index:9999;background:rgba(251,248,241,.94);'
        'border:1px solid #E2D9C6;border-radius:6px;padding:8px 12px;font:12px \'Segoe UI\',Arial,sans-serif;'
        'color:#222831;box-shadow:0 1px 4px rgba(0,0,0,.15)"><div style="font:600 12px Georgia,serif;'
        f'margin-bottom:4px;color:#1F3A5F">News reports</div>{legend}'
        '<div style="margin:6px 0 2px 0;border-top:1px solid #E2D9C6;padding-top:5px"><span style="display:'
        f'inline-block;width:10px;height:10px;background:{OFFICIAL_COLOUR};border:1px solid #fff;margin-right:6px;'
        'vertical-align:middle"></span>Official report (IDSP)</div></div>'))
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
    st.markdown('<div class="panel-title">Latest reports</div>', unsafe_allow_html=True)
    reports = latest_reports(matching, limit=4)
    if not reports:
        st.info("No reports match the current filters.")
    for r in reports:
        colour = EVENT_COLOURS[r["event_status"]]
        st.markdown(
            f'<div class="report"><span class="tag" style="background:{colour}">'
            f'{html.escape(r["diseases"] or "—")} · {EVENT_LABELS[r["event_status"]]}</span>'
            f'<div class="title"><a href="{html.escape(r["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{html.escape(r["headline"])}</a></div>'
            f'<div class="meta">📍 {html.escape(r["place"])}<br>🗓 {fmt_ist(r["published"], "%d %b %Y")}'
            f' · {html.escape(r["source"])}</div></div>', unsafe_allow_html=True)

    by_state = reports_by_state(matching)
    if by_state:
        st.markdown('<div class="panel-title" style="margin-top:1rem">Most reported states</div>',
                    unsafe_allow_html=True)
        st.markdown("".join(f'<div class="state-row"><span>{html.escape(s)}</span><b>{n}</b></div>'
                            for s, n in by_state)
                    + '<div class="caveat">Number of news stories, not cases. Coverage differs between regions '
                      'and outlets.</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- register + about
register_tab, official_tab, about_tab = st.tabs([f"📋  Article register ({len(matching)})",
                                                 f"🏛️  Official reports ({len(official_matching)})",
                                                 "ℹ️  About the data"])

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
        if st.button(f"💾  Save review changes ({len(changes)})", disabled=not changes, type="primary"):
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

**Data and attribution.** Map tiles © OpenStreetMap contributors. India's boundary: official boundary of India as
per the Survey of India, compiled by the DataMeet India community (CC0). Place data © GeoNames (CC BY 4.0).
Geocoding © OpenStreetMap contributors (ODbL), via Nominatim. Articles © their publishers; follow the links to read
the originals.
""")

st.markdown('<div class="footer">Infectious Disease Outbreak News Monitor — prototype. '
            'Not an official surveillance or early-warning system.</div>', unsafe_allow_html=True)
