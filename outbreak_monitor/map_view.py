"""The India map of the dashboard.

Districts are shaded by the number of reports (violet: news, green: official IDSP records); districts where news
reports are rising are outlined in red. Hovering a district shows its numbers and latest reports. Clicking it
opens it: the rest of India dims, the map zooms to it, and its reports appear as dots at their places (coloured by
event status; IDSP records as green squares), with the full list in a panel. Reports that name only a state are a
pill under the state's name. In "both" mode, districts with IDSP records are striped in green over the news shading.

Everything written on the map comes from our own data (LGD names in English). The optional backgrounds carry no
labels (Esri World Shaded Relief or World Imagery), so no place name in another script ever shows, and everything
outside India's official boundary is covered.
"""

import html
import json
import math
from datetime import date, timedelta
from functools import lru_cache

import folium
import pandas as pd
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template

from .boundaries import DISTRICTS_FILE, _in_polygon
from .config import load_disease_config
from .matcher import KeywordMatcher
from .dashboard_data import EVENT_COLOURS, EVENT_LABELS, OFFICIAL_COLOUR, _esc, href, published_ist

INDIA_BOUNDS = [[6.5, 68.0], [37.2, 97.5]]
PAN_LIMITS = [[3.0, 63.0], [40.0, 102.0]]
MAP_BACKGROUND = "#E3EAF1"   # outside India's boundary (and the sea)
LAND = "#F7F5F0"             # districts without reports, on the plain background
ALERT_COLOUR = "#D64545"
# District shading: reports in the district. One hue, light to dark (violet: news, green: official records); fixed
# classes, so a colour means the same number whatever the filters.
SHADE_BREAKS = [(1, 1), (2, 3), (4, 7), (8, 15), (16, None)]
SHADES = {"news": ["#B9A3EC", "#9A7BE0", "#7C57D1", "#5F37B8", "#43228A"],
          "official": ["#7FC2A3", "#55A783", "#348B67", "#1E6E4D", "#0F5038"]}
MODES = {"news": "News reports", "official": "Official IDSP records", "both": "Both"}
STATUS_ORDER = ["new", "suspected", "unclear", "historical"]
# Shorter forms for labels of long state / UT names
STATE_LABELS = {"Dadra and Nagar Haveli and Daman and Diu": "DNH & DD",
                "Andaman and Nicobar Islands": "Andaman & Nicobar Islands"}
TILES = {
    "Terrain": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}",
                "Relief: Esri", 13),
    "Satellite": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                  "Imagery: Esri, Maxar, Earthstar Geographics", 18),
}


# ---------------------------------------------------------------- boundaries

@lru_cache(maxsize=1)
def topology() -> dict:
    return json.loads(DISTRICTS_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _arc_points() -> list[list[tuple[float, float]]]:
    """Every arc of the topology as (longitude, latitude) points."""
    topo = topology()
    scale = topo["scale"]
    arcs = []
    for flat in topo["arcs"]:
        x, y, pts = flat[0], flat[1], [(flat[0], flat[1])]
        for i in range(2, len(flat), 2):
            x, y = x + flat[i], y + flat[i + 1]
            pts.append((x, y))
        arcs.append([(px / scale, py / scale) for px, py in pts])
    return arcs


def _ring(ring: list[int]) -> list[tuple[float, float]]:
    arcs, pts = _arc_points(), []
    for r in ring:
        arc = arcs[~r][::-1] if r < 0 else arcs[r]
        pts += arc if not pts else arc[1:]
    return pts


@lru_cache(maxsize=1)
def _decoded() -> dict[int, list]:
    """dc -> polygons as lists of [x, y] rings (for locating a point in a district)."""
    return {d["dc"]: [[_ring(ring) for ring in poly] for poly in d["polygons"]] for d in topology()["districts"]}


SMALL_ISLANDS_AREA = 0.02   # square degrees (~250 km2): a district of islands this small is hard to see or hover
ISLAND_HALO_DEG = 0.12      # the outline drawn around such a group of islands lies ~13 km off its islands


@lru_cache(maxsize=1)
def island_halos() -> dict[int, list[list[float]]]:
    """dc -> a rounded outline ([lat, lon] points) around a district made only of small islands (Lakshadweep), drawn
    dashed around the group, as on official maps, so the district can be seen, shaded and hovered at any zoom."""
    topo = topology()
    shared = {a for kind in ("district", "state") for a in topo["lines"][kind]}
    out = {}
    for d in topo["districts"]:
        arcs = {~r if r < 0 else r for poly in d["polygons"] for ring in poly for r in ring}
        if d["area"] >= SMALL_ISLANDS_AREA or arcs & shared:
            continue
        points = [(x + ISLAND_HALO_DEG * math.cos(t * math.pi / 8), y + ISLAND_HALO_DEG * math.sin(t * math.pi / 8))
                  for poly in _decoded()[d["dc"]] for x, y in poly[0] for t in range(16)]
        out[d["dc"]] = [[round(y, 3), round(x, 3)] for x, y in _convex_hull(points)]
    return out


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain."""
    pts = sorted(set(points))

    def half(seq):
        chain = []
        for p in seq:
            while len(chain) >= 2 and ((chain[-1][0] - chain[-2][0]) * (p[1] - chain[-2][1]) -
                                       (chain[-1][1] - chain[-2][1]) * (p[0] - chain[-2][0])) <= 0:
                chain.pop()
            chain.append(p)
        return chain[:-1]

    return half(pts) + half(reversed(pts))


def district_at(lon: float, lat: float) -> int | None:
    """The district (LGD code) whose boundary contains the point, or None."""
    for dc, polys in _decoded().items():
        for poly in polys:
            xs, ys = [p[0] for p in poly[0]], [p[1] for p in poly[0]]
            if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys) and _in_polygon(lon, lat, poly):
                return dc
    return None


@lru_cache(maxsize=1)
def state_bounds() -> dict[str, list[list[float]]]:
    """State / UT name -> [[south, west], [north, east]] of its districts."""
    out = {}
    names = {d["dc"]: d["state"] for d in topology()["districts"]}
    for dc, polys in _decoded().items():
        pts = [p for poly in polys for p in poly[0]]
        (s, w), (n, e) = out.get(names[dc], [[90, 180], [-90, -180]])
        out[names[dc]] = [[min(s, min(p[1] for p in pts)), min(w, min(p[0] for p in pts))],
                          [max(n, max(p[1] for p in pts)), max(e, max(p[0] for p in pts))]]
    return out


def boundary_of(code, lat, lon) -> int | None:
    """The boundary (LGD code) a located item belongs to: its own district or, for a district created after the
    boundary edition, the district its point lies in. None for items placed only at state level."""
    if code is None or pd.isna(code):
        return None
    dc = int(code)
    if dc not in _decoded() and lat is not None and not pd.isna(lat):
        dc = district_at(float(lon), float(lat)) or dc
    return dc


# ---------------------------------------------------------------- what the map shows

def shade(n: int, source: str = "news") -> str | None:
    for (low, high), colour in zip(SHADE_BREAKS, SHADES[source]):
        if n >= low and (high is None or n <= high):
            return colour
    return None


def _date(value) -> str:
    dt = published_ist(value)
    return dt.strftime("%d %b %Y") if dt else ""


NEWS_COLUMNS = ["story_id", "published_at", "published_date", "event_status", "diseases", "headline",
                "headline_original", "language_name", "article_url", "source_name", "place", "state", "district",
                "district_lgd_code", "latitude", "longitude"]
STATUS_RANK = ("new", "suspected", "unclear", "historical")   # an outbreak shows its most serious report
OTHER_REPORTS_SHOWN = 5                                        # reports listed on an outbreak's card besides the first


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)


@lru_cache(maxsize=1)
def _matcher() -> KeywordMatcher:
    return KeywordMatcher(load_disease_config().diseases)


def _lead_first(stories: list[dict], disease: str | None) -> list[dict]:
    """The reports of an outbreak with a report whose headline names its disease first: a report of two diseases
    ("dengue and malaria cases rise") belongs to both outbreaks, and should not head the card of the one its headline
    does not name."""
    lead = next((r for r in stories if disease in _matcher().match(r["headline"])), None) if disease else None
    return [lead] + [r for r in stories if r is not lead] if lead else stories


def _link(url, text: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f'<a{attr} href="{href(url)}" target="_blank" rel="noopener noreferrer">{_esc(text)}</a>'


def news_items(df: pd.DataFrame) -> list[dict]:
    """One item per outbreak in the news, newest first. An outbreak is the reports of one disease in one district (or
    state-wide, when no district is named) in one calendar week, from any source or language: the rule of the
    Outbreaks tab (dashboard_data.outbreak_events), so five articles about one outbreak count once. Each item has its
    position (its lead report's), district, most serious status and a card (HTML, every value escaped) listing its
    reports and sources. A report of two diseases belongs to two outbreaks. Works on plain records: much faster than
    pandas row access."""
    if df.empty:
        return []
    located = df[(df["country"] == "India") & df["latitude"].notna() & df["longitude"].notna()]
    if "published_date" not in located:   # the date in India (load_articles adds it)
        located = located.assign(published_date=[published_ist(v).date() if published_ist(v) else None
                                                 for v in located["published_at"]])
    if "district" not in located:
        located = located.assign(district=None)
    outbreaks: dict = {}
    for row in located.sort_values("published_at")[NEWS_COLUMNS].to_dict("records"):
        code = None if _missing(row["district_lgd_code"]) else row["district_lgd_code"]
        district = code if code is not None else (row["district"] if isinstance(row["district"], str) else None)
        day = row["published_date"]
        week = day - timedelta(days=day.weekday()) if isinstance(day, date) else None
        for disease in row["diseases"] or [None]:
            outbreaks.setdefault((disease, row["state"], district, week), []).append(row)
    items = []
    for (disease, *_), group in outbreaks.items():
        first, last = group[0], group[-1]
        status = next((s for s in STATUS_RANK if any(r["event_status"] == s for r in group)), "unclear")
        colour = EVENT_COLOURS[status]
        stories = _lead_first(list({r["story_id"]: r for r in group}.values()), disease)   # one per story
        lead = stories[0]                                                  # heads the card and places the dot
        code = None if _missing(lead["district_lgd_code"]) else lead["district_lgd_code"]
        sources = list(dict.fromkeys(r["source_name"] for r in group))
        dates = _date(first["published_at"]) if _date(first["published_at"]) == _date(last["published_at"]) \
            else f'{_date(first["published_at"])} – {_date(last["published_at"])}'
        original = lead["headline_original"] if isinstance(lead["headline_original"], str) and \
            lead["headline_original"] else None
        count = (f'<div class="mc-n">{len(stories)} reports from {len(sources)} '
                 f'source{"" if len(sources) == 1 else "s"}</div>' if len(stories) > 1 else "")
        others = "".join(f'<div class="mc-r">{_link(r["article_url"], r["headline"])} '
                         f'<span>· {_esc(r["source_name"])}</span></div>' for r in stories[1:1 + OTHER_REPORTS_SHOWN])
        more = len(stories) - 1 - OTHER_REPORTS_SHOWN
        others += f'<div class="mc-r"><span>+ {more} more</span></div>' if more > 0 else ""
        card = (f'<div class="mc" style="border-left-color:{colour}">'
                f'<div class="mc-top"><span class="mc-chip" style="color:{colour};background:{colour}1A">'
                f'{EVENT_LABELS[status]}</span><span class="mc-dis">{_esc(disease or "—")}</span>'
                f'<span class="mc-date">{dates}</span></div>'
                + _link(lead["article_url"], lead["headline"], "mc-h")
                + (f'<div class="mc-o">{_esc(original)} · {_esc(lead["language_name"])}</div>' if original else "")
                + f'<div class="mc-m">{_esc(lead["place"])}</div>{count}{others}'
                + f'<div class="mc-s">{" · ".join(_link(r["article_url"], r["source_name"]) for r in group)}</div>'
                + '</div>')
        items.append({"la": round(float(lead["latitude"]), 4), "lo": round(float(lead["longitude"]), 4),
                      "dc": boundary_of(code, lead["latitude"], lead["longitude"]),
                      "st": _esc(lead["state"]) if isinstance(lead["state"], str) else "",
                      "s": status, "p": _esc(lead["place"]), "h": _esc(lead["headline"]), "c": card,
                      "t": str(last["published_at"]), "ds": [disease] if disease else []})
    items.sort(key=lambda i: i["t"], reverse=True)
    return items


def official_items(df: pd.DataFrame) -> list[dict]:
    """One item per located official IDSP record (newest reporting week first), with a card for the panel."""
    if df.empty:
        return []
    items = []
    for r in df[df["latitude"].notna()].itertuples():
        cases = "" if r.cases is None or pd.isna(r.cases) else f"{int(r.cases)} case(s)"
        deaths = "" if r.deaths is None or pd.isna(r.deaths) else f"{int(r.deaths)} death(s)"
        place = ", ".join(p for p in (r.district, r.state) if isinstance(p, str)) or "—"
        onset = r.onset_date.strftime("%d %b %Y") if isinstance(r.onset_date, date) else "—"
        title = ": ".join(x for x in (str(r.disease_raw or "—"), ", ".join(x for x in (cases, deaths) if x)) if x)
        card = (f'<div class="mc" style="border-left-color:{OFFICIAL_COLOUR}">'
                f'<div class="mc-top"><span class="mc-chip" style="color:{OFFICIAL_COLOUR};'
                f'background:{OFFICIAL_COLOUR}1A">IDSP</span><span class="mc-dis">{_esc(r.week_label)}</span></div>'
                f'<div class="mc-h">{_esc(title)}</div>'
                f'<div class="mc-m">{_esc(place)} · started {onset} · {_esc(r.status)}</div>'
                f'<div class="mc-s">ID {_esc(r.report_id)} · <a href="{href(r.report_url)}" target="_blank" '
                'rel="noopener noreferrer">weekly report (PDF)</a></div></div>')
        code = getattr(r, "district_lgd_code", None)
        dc = (boundary_of(code, r.latitude, r.longitude) if code is not None and not pd.isna(code)
              else district_at(float(r.longitude), float(r.latitude)) if r.location_level == "district" else None)
        items.append({"la": round(float(r.latitude), 4), "lo": round(float(r.longitude), 4), "dc": dc,
                      "st": _esc(r.state) if isinstance(r.state, str) else "",
                      "p": _esc(place), "h": _esc(title), "c": card})
    return items


def district_counts(items: list[dict]) -> dict[int, dict]:
    """dc -> {n, top}: the number of items in each district and, for news, its most reported diseases."""
    out, diseases = {}, {}
    for item in items:
        if item["dc"] is None:
            continue
        out.setdefault(item["dc"], {"n": 0, "top": ""})["n"] += 1
        for d in item.get("ds", []):
            diseases.setdefault(item["dc"], {}).setdefault(d, 0)
            diseases[item["dc"]][d] += 1
    for dc, counts in diseases.items():
        top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:4]
        out[dc]["top"] = html.escape(", ".join(f"{d} {n}" for d, n in top))
    return out


def alert_districts(alerts: pd.DataFrame | None) -> dict[int, dict]:
    """dc -> {"rise": bool, "list": ['Rise: Dengue (5 reports in 7 days)', ...]} for district-level alerts. Only
    districts with a rise are outlined on the map; every alert is listed in the tooltip and the panel."""
    out = {}
    if alerts is None or alerts.empty:
        return out
    for a in alerts[alerts["district_lgd_code"].notna()].itertuples():
        dc = boundary_of(a.district_lgd_code, getattr(a, "latitude", None), getattr(a, "longitude", None))
        if dc not in _decoded():
            continue
        entry = out.setdefault(dc, {"rise": False, "list": []})
        entry["rise"] |= a.alert == "Rise"
        entry["list"].append(html.escape(
            f"{a.alert}: {a.disease} ({a.reports} report{'' if a.reports == 1 else 's'} in 7 days)"))
    return out


# ---------------------------------------------------------------- the map element (drawn in the browser)

class IndiaMap(MacroElement):
    _template = Template("""
{% macro header(this, kwargs) %}
<style>
  .leaflet-container { background: {{ this.background }}; font-family: Inter, 'Segoe UI', Arial, sans-serif; }
  .lbl { pointer-events: none; text-align: center; color: #243247; line-height: 1.15; white-space: normal;
         text-shadow: 0 0 2px #fff, 0 0 2px #fff, 0 0 3px #fff, 0 0 4px #fff; }
  .lbl-s { font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: #3A4A63; }
  .lbl-d { font-size: 10.5px; font-weight: 500; color: #5A687D; }
  .lbl-d.sel { font-size: 12.5px; font-weight: 700; color: #0B2545; }
  .imagery .lbl { color: #fff; text-shadow: 0 0 2px #000, 0 0 3px #000, 0 0 4px #000; }
  /* a selected district pops out: the rest dims, the district casts a shadow */
  .leaflet-fill-pane, .leaflet-hatch-pane, .leaflet-halo-pane, .leaflet-alerts-pane { transition: opacity .25s ease; }
  .has-sel .leaflet-fill-pane, .has-sel .leaflet-hatch-pane, .has-sel .leaflet-halo-pane { opacity: .4; }
  .has-sel .leaflet-alerts-pane { opacity: .35; }
  .has-sel .leaflet-pills-pane { display: none; }
  .leaflet-sel-pane { filter: drop-shadow(0 10px 14px rgba(11,37,69,.45)); }
  .spill { display: inline-block; margin: 0 auto; padding: 2px 9px; border-radius: 999px; background: #fff;
           border: 1px solid #1F3A5F; color: #1F3A5F; font: 600 10.5px Inter, 'Segoe UI', Arial, sans-serif;
           cursor: pointer; box-shadow: 0 1px 4px rgba(16,24,40,.15); white-space: nowrap; }
  .spill:hover { background: #1F3A5F; color: #fff; }
  /* dots in a selected district */
  .pt { position: relative; width: 14px; height: 14px; border-radius: 50%; border: 2px solid #fff; cursor: pointer;
        box-shadow: 0 0 0 1px rgba(0,0,0,.22), 0 1px 4px rgba(0,0,0,.3); }
  .pt.sq { border-radius: 3px; }
  /* panel */
  .mp { position: absolute; top: 10px; right: 56px; bottom: 10px; width: 340px; z-index: 1000; display: none;
        background: #fff; border: 1px solid #E2E8F0; border-radius: 14px; box-shadow: 0 12px 32px rgba(16,24,40,.2);
        overflow: hidden; font: 12.5px Inter, 'Segoe UI', Arial, sans-serif; color: #172033; flex-direction: column; }
  .mp.open { display: flex; }
  .mp-head { display: flex; justify-content: space-between; gap: 8px; padding: 12px 14px 10px 14px;
             border-bottom: 1px solid #EEF1F5; }
  .mp-title { font-size: 15px; font-weight: 700; line-height: 1.25; }
  .mp-sub { color: #5A687D; font-size: 11.5px; margin-top: 2px; }
  .mp-x { border: 0; background: #F1F4F9; border-radius: 8px; width: 28px; height: 28px; font-size: 16px;
          cursor: pointer; color: #5A687D; flex: 0 0 auto; }
  .mp-body { overflow-y: auto; padding: 10px 12px 12px 12px; }
  .mp-sec { font-size: 10.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: #5A687D;
            margin: 6px 0 6px 2px; }
  .mp-alert { background: #FDECEC; color: #8A1F1F; border-radius: 8px; padding: 6px 9px; margin-bottom: 4px;
              font-weight: 600; font-size: 12px; }
  .mp-more { color: #5A687D; font-size: 11.5px; margin: 4px 2px; }
  .mc { border: 1px solid #E2E8F0; border-left: 4px solid; border-radius: 9px; padding: 7px 9px; margin-bottom: 7px; }
  .mc-top { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #5A687D; }
  .mc-chip { font-weight: 700; border-radius: 999px; padding: 1px 7px; font-size: 10px; white-space: nowrap; }
  .mc-dis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mc-date { margin-left: auto; white-space: nowrap; }
  .mc-h { display: block; font-weight: 600; font-size: 13px; line-height: 1.35; color: #172033; margin: 4px 0 2px 0;
          text-decoration: none; }
  a.mc-h:hover { color: #1554A6; }
  .mc-o { color: #5A687D; font-size: 11.5px; }
  .mc-m, .mc-s { color: #5A687D; font-size: 11.5px; margin-top: 2px; }
  .mc-s a { color: #1554A6; text-decoration: none; font-weight: 500; }
  .mc-n { font-size: 11px; font-weight: 700; color: #3A4A63; margin: 5px 0 2px 0; }
  .mc-r { font-size: 11.5px; line-height: 1.35; margin: 2px 0 0 8px; color: #5A687D; }
  .mc-r a { color: #243247; text-decoration: none; }
  .mc-r a:hover { color: #1554A6; }
  /* legend */
  .mlg { background: rgba(255,255,255,.97); border: 1px solid #E2E8F0; border-radius: 12px; padding: 10px 12px;
         width: 214px; font: 12px Inter, 'Segoe UI', Arial, sans-serif; color: #172033;
         box-shadow: 0 4px 14px rgba(16,24,40,.12); }
  .mlg-h { font-weight: 700; font-size: 10.5px; letter-spacing: .08em; text-transform: uppercase; color: #5A687D;
           margin: 8px 0 4px 0; }
  .mlg-h:first-child { margin-top: 0; }
  .mlg-r { display: flex; align-items: center; gap: 8px; margin: 3px 0; }
  .mlg-hint { margin-top: 8px; padding-top: 7px; border-top: 1px solid #EEF1F5; color: #5A687D; font-size: 11px; }
  .leaflet-tooltip.dtip { border-radius: 8px; border: 1px solid #E2E8F0; box-shadow: 0 6px 18px rgba(16,24,40,.16);
        padding: 8px 10px; font: 12px Inter, 'Segoe UI', Arial, sans-serif; color: #172033; }
  .leaflet-bar a, .leaflet-control-layers { border-radius: 8px !important; }
  .leaflet-control-layers-expanded { font: 12px Inter, 'Segoe UI', Arial, sans-serif; padding: 8px 12px; }
  .leaflet-control-attribution { font-size: 10px; background: rgba(255,255,255,.85) !important; }
</style>
{% endmacro %}
{% macro script(this, kwargs) %}
(function () {
  var map = {{ this._parent.get_name() }};
  var D = {{ this.data }};
  var topo = D.topo, mode = D.mode, el = map.getContainer();
  var selected = null;   // the open district (LGD code), if any
  var primary = mode === 'official' ? D.official : D.news;           // shading and dots
  var secondary = mode === 'both' ? D.official : null;              // green squares beside them
  var shades = D.shades[mode === 'official' ? 'official' : 'news'], breaks = D.breaks;
  [['fill', 350], ['hatch', 352], ['mask', 390], ['halo', 392], ['borders', 395], ['alerts', 396], ['sel', 398],
   ['labels', 420], ['pills', 640]].forEach(function (p) {
    map.createPane(p[0]).style.zIndex = p[1];
    if (p[0] !== 'fill' && p[0] !== 'pills') map.getPane(p[0]).style.pointerEvents = 'none';
  });
  // The island outline is drawn above the mask (it lies at sea); a canvas covers the whole map, so this one takes no
  // mouse events, and an invisible copy among the district fills answers hover and click instead
  var haloCanvas = L.canvas({pane: 'halo', padding: 0.3});
  var fillCanvas = L.canvas({pane: 'fill', padding: 0.3}), lineCanvas = L.canvas({pane: 'borders', padding: 0.3});
  var alertCanvas = L.canvas({pane: 'alerts', padding: 0.3});

  function shadeOf(n) {
    for (var i = 0; i < breaks.length; i++) {
      if (n >= breaks[i][0] && (breaks[i][1] === null || n <= breaks[i][1])) return shades[i];
    }
    return null;
  }
  function plural(n, word) {   // 'outbreak in the news' -> 'outbreaks in the news'
    return n + ' ' + (n === 1 ? word : word.replace(/(\\w)( in the news)?$/, '$1s$2'));
  }
  var unit = mode === 'official' ? 'IDSP record' : 'outbreak in the news';

  // Base: plain (default), terrain or satellite; none of them carries any label
  var bases = {}, base = 'Plain';
  D.tiles.forEach(function (t) {
    bases[t[0]] = t[1] ? L.tileLayer(t[1], {attribution: t[2], maxNativeZoom: t[3], maxZoom: 18}) : L.layerGroup();
  });
  bases.Plain.addTo(map);

  // Arcs (shared borders, delta-encoded in 1/scale degree) to [lat, lng] lists
  var arcs = topo.arcs.map(function (a) {
    var x = a[0], y = a[1], pts = [[y / topo.scale, x / topo.scale]];
    for (var i = 2; i < a.length; i += 2) { x += a[i]; y += a[i + 1]; pts.push([y / topo.scale, x / topo.scale]); }
    return pts;
  });
  function ring(ids) {
    var out = [];
    ids.forEach(function (r) {
      var a = r < 0 ? arcs[~r].slice().reverse() : arcs[r];
      out = out.length ? out.concat(a.slice(1)) : a.slice();
    });
    return out;
  }

  // Items by district and by place
  function byDistrict(items) {
    var out = {};
    (items || []).forEach(function (it) { if (it.dc !== null) (out[it.dc] = out[it.dc] || []).push(it); });
    return out;
  }
  var primaryBy = byDistrict(primary.items), secondaryBy = secondary ? byDistrict(secondary.items) : {};
  var districts = {};
  topo.districts.forEach(function (d) { districts[d.dc] = d; });

  // Panel with the reports of a district or a place
  var panel = L.DomUtil.create('div', 'mp', el);
  L.DomEvent.disableClickPropagation(panel);
  L.DomEvent.disableScrollPropagation(panel);
  function cards(items, limit) {
    var html = items.slice(0, limit).map(function (it) { return it.c; }).join('');
    if (items.length > limit) html += '<div class="mp-more">and ' + (items.length - limit) +
                                      ' more: see the tables below the map</div>';
    return html;
  }
  function section(title, items) {
    return items && items.length ? '<div class="mp-sec">' + title + ' (' + items.length + ')</div>' + cards(items, 40) : '';
  }
  function openPanel(title, sub, body) {
    panel.innerHTML = '<div class="mp-head"><div><div class="mp-title">' + title + '</div><div class="mp-sub">' +
                      sub + '</div></div><button class="mp-x" title="Close">&times;</button></div>' +
                      '<div class="mp-body">' + body + '</div>';
    panel.classList.add('open');
  }
  function openDistrict(dc) {
    var d = districts[dc], alerts = (mode !== 'official' && D.alerts[dc] && D.alerts[dc].list) || [];
    var body = alerts.map(function (a) { return '<div class="mp-alert">' + a + '</div>'; }).join('');
    body += section(mode === 'official' ? 'Official IDSP records' : 'Outbreaks in the news', primaryBy[dc]);
    if (secondary) body += section('Official IDSP records', secondaryBy[dc]);
    if (!body) body = '<div class="mp-more">Nothing for the current filters.</div>';
    openPanel(d.name, d.state + (d.dc > 0 ? ' · LGD ' + d.dc : ''), body);
  }

  // What hovering a district shows: the count, diseases, alerts and the latest reports
  function districtTip(d) {
    var c = primary.counts[d.dc], sec = secondary && D.official.counts[d.dc];
    var alerts = (mode !== 'official' && D.alerts[d.dc] && D.alerts[d.dc].list) || [];
    var latest = (primaryBy[d.dc] || []).slice(0, 3).map(function (it) {
      return '<div style="margin-top:3px;max-width:300px;white-space:normal;line-height:1.3">· ' + it.h + '</div>';
    }).join('');
    return '<div style="font-weight:700;font-size:13px">' + d.name + '</div>' +
      '<div style="color:#5A687D;margin-bottom:4px">' + d.state + (d.dc > 0 ? ' · LGD ' + d.dc : '') + '</div>' +
      (c ? '<b>' + plural(c.n, unit) + '</b>' + (c.top ? '<div style="color:#5A687D;margin-top:2px">' + c.top +
           '</div>' : '') : '<span style="color:#5A687D">No ' + unit + 's for the current filters</span>') +
      (sec ? '<div style="margin-top:3px;color:' + D.officialColour + ';font-weight:600">' +
             plural(sec.n, 'IDSP record') + '</div>' : '') +
      alerts.map(function (a) { return '<div style="margin-top:4px;color:' + D.alertColour + ';font-weight:600">' +
                                       a + '</div>'; }).join('') +
      (latest ? '<div style="margin-top:6px;color:#5A687D;font-size:10.5px;font-weight:700;letter-spacing:.06em;' +
                'text-transform:uppercase">Latest</div>' + latest : '') +
      (c || sec ? '<div style="color:#8592A6;margin-top:5px;font-size:11px">Click to see them on the map</div>' : '');
  }

  // District shading
  var noDataStyle = {Plain: {fillColor: D.land, fillOpacity: 1}, Terrain: {fillColor: '#FFFFFF', fillOpacity: 0.12},
                     Satellite: {fillColor: '#FFFFFF', fillOpacity: 0}};
  var shapes = [];
  var fills = L.layerGroup().addTo(map);
  // Areas SOI marks disputed between two states: land of India in no district
  (topo.disputed || []).forEach(function (poly) {
    var land = L.polygon([poly.map(ring)], L.extend({renderer: fillCanvas, stroke: false, fill: true,
                                                    interactive: false}, noDataStyle.Plain));
    land._has = false;
    fills.addLayer(land);
    shapes.push(land);
  });
  function rings(d) { return d.polygons.map(function (p) { return p.map(ring); }); }
  topo.districts.forEach(function (d) {
    var c = primary.counts[d.dc], sec = secondary && D.official.counts[d.dc];
    var halo = D.halos[d.dc];
    if (halo) {   // a group of very small islands (Lakshadweep): a dashed outline around it, shaded like the district
      fills.addLayer(L.polygon(halo, {renderer: haloCanvas, interactive: false, color: '#5A687D', weight: 1.2,
                                      dashArray: '5 4', fill: true, fillOpacity: c || sec ? 0.35 : 0,
                                      fillColor: c ? shadeOf(c.n) : (sec ? D.officialColour : D.land)}));
      halo = L.polygon(halo, {renderer: fillCanvas, stroke: false, fill: true, fillOpacity: 0});
      halo.bindTooltip(districtTip(d), {sticky: true, className: 'dtip', direction: 'top', offset: [0, -8], opacity: 1});
      halo.on('tooltipopen', function () { if (selected === d.dc) this.closeTooltip(); });
      halo.on('click', function (e) {
        L.DomEvent.stopPropagation(e);
        if (selected === d.dc) return;
        if (c || sec) select(d.dc); else clearSelection();
      });
      fills.addLayer(halo);
    }
    var shape = L.polygon(rings(d), {
      renderer: fillCanvas, stroke: false, fill: true, smoothFactor: 1.2,
      fillColor: c ? shadeOf(c.n) : D.land, fillOpacity: c ? 0.85 : 1
    });
    shape._has = !!c;
    shape.bindTooltip(districtTip(d), {sticky: true, className: 'dtip', direction: 'top', offset: [0, -8], opacity: 1});
    shape.on('tooltipopen', function () { if (selected === d.dc) this.closeTooltip(); });
    shape.on('click', function (e) {
      L.DomEvent.stopPropagation(e);
      if (selected === d.dc) return;   // a click inside the open district (between its dots) keeps it open
      if (c || sec) select(d.dc); else clearSelection();
    });
    fills.addLayer(shape);
    shapes.push(shape);
  });

  // Both: districts that also have official IDSP records are hatched in green over the news shading
  var hatching = L.layerGroup(), stripes = null;
  if (secondary) {
    var tile = document.createElement('canvas');
    tile.width = tile.height = 8;
    var g = tile.getContext('2d');
    g.strokeStyle = D.officialColour;
    g.lineWidth = 1.7;
    g.lineCap = 'square';
    g.beginPath();
    g.moveTo(0, 8); g.lineTo(8, 0);     // one diagonal per tile, plus the corners of its neighbours
    g.moveTo(-1, 1); g.lineTo(1, -1);
    g.moveTo(7, 9); g.lineTo(9, 7);
    g.stroke();
    stripes = document.createElement('canvas').getContext('2d').createPattern(tile, 'repeat');
    var hatchCanvas = L.canvas({pane: 'hatch', padding: 0.3});
    topo.districts.forEach(function (d) {
      if (!D.official.counts[d.dc]) return;
      hatching.addLayer(L.polygon(rings(d), {renderer: hatchCanvas, stroke: false, fill: true, fillColor: stripes,
                                             fillOpacity: 0.85, interactive: false, smoothFactor: 1.2}));
    });
    hatching.addTo(map);
  }

  // Everything outside India is covered: a polygon over the world with every ring of every district and disputed area
  // cut out of it. It is filled even-odd, so a district inside another's hole is cut out again; India is exactly the
  // land of the districts, and the district colours meet India's border line exactly
  var world = [[-85, -180], [-85, 180], [85, 180], [85, -180]], cuts = [world];
  topo.districts.forEach(function (d) { rings(d).forEach(function (p) { cuts.push.apply(cuts, p); }); });
  (topo.disputed || []).forEach(function (p) { cuts.push.apply(cuts, p.map(ring)); });
  L.polygon(cuts, {pane: 'mask', interactive: false, fillColor: D.background, fillOpacity: 1, stroke: false,
                   fillRule: 'evenodd', smoothFactor: 1.2}).addTo(map);   // smoothed as the district fills are

  // Borders: districts, states, India; districts where reports are rising outlined in red
  var lines = {};
  [['district', '#A3ACBA', 0.5], ['state', '#5B6B84', 1.3], ['india', '#33435C', 1.6]].forEach(function (s) {
    lines[s[0]] = L.polyline(topo.lines[s[0]].map(function (i) { return arcs[i]; }), {
      renderer: lineCanvas, color: s[1], weight: s[2], opacity: 0.95, interactive: false, smoothFactor: 1.2,
      lineJoin: 'round'
    }).addTo(map);
  });
  var alertLines = L.layerGroup().addTo(map);
  if (mode !== 'official') {
    Object.keys(D.alerts).forEach(function (dc) {
      var d = districts[dc];
      if (!d || !D.alerts[dc].rise) return;
      alertLines.addLayer(L.polygon(rings(d), {
        renderer: alertCanvas, fill: false, color: D.alertColour, weight: 2, opacity: 1, interactive: false}));
    });
  }

  // Labels, in English, from our own data
  function label(text, lat, lng, cls) {
    return L.marker([lat, lng], {pane: 'labels', interactive: false, keyboard: false,
      icon: L.divIcon({className: '', iconSize: [150, 30], iconAnchor: [75, 15],
                       html: '<div class="lbl ' + cls + '">' + text + '</div>'})});
  }
  // State and district names are placed together, most important first (the selected district, then states, then
  // larger states and districts first); a name that would overlap one already placed is left out, so names never
  // print over names. A name left out appears when the map is zoomed in further.
  var names = L.layerGroup().addTo(map);
  var byArea = topo.districts.slice().sort(function (a, b) { return b.area - a.area; });
  var statesByArea = topo.states.slice().sort(function (a, b) { return b.area - a.area; });
  function labelBox(text, lat, lng, charWidth, lineHeight) {
    var width = text.length * charWidth, lines = Math.max(1, Math.ceil(width / 146));
    var w = Math.min(width, 146), h = lines * lineHeight, p = map.latLngToContainerPoint([lat, lng]);
    return [p.x - w / 2 - 2, p.y - h / 2, p.x + w / 2 + 2, p.y + h / 2];
  }
  function refreshDistrictNames() {
    names.clearLayers();
    var z = map.getZoom(), view = map.getBounds().pad(0.15), placed = [];
    function place(text, lat, lng, cls, charWidth, lineHeight) {
      if (!view.contains([lat, lng])) return;
      var b = labelBox(text, lat, lng, charWidth, lineHeight);
      for (var i = 0; i < placed.length; i++) {
        var o = placed[i];
        if (b[0] < o[2] && o[0] < b[2] && b[1] < o[3] && o[1] < b[3]) return;
      }
      placed.push(b);
      names.addLayer(label(text, lat, lng, cls));
    }
    if (selected !== null) {
      var s = districts[selected];
      place(s.name, s.ly, s.lx, 'lbl-d sel', 7.2, 15);
    }
    if (z < 9) statesByArea.forEach(function (s) {
      if (s.area >= 1.2 || z >= 6) place(s.label, s.ly, s.lx, 'lbl-s', 8.4, 12);
    });
    if (z >= 7) byArea.forEach(function (d) {
      if (d.dc === selected || (z < 8 && d.area <= 0.35)) return;
      if (d.name.toLowerCase() === d.state.toLowerCase()) return;   // the state's own name already shows it (Lakshadweep, Chandigarh)
      place(d.name, d.ly, d.lx, 'lbl-d', 6, 12.5);
    });
  }

  // Reports that name only a state: a pill under the state's name (from state zoom), not a dot at its centre
  var statePills = L.layerGroup(), stateLabelAt = {};
  topo.states.forEach(function (s) { stateLabelAt[s.name] = s; });
  var byState = {};
  (primary.items || []).forEach(function (it) {
    if (it.dc === null && stateLabelAt[it.st]) (byState[it.st] = byState[it.st] || []).push(it);
  });
  Object.keys(byState).forEach(function (st) {
    var s = stateLabelAt[st], g = byState[st];
    var text = plural(g.length, 'state-wide ' + (mode === 'official' ? 'record' : 'outbreak'));
    statePills.addLayer(L.marker([s.ly, s.lx], {pane: 'pills', icon: L.divIcon({className: '', iconSize: [170, 22],
        iconAnchor: [85, -9], html: '<div style="text-align:center"><span class="spill">' + text + '</span></div>'})})
      .bindTooltip('Reports that name ' + st + ' but no district. Click for the list.', {direction: 'bottom'})
      .on('click', function (e) {
        L.DomEvent.stopPropagation(e);
        clearSelection();
        openPanel(st, text + ': they name the state but no district', cards(g, 60));
      }));
  });

  // A selected district: it pops out (the rest of India dims), the map zooms to it, and its reports appear as
  // dots at their places (green squares: IDSP records). Clicking outside it, or closing the panel, goes back.
  var selLayer = L.layerGroup().addTo(map), dots = L.layerGroup().addTo(map);
  var selRenderer = L.canvas({pane: 'sel', padding: 0.5});   // canvas: the IDSP stripe pattern draws on it
  var rank = {};   // most serious status first
  D.statusOrder.forEach(function (s, i) { rank[s] = i; });
  function addDots(items, official, district) {
    var groups = {};
    (items || []).forEach(function (it) {
      (groups[it.la + ',' + it.lo] = groups[it.la + ',' + it.lo] || []).push(it);
    });
    Object.keys(groups).forEach(function (k) {
      var g = groups[k], first = g[0], colour = D.officialColour;
      if (!official) {
        g.sort(function (a, b) { return rank[a.s] - rank[b.s]; });
        colour = D.statusColours[g[0].s];   // the most serious status at that place
        g.sort(function (a, b) { return a.t < b.t ? 1 : -1; });
      }
      var px = g.length > 1 ? 18 : 14;   // several reports at one place: a larger dot
      var ax = official && secondary ? -5 : px / 2 + 2;   // IDSP squares sit beside a news dot at the same place
      var what = plural(g.length, official ? 'IDSP record' : 'outbreak in the news');
      dots.addLayer(L.marker([first.la, first.lo], {riseOnHover: true, icon: L.divIcon({className: 'pt-in',
          iconSize: [px + 4, px + 4], iconAnchor: [ax, px / 2 + 2],
          html: '<div class="pt' + (official ? ' sq' : '') + '" style="width:' + px + 'px;height:' + px +
                'px;background:' + colour + '"></div>'})})
        .bindTooltip('<div style="font-weight:700">' + first.p + '</div><b>' + what + '</b>' +
                     g.slice(0, 3).map(function (it) {
                       return '<div style="margin-top:3px;max-width:300px;white-space:normal;line-height:1.3">· ' +
                              it.h + '</div>'; }).join('') +
                     (g.length > 3 ? '<div style="color:#8592A6;margin-top:3px">and ' + (g.length - 3) +
                                     ' more</div>' : '') +
                     '<div style="color:#8592A6;margin-top:5px;font-size:11px">Click to read them</div>',
                     {className: 'dtip', direction: 'top', offset: [0, -px / 2], opacity: 1})
        .on('click', function (e) {
          L.DomEvent.stopPropagation(e);
          openPanel(first.p, what + ' at this place · <a href="#" class="mp-back">all of ' + district.name + '</a>',
                    cards(g, 60));
        }));
    });
  }
  function select(dc, instant) {
    clearSelection(true);
    selected = dc;
    var d = districts[dc], c = primary.counts[dc];
    var fill = c ? {fillColor: shadeOf(c.n), fillOpacity: 1} : noDataStyle[base];
    var shape = L.polygon(rings(d), L.extend({renderer: selRenderer, interactive: false, color: '#0B2545',
                                              weight: 2.5}, fill));
    selLayer.addLayer(shape);
    // what the district showed before it was opened stays on it: IDSP stripes and a red "rising" outline
    if (secondary && D.official.counts[dc] && stripes) {
      selLayer.addLayer(L.polygon(rings(d), {renderer: selRenderer, interactive: false, stroke: false,
                                             fillColor: stripes, fillOpacity: 0.85}));
    }
    if (mode !== 'official' && D.alerts[dc] && D.alerts[dc].rise) {
      selLayer.addLayer(L.polygon(rings(d), {renderer: selRenderer, interactive: false, fill: false,
                                             color: D.alertColour, weight: 2.5}));
    }
    el.classList.add('has-sel');
    addDots(primaryBy[dc], mode === 'official', d);
    if (secondary) addDots(secondaryBy[dc], true, d);
    openDistrict(dc);
    refreshDistrictNames();
    var fit = {paddingTopLeft: [40, 40], paddingBottomRight: [400, 40], maxZoom: 10};
    if (instant) map.fitBounds(shape.getBounds(), L.extend({animate: false}, fit));
    else map.flyToBounds(shape.getBounds(), L.extend(fit, {duration: 0.6}));
    updateLegend();
  }
  function clearSelection(keepPanel) {
    var had = selected !== null;
    selected = null;
    selLayer.clearLayers();
    dots.clearLayers();
    el.classList.remove('has-sel');
    if (!keepPanel) panel.classList.remove('open');
    if (had && !keepPanel) { updateLegend(); refreshDistrictNames(); }
  }
  map.on('click', function () { clearSelection(); });
  panel.addEventListener('click', function (e) {
    if (e.target.classList.contains('mp-x')) clearSelection();
    if (e.target.classList.contains('mp-back') && selected !== null) { e.preventDefault(); openDistrict(selected); }
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') clearSelection(); });

  // Legend
  var legend = L.control({position: 'bottomleft'});
  legend.onAdd = function () { this._div = L.DomUtil.create('div', 'mlg'); return this._div; };
  legend.addTo(map);
  function updateLegend() {
    var ramp = shades.map(function (c, i) {
      var b = breaks[i], t = b[1] === null ? b[0] + '+' : (b[0] === b[1] ? b[0] : b[0] + '–' + b[1]);
      return '<div style="flex:1;text-align:center"><div style="height:10px;background:' + c +
             (i === 0 ? ';border-radius:3px 0 0 3px' : '') + (i === shades.length - 1 ? ';border-radius:0 3px 3px 0' : '') +
             '"></div><div style="font-size:10.5px;color:#5A687D;margin-top:2px">' + t + '</div></div>';
    }).join('');
    var h = '<div class="mlg-h">' + (mode === 'official' ? 'IDSP records' : 'Outbreaks in the news') + ' per district</div>' +
            '<div style="display:flex;gap:2px">' + ramp + '</div>';
    if (mode !== 'official') h += '<div class="mlg-r" style="margin-top:7px"><span style="width:16px;height:11px;' +
        'border:2px solid ' + D.alertColour + ';border-radius:2px"></span>Reports rising (last 7 days)</div>';
    if (secondary) h += '<div class="mlg-r"><span style="width:16px;height:12px;border-radius:2px;flex:0 0 auto;' +
        'background:repeating-linear-gradient(-45deg,' + D.officialColour + ' 0 2px,transparent 2px 5.5px),' +
        shades[1] + '"></span>Green stripes: IDSP records (stripes alone: IDSP records, no news)</div>';
    if (selected !== null) {
      h += '<div class="mlg-h">In ' + districts[selected].name + '</div>';
      if (mode !== 'official') D.statusOrder.forEach(function (s) {
        h += '<div class="mlg-r"><span style="width:11px;height:11px;border-radius:50%;background:' +
             D.statusColours[s] + ';border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.2)"></span>' +
             D.statusLabels[s] + '</div>';
      });
      if (mode !== 'news') h += '<div class="mlg-r"><span style="width:11px;height:11px;border-radius:3px;background:' +
             D.officialColour + ';border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.2)"></span>IDSP outbreak record</div>';
      h += '<div class="mlg-r" style="color:#5A687D">A larger dot: several at one place</div>';
    }
    h += '<div class="mlg-hint">' + (selected === null
          ? 'Hover a district for its numbers. Click it to see its reports on the map.'
          : 'Click outside the district, or close the panel, to go back.') + '</div>';
    legend._div.innerHTML = h;
  }

  function update() {
    var z = map.getZoom();
    for (var n = 4; n <= 12; n++) el.classList.toggle('zge' + n, z >= n);
    lines.district.setStyle({weight: z < 6 ? 0.4 : (z < 8.5 ? 0.7 : 1.1), opacity: z < 6 ? 0.7 : 0.95});
    lines.state.setStyle({weight: z < 6 ? 1.2 : (z < 8.5 ? 1.7 : 2.3)});
    if (z >= 6 && z < 9) statePills.addTo(map); else map.removeLayer(statePills);
  }
  map.on('zoomend', update);
  map.on('moveend', refreshDistrictNames);
  map.on('baselayerchange', function (e) {
    base = e.name;
    el.classList.toggle('imagery', base === 'Satellite');
    shapes.forEach(function (s) { if (!s._has) s.setStyle(noDataStyle[base]); });
  });
  update();
  updateLegend();
  refreshDistrictNames();
  // a district chosen in the focus selector opens once the map is ready (build_map then skips its own fit)
  if (D.select !== null && districts[D.select]) setTimeout(function () { select(D.select, true); }, 50);

  var overlays = {'District shading': fills, 'Rising outlines': alertLines, 'Place names': names};
  if (secondary) overlays['IDSP stripes'] = hatching;
  L.control.layers(bases, overlays,
                   {position: 'topright', collapsed: true}).addTo(map);
})();
{% endmacro %}
""")

    def __init__(self, data: dict):
        super().__init__()
        self._name = "IndiaMap"
        self.data = _js(data)
        self.background = MAP_BACKGROUND


def _js(value) -> str:
    """JSON for an inline <script>: '</' cannot end the script element."""
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def build_map(news: list[dict], official: list[dict], news_counts: dict, official_counts: dict,
              alerts: dict[int, dict], mode: str, bounds: list[list[float]] | None = None,
              max_zoom: int | None = None, select: int | None = None) -> folium.Map:
    """The map. news / official: items from news_items / official_items; *_counts: from district_counts;
    alerts: from alert_districts; mode: a key of MODES; select: a district (LGD code) to open selected."""
    # min and max zoom go in the options: folium passes min_zoom / max_zoom only to a tile layer, and the base
    # layers are added in the browser
    fmap = folium.Map(location=[22.5, 80.0], zoom_start=5, tiles=None, control_scale=True, minZoom=4, maxZoom=16,
                      max_bounds=True, min_lat=PAN_LIMITS[0][0], max_lat=PAN_LIMITS[1][0],
                      min_lon=PAN_LIMITS[0][1], max_lon=PAN_LIMITS[1][1], zoomSnap=0.25, zoomDelta=0.5)
    topo = dict(topology())
    halos = island_halos()
    centre = {}   # a state of small islands only (Lakshadweep): its name goes in the middle of the outline
    for d in topo["districts"]:
        if d["dc"] in halos:
            pts = halos[d["dc"]]
            centre[d["state"]] = (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
    whole = {s for s in centre if all(d["dc"] in halos for d in topo["districts"] if d["state"] == s)}
    topo["states"] = [{**s, "label": STATE_LABELS.get(s["name"], s["name"]),
                       **({"lx": round(centre[s["name"]][0], 3), "ly": round(centre[s["name"]][1], 3)}
                          if s["name"] in whole else {})} for s in topo["states"]]
    topo["districts"] = [{**d, "name": html.escape(d["name"]), "state": html.escape(d["state"])}
                         for d in topo["districts"]]
    data = {
        "topo": topo, "mode": mode, "select": select, "halos": halos,
        "news": {"items": [{k: v for k, v in i.items() if k != "ds"} for i in news] if mode != "official" else [],
                 "counts": news_counts},
        "official": {"items": official if mode != "news" else [], "counts": official_counts},
        "alerts": alerts if mode != "official" else {},
        "shades": SHADES, "breaks": [list(b) for b in SHADE_BREAKS],
        "tiles": [["Plain", None, "", 0]] + [[name, url, attribution, native]
                                              for name, (url, attribution, native) in TILES.items()],
        "background": MAP_BACKGROUND, "land": LAND, "alertColour": ALERT_COLOUR, "officialColour": OFFICIAL_COLOUR,
        "statusColours": EVENT_COLOURS, "statusLabels": EVENT_LABELS, "statusOrder": STATUS_ORDER,
    }
    fmap.add_child(IndiaMap(data))
    Fullscreen(position="topright").add_to(fmap)
    if select is None:   # a selected district sets the view itself (a second fit would undo it)
        fmap.fit_bounds(bounds or INDIA_BOUNDS, max_zoom=max_zoom)
    return fmap
