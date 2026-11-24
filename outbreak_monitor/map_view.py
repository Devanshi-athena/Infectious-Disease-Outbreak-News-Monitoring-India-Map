"""The India map of the dashboard.

Detail is revealed by zoom, so the map never shows everything at once:
  India view     districts shaded by the number of reports; districts with an alert outlined in red
  State view     one circle per district with reports (size = number), with the district's name
  District view  each report as a dot at its place; reports at the same place share one dot with a number
Clicking a district, circle or dot opens a panel with its reports. The map shows news reports, official IDSP
records, or both (news, with the IDSP records as green badges).

Everything written on the map comes from our own data (LGD names in English). The optional backgrounds carry no
labels (Esri World Shaded Relief or World Imagery), so no place name in another script ever shows, and everything
outside India's official boundary is covered.
"""

import html
import json
from datetime import date
from functools import lru_cache

import folium
import pandas as pd
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template

from .boundaries import DISTRICTS_FILE, _in_polygon
from .dashboard_data import EVENT_COLOURS, EVENT_LABELS, OFFICIAL_COLOUR, _esc, published_ist

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
def _decoded() -> dict[int, list]:
    """dc -> polygons as lists of [x, y] rings (for locating a point in a district)."""
    topo = topology()
    scale = topo["scale"]
    arcs = []
    for flat in topo["arcs"]:
        x, y, pts = flat[0], flat[1], [(flat[0], flat[1])]
        for i in range(2, len(flat), 2):
            x, y = x + flat[i], y + flat[i + 1]
            pts.append((x, y))
        arcs.append([(px / scale, py / scale) for px, py in pts])
    out = {}
    for d in topo["districts"]:
        polys = []
        for poly in d["polygons"]:
            rings = []
            for ring in poly:
                pts = []
                for r in ring:
                    arc = arcs[~r][::-1] if r < 0 else arcs[r]
                    pts += arc if not pts else arc[1:]
                rings.append(pts)
            polys.append(rings)
        out[d["dc"]] = polys
    return out


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


def _district_of(code, lat, lon) -> int | None:
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


NEWS_COLUMNS = ["story_id", "published_at", "event_status", "diseases", "headline", "headline_original",
                "language_name", "article_url", "source_name", "place", "state", "district_lgd_code", "latitude",
                "longitude"]


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def news_items(df: pd.DataFrame) -> list[dict]:
    """One item per story located in India, newest first: position, district, status, diseases and a small card
    (HTML, every value escaped) for the map's panel. Works on plain records: much faster than pandas row access."""
    if df.empty:
        return []
    located = df[(df["country"] == "India") & df["latitude"].notna() & df["longitude"].notna()]
    stories: dict = {}
    for row in located.sort_values("published_at")[NEWS_COLUMNS].to_dict("records"):
        stories.setdefault(row["story_id"], []).append(row)
    items = []
    for group in stories.values():
        first = group[0]
        status = first["event_status"] if first["event_status"] in EVENT_COLOURS else "unclear"
        colour = EVENT_COLOURS[status]
        disease_list = sorted({d for r in group for d in (r["diseases"] or [])})
        original = first["headline_original"] if isinstance(first["headline_original"], str) and \
            first["headline_original"] else None
        sources = " · ".join(f'<a href="{_esc(r["article_url"])}" target="_blank" rel="noopener noreferrer">'
                             f'{_esc(r["source_name"])}</a>' for r in group)
        card = (f'<div class="mc" style="border-left-color:{colour}">'
                f'<div class="mc-top"><span class="mc-chip" style="color:{colour};background:{colour}1A">'
                f'{EVENT_LABELS[status]}</span><span class="mc-dis">{_esc(", ".join(disease_list) or "—")}</span>'
                f'<span class="mc-date">{_date(first["published_at"])}</span></div>'
                f'<a class="mc-h" href="{_esc(first["article_url"])}" target="_blank" rel="noopener noreferrer">'
                f'{_esc(first["headline"])}</a>'
                + (f'<div class="mc-o">{_esc(original)} · {_esc(first["language_name"])}</div>' if original else "")
                + f'<div class="mc-m">{_esc(first["place"])}</div><div class="mc-s">{sources}</div></div>')
        code = None if _missing(first["district_lgd_code"]) else first["district_lgd_code"]
        items.append({"la": round(float(first["latitude"]), 4), "lo": round(float(first["longitude"]), 4),
                      "dc": _district_of(code, first["latitude"], first["longitude"]),
                      "st": _esc(first["state"]) if isinstance(first["state"], str) else "",
                      "s": status, "p": _esc(first["place"]), "h": _esc(first["headline"]), "c": card,
                      "t": str(first["published_at"]), "ds": disease_list})
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
                f'<div class="mc-s">ID {_esc(r.report_id)} · <a href="{_esc(r.report_url)}" target="_blank" '
                'rel="noopener noreferrer">weekly report (PDF)</a></div></div>')
        code = getattr(r, "district_lgd_code", None)
        dc = (_district_of(code, r.latitude, r.longitude) if code is not None and not pd.isna(code)
              else district_at(float(r.longitude), float(r.latitude)) if r.location_level == "district" else None)
        items.append({"la": round(float(r.latitude), 4), "lo": round(float(r.longitude), 4), "dc": dc,
                      "st": _esc(r.state) if isinstance(r.state, str) else "",
                      "p": _esc(place), "h": _esc(title), "c": card})
    return items


def district_code(name: str, state: str) -> int | None:
    """The boundary (LGD code) of a district named as in the articles ("Sundargarh", "Odisha"), or None."""
    def norm(text):
        return "".join(ch for ch in str(text).lower() if ch.isalnum())
    for d in topology()["districts"]:
        if norm(d["name"]) == norm(name) and norm(d["state"]) == norm(state):
            return d["dc"]
    return None


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
        dc = int(a.district_lgd_code)
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
  .lbl-s.small { display: none; }
  .zge6 .lbl-s.small { display: block; }
  .zge9 .lbl-s { display: none; }
  .lbl-d { font-size: 10.5px; font-weight: 500; color: #5A687D; }
  .lbl-d.sel { font-size: 12.5px; font-weight: 700; color: #0B2545; }
  .imagery .lbl { color: #fff; text-shadow: 0 0 2px #000, 0 0 3px #000, 0 0 4px #000; }
  /* a selected district pops out: the rest dims, the district casts a shadow */
  .leaflet-fill-pane, .leaflet-hatch-pane, .leaflet-alerts-pane { transition: opacity .25s ease; }
  .has-sel .leaflet-fill-pane, .has-sel .leaflet-hatch-pane { opacity: .4; }
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
  var primary = mode === 'official' ? D.official : D.news;           // shading and dots
  var secondary = mode === 'both' ? D.official : null;              // green squares beside them
  var shades = D.shades[mode === 'official' ? 'official' : 'news'], breaks = D.breaks;
  [['fill', 350], ['hatch', 352], ['mask', 390], ['borders', 395], ['alerts', 396], ['sel', 398], ['labels', 420],
   ['pills', 640]].forEach(function (p) {
    map.createPane(p[0]).style.zIndex = p[1];
    if (p[0] !== 'fill' && p[0] !== 'pills') map.getPane(p[0]).style.pointerEvents = 'none';
  });
  var fillCanvas = L.canvas({pane: 'fill', padding: 0.3}), lineCanvas = L.canvas({pane: 'borders', padding: 0.3});
  var alertCanvas = L.canvas({pane: 'alerts', padding: 0.3});

  function shadeOf(n) {
    for (var i = 0; i < breaks.length; i++) {
      if (n >= breaks[i][0] && (breaks[i][1] === null || n <= breaks[i][1])) return shades[i];
    }
    return null;
  }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  var unit = mode === 'official' ? 'IDSP record' : 'news report';

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
    body += section(mode === 'official' ? 'Official IDSP records' : 'News reports', primaryBy[dc]);
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
  function rings(d) { return d.polygons.map(function (p) { return p.map(ring); }); }
  topo.districts.forEach(function (d) {
    var c = primary.counts[d.dc], sec = secondary && D.official.counts[d.dc];
    var shape = L.polygon(rings(d), {
      renderer: fillCanvas, stroke: false, fill: true, smoothFactor: 1.2,
      fillColor: c ? shadeOf(c.n) : D.land, fillOpacity: c ? 0.85 : 1
    });
    shape._has = !!c;
    shape.bindTooltip(districtTip(d), {sticky: true, className: 'dtip', direction: 'top', offset: [0, -8], opacity: 1});
    shape.on('tooltipopen', function () { if (selected === d.dc) this.closeTooltip(); });
    shape.on('click', function (e) {
      L.DomEvent.stopPropagation(e);
      if (c || sec) select(d.dc); else clearSelection();
    });
    fills.addLayer(shape);
    shapes.push(shape);
  });

  // Both: districts that also have official IDSP records are hatched in green over the news shading
  var hatching = L.layerGroup();
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
    var stripes = document.createElement('canvas').getContext('2d').createPattern(tile, 'repeat');
    var hatchCanvas = L.canvas({pane: 'hatch', padding: 0.3});
    topo.districts.forEach(function (d) {
      if (!D.official.counts[d.dc]) return;
      hatching.addLayer(L.polygon(rings(d), {renderer: hatchCanvas, stroke: false, fill: true, fillColor: stripes,
                                             fillOpacity: 0.85, interactive: false, smoothFactor: 1.2}));
    });
    hatching.addTo(map);
  }

  // Everything outside India's official boundary is covered
  L.geoJSON(D.mask, {pane: 'mask', interactive: false,
                     style: {fillColor: D.background, fillOpacity: 1, stroke: false}}).addTo(map);

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
  var districtNames = L.layerGroup();
  var names = L.layerGroup([L.layerGroup(topo.states.map(function (s) {
    return label(s.label, s.ly, s.lx, 'lbl-s' + (s.area < 1.2 ? ' small' : ''));
  })), districtNames]).addTo(map);
  function refreshDistrictNames() {
    districtNames.clearLayers();
    var z = map.getZoom(), view = map.getBounds().pad(0.15);
    topo.districts.forEach(function (d) {
      var isSel = d.dc === selected;
      if (!isSel && (z < 7 || (z < 8 && d.area <= 0.35) || !view.contains([d.ly, d.lx]))) return;
      districtNames.addLayer(label(d.name, d.ly, d.lx, 'lbl-d' + (isSel ? ' sel' : '')));
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
    var text = plural(g.length, 'state-wide ' + (mode === 'official' ? 'record' : 'report'));
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
  var selected = null, selLayer = L.layerGroup().addTo(map), dots = L.layerGroup().addTo(map);
  var selRenderer = L.svg({pane: 'sel', padding: 0.5});
  var rank = {'new': 0, 'suspected': 1, 'unclear': 2, 'historical': 3};
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
      var what = plural(g.length, official ? 'IDSP record' : 'news report');
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
    var shape = L.polygon(rings(d), {renderer: selRenderer, interactive: false, color: '#0B2545', weight: 2.5,
                                     fillColor: c ? shadeOf(c.n) : D.land, fillOpacity: 1});
    selLayer.addLayer(shape);
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
    var h = '<div class="mlg-h">' + (mode === 'official' ? 'IDSP records' : 'News reports') + ' per district</div>' +
            '<div style="display:flex;gap:2px">' + ramp + '</div>';
    if (mode !== 'official') h += '<div class="mlg-r" style="margin-top:7px"><span style="width:16px;height:11px;' +
        'border:2px solid ' + D.alertColour + ';border-radius:2px"></span>Reports rising (last 7 days)</div>';
    if (secondary) h += '<div class="mlg-r"><span style="width:16px;height:12px;border-radius:2px;flex:0 0 auto;' +
        'background:repeating-linear-gradient(-45deg,' + D.officialColour + ' 0 2px,transparent 2px 5.5px),' +
        shades[1] + '"></span>Green stripes: IDSP records (on white: IDSP only, no news)</div>';
    if (selected !== null) {
      h += '<div class="mlg-h">In ' + districts[selected].name + '</div>';
      if (mode !== 'official') D.statusOrder.forEach(function (s) {
        h += '<div class="mlg-r"><span style="width:11px;height:11px;border-radius:50%;background:' +
             D.statusColours[s] + ';border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.2)"></span>' +
             D.statusLabels[s] + '</div>';
      });
      if (mode !== 'news') h += '<div class="mlg-r"><span style="width:11px;height:11px;border-radius:3px;background:' +
             D.officialColour + ';border:2px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.2)"></span>IDSP outbreak record</div>';
      h += '<div class="mlg-r" style="color:#5A687D">A larger dot: several reports at one place</div>';
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
  // after the map's starting view is set (later in this page's script)
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
        self.alert_colour = ALERT_COLOUR
        self.official_colour = OFFICIAL_COLOUR


def _js(value) -> str:
    """JSON for an inline <script>: '</' cannot end the script element."""
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def build_map(news: list[dict], official: list[dict], news_counts: dict, official_counts: dict,
              alerts: dict[int, dict], mode: str, mask: dict, bounds: list[list[float]] | None = None,
              max_zoom: int | None = None, select: int | None = None) -> folium.Map:
    """The map. news / official: items from news_items / official_items; *_counts: from district_counts;
    alerts: from alert_districts; mode: a key of MODES; select: a district (LGD code) to open selected."""
    # min and max zoom go in the options: folium passes min_zoom / max_zoom only to a tile layer, and the base
    # layers are added in the browser
    fmap = folium.Map(location=[22.5, 80.0], zoom_start=5, tiles=None, control_scale=True, minZoom=4, maxZoom=16,
                      max_bounds=True, min_lat=PAN_LIMITS[0][0], max_lat=PAN_LIMITS[1][0],
                      min_lon=PAN_LIMITS[0][1], max_lon=PAN_LIMITS[1][1], zoomSnap=0.25, zoomDelta=0.5)
    topo = dict(topology())
    topo["states"] = [{**s, "label": STATE_LABELS.get(s["name"], s["name"])} for s in topo["states"]]
    topo["districts"] = [{**d, "name": html.escape(d["name"]), "state": html.escape(d["state"])}
                         for d in topo["districts"]]
    data = {
        "topo": topo, "mode": mode, "mask": mask, "select": select,
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
