"""State and district boundaries for the map, linked to the Local Government Directory (LGD).

Built once with `python -m outbreak_monitor.boundaries`; the app only reads the small file it writes,
reference/india_districts.json, a compact topology (the idea of TopoJSON):

  arcs       every border line once, as integers in 1/scale degree: [x0, y0, dx1, dy1, dx2, dy2, ...]
  districts  one per LGD district: LGD code (dc), name, state code (sc) and state, a point inside it for its label
             (lx, ly), and its polygons as rings of arc ids (~id: the arc runs backwards)
  states     one per state / union territory: name, code and a label point
  lines      arc ids of the borders between districts, between states, and of India's border and coast
  disputed   the areas SOI marks disputed between two states, as polygons: land of India in no district

Source: the Survey of India Administrative Boundary Database (district level, 1:1 million), from
https://onlinemaps.surveyofindia.gov.in (Digital Products), as published with attribute and invalid-geometry
corrections in https://github.com/surajreddyr/India-State-District-Administrative-Boundaries-Shapefiles. Its
coordinates are in the Lambert Conformal Conic projection of India on WGS 84 (lcc_to_lonlat), and every district
carries its LGD code, so districts are linked to the LGD list by code. Districts created after that edition are drawn
inside the district they were carved from. The India outline, state and district borders all come from this one
source, so they meet exactly.

Shared borders are simplified once, as lines between junctions, so neighbouring districts stay aligned and
no gaps open between them (the method of topology-aware GIS tools such as mapshaper and QGIS).
"""

import csv
import io
import json
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import requests
import shapefile

from .gazetteer import _simplify

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "reference"
RAW_DIR = ROOT / "data" / "reference"
DISTRICTS_FILE = REFERENCE_DIR / "india_districts.json"
SOI_DIR = RAW_DIR / "soi"
SOI_DISTRICTS = SOI_DIR / "DISTRICT_BOUNDARY_Corrected.shp"
SOI_REPO = "https://raw.githubusercontent.com/surajreddyr/India-State-District-Administrative-Boundaries-Shapefiles/main/"
SOI_PARTS = ("India_ADMIN_SOI_Districts.zip.001", "India_ADMIN_SOI_Districts.zip.002")
TOLERANCE_DEG = 0.006   # about 600 m: smooth down to district zoom
GRID = 1e5              # coordinates are compared at 1e-5 degrees (about 1 m)
SCALE = 1000            # and stored in 1/1000 degree (about 100 m)

# LGD names shown on the map in their common English form (LGD uses Odia spellings for Odisha's districts);
# the LGD code is still shown with them
COMMON_NAMES = {"Kataka": "Cuttack", "Baleshwar": "Balasore", "Debagada": "Deogarh", "Baragada": "Bargarh",
                "Anugola": "Angul", "Sundaragada": "Sundargarh", "Nayagada": "Nayagarh", "Kendrapada": "Kendrapara",
                "Kandhamala": "Kandhamal", "Kendujhar": "Keonjhar", "Jagatsinghapur": "Jagatsinghpur",
                "Lakshadweep District": "Lakshadweep", "Leh Ladakh": "Leh", "Lahaul And Spiti": "Lahaul and Spiti"}
# SOI districts whose code is not in the LGD list: LGD counts Mahe and Yanam as parts of Puducherry district
PART_OF = {"MAHE": 600, "YANAM": 600}
# Districts with a boundary but no LGD entry: shown with their own name and a code of their own below zero
UNLINKED = {"MIRPUR": (-1, "Mirpur"), "MUZAFFARABAD": (-2, "Muzaffarabad"), "SHAHADRA": (-3, "Shahdara")}

# The SOI projection: Lambert Conformal Conic on WGS 84 (the same as EPSG:7755, WGS 84 / India NSF LCC)
_A, _F = 6378137.0, 1 / 298.257223563
_E = math.sqrt(2 * _F - _F * _F)
_LAT1, _LAT2, _LAT0, _LON0 = (math.radians(v) for v in (12.472944, 35.172806, 24.0, 80.0))
_FALSE_EASTING = _FALSE_NORTHING = 4_000_000.0


def _m(lat):
    return math.cos(lat) / math.sqrt(1 - (_E * math.sin(lat)) ** 2)


def _t(lat):
    s = _E * math.sin(lat)
    return math.tan(math.pi / 4 - lat / 2) / ((1 - s) / (1 + s)) ** (_E / 2)


_N = (math.log(_m(_LAT1)) - math.log(_m(_LAT2))) / (math.log(_t(_LAT1)) - math.log(_t(_LAT2)))
_AF = _A * _m(_LAT1) / (_N * _t(_LAT1) ** _N)
_RHO0 = _AF * _t(_LAT0) ** _N


def lcc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Metres in the SOI Lambert projection -> (longitude, latitude) in degrees, WGS 84 (Snyder, Map Projections:
    A Working Manual, 1987, equations 15-5 and 15-7 to 15-11)."""
    dx, dy = x - _FALSE_EASTING, _RHO0 - (y - _FALSE_NORTHING)
    rho = math.copysign(math.hypot(dx, dy), _N)
    t = (rho / _AF) ** (1 / _N)
    lat = math.pi / 2 - 2 * math.atan(t)
    for _ in range(8):
        s = _E * math.sin(lat)
        lat = math.pi / 2 - 2 * math.atan(t * ((1 - s) / (1 + s)) ** (_E / 2))
    return math.degrees(math.atan2(dx, dy) / _N + _LON0), math.degrees(lat)


def display_state(name: str) -> str:
    """LGD spells state names in title case ('Jammu And Kashmir'); maps use 'Jammu and Kashmir'."""
    name = re.sub(r"^The ", "", name)
    return re.sub(r" (And|Of) ", lambda m: f" {m.group(1).lower()} ", name)


def _lgd_rows(name: str) -> list[dict]:
    with open(REFERENCE_DIR / "lgd" / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_soi(path: Path = SOI_DISTRICTS) -> list[dict]:
    """The SOI district polygons as GeoJSON-like features in longitude / latitude, with the source's attributes
    (District, STATE, State_LGD, DISTRICT_L = the LGD district code)."""
    features = []
    with shapefile.Reader(str(path), encoding="utf-8") as reader:
        for record in reader.iterShapeRecords():
            geometry = record.shape.__geo_interface__
            polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
            coordinates = [[[lcc_to_lonlat(x, y) for x, y in ring] for ring in polygon] for polygon in polygons]
            features.append({"geometry": {"type": "MultiPolygon", "coordinates": coordinates},
                             "properties": record.record.as_dict()})
    return features


def match_districts(features: list[dict]) -> tuple[list[dict], list[str]]:
    """Give every SOI polygon its LGD state and district, by the LGD code it carries. Returns the features with
    properties {dc, district, sc, state} (dc = sc = 0 for the areas SOI marks "DISPUTED" between two states) and a
    list of polygons that could not be matched."""
    states = {int(r["state_code"]): r for r in _lgd_rows("states")}
    districts = {int(r["district_code"]): r for r in _lgd_rows("districts")}
    out, problems = [], []
    for feature in features:
        p = feature["properties"]
        name, code = p["District"].strip(), str(p["DISTRICT_L"] or "").strip()
        if name.startswith("DISPUTED"):
            out.append({"geometry": feature["geometry"], "properties": {"dc": 0, "district": "", "sc": 0, "state": ""}})
            continue
        dc = PART_OF.get(name, int(code) if code.isdigit() else None)
        row = districts.get(dc)
        if row is not None:
            state = states[int(row["state_code"])]
            props = {"dc": dc, "district": COMMON_NAMES.get(row["district"], row["district"])}
        elif name in UNLINKED and int(p["State_LGD"] or 0) in states:
            state = states[int(p["State_LGD"])]
            props = {"dc": UNLINKED[name][0], "district": UNLINKED[name][1]}
        else:
            problems.append(f"no LGD district for {p['STATE']} / {name} ({code or 'no code'})")
            continue
        out.append({"geometry": feature["geometry"],
                    "properties": {**props, "sc": int(state["state_code"]), "state": display_state(state["state"])}})
    return out, problems


# ---------------------------------------------------------------- geometry helpers

def _rings(geometry: dict) -> list[list[list]]:
    """Polygons as lists of rings: [[outer, hole, ...], ...]."""
    return geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]


def _inside(x: float, y: float, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _in_polygon(x, y, polygon) -> bool:
    return _inside(x, y, polygon[0]) and not any(_inside(x, y, hole) for hole in polygon[1:])


def _area(ring) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _segment_distance(px, py, x1, y1, x2, y2) -> float:
    dx, dy = x2 - x1, y2 - y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
    return ((px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2) ** 0.5


def label_point(polygon, steps: int = 14) -> tuple[float, float]:
    """A point well inside the polygon, for its label: of a grid of points inside, the one farthest from the edge
    (a simple form of the 'pole of inaccessibility')."""
    outer = polygon[0]
    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
    best, point = -1.0, (sum(xs) / len(xs), sum(ys) / len(ys))
    edges = [e for ring in polygon for e in zip(ring, ring[1:] + ring[:1])]
    for i in range(steps + 1):
        for j in range(steps + 1):
            x = min(xs) + (max(xs) - min(xs)) * (i + 0.5) / (steps + 1)
            y = min(ys) + (max(ys) - min(ys)) * (j + 0.5) / (steps + 1)
            if not _in_polygon(x, y, polygon):
                continue
            d = min(_segment_distance(x, y, *a, *b) for a, b in edges)
            if d > best:
                best, point = d, (x, y)
    return round(point[0], 3), round(point[1], 3)


# ---------------------------------------------------------------- topology

def _arcs(features: list[dict]):
    """Split every ring into arcs between junctions (points where three or more areas meet, or where neighbours'
    outlines part). Each distinct arc is stored once. Returns (arcs, owners, rings): arcs[i] is a list of points,
    owners[i] the features on either side, and rings[f] the rings of feature f as lists of (arc, reversed)."""
    quantised = []
    neighbours = defaultdict(set)
    for feature in features:
        polys = []
        for polygon in _rings(feature["geometry"]):
            rings = []
            for ring in polygon:
                q = [(round(p[0] * GRID), round(p[1] * GRID)) for p in ring]
                q = [p for i, p in enumerate(q) if i == 0 or p != q[i - 1]]
                if q[0] == q[-1]:
                    q = q[:-1]
                if len(q) < 3:
                    continue
                for i, p in enumerate(q):
                    neighbours[p].update((q[i - 1], q[(i + 1) % len(q)]))
                rings.append(q)
            if rings:
                polys.append(rings)
        quantised.append(polys)
    junction = {p for p, s in neighbours.items() if len(s) != 2}
    arc_ids, arcs, owners = {}, [], []
    feature_rings = []
    for f, polys in enumerate(quantised):
        out_polys = []
        for rings in polys:
            out_rings = []
            for q in rings:
                starts = [i for i, p in enumerate(q) if p in junction]
                pieces = []   # (arc as stored, whether the ring runs along it backwards)
                if not starts:   # a ring that touches no junction: one closed arc, in a canonical rotation
                    i = q.index(min(q))
                    r = q[i:] + q[:i]
                    rev = [r[0]] + r[:0:-1]
                    pieces.append((tuple(r + [r[0]]), False) if r[1] <= rev[1] else (tuple(rev + [rev[0]]), True))
                else:
                    r = q[starts[0]:] + q[:starts[0]] + [q[starts[0]]]
                    cut = [i for i, p in enumerate(r) if p in junction]
                    for a, b in zip(cut, cut[1:]):
                        key, rkey = tuple(r[a:b + 1]), tuple(reversed(r[a:b + 1]))
                        pieces.append((rkey, True) if rkey < key else (key, False))
                ring_arcs = []
                for canonical, flip in pieces:
                    if canonical not in arc_ids:
                        arc_ids[canonical] = len(arcs)
                        arcs.append(list(canonical))
                        owners.append([])
                    a = arc_ids[canonical]
                    owners[a].append(f)
                    ring_arcs.append((a, flip))
                out_rings.append(ring_arcs)
            out_polys.append(out_rings)
        feature_rings.append(out_polys)
    return arcs, owners, feature_rings


def _other_side(arc, owner: int, features, boxes) -> int | None:
    """For an arc only one feature uses (the neighbour's outline differs from it): the feature across it, found by
    stepping off the arc on the side away from its owner, at a few points along it and further each time (about
    20 m, 200 m, 1 km), since the two outlines can lie apart. None: nothing there, the coast or India's border."""
    owner_polys = _rings(features[owner]["geometry"])
    n = len(arc)
    for i in dict.fromkeys(max(0, min(n - 2, round(n * f) - 1)) for f in (0.5, 0.25, 0.75)):
        (x1, y1), (x2, y2) = arc[i], arc[i + 1]
        mx, my = (x1 + x2) / 2 / GRID, (y1 + y2) / 2 / GRID
        dx, dy = (x2 - x1) / GRID, (y2 - y1) / GRID
        norm = (dx * dx + dy * dy) ** 0.5 or 1
        for step in (2e-4, 2e-3, 1e-2):
            for side in (1, -1):
                px, py = mx - side * dy / norm * step, my + side * dx / norm * step
                if any(_in_polygon(px, py, poly) for poly in owner_polys):
                    continue   # the owner's side
                for f, (x0, y0, x9, y9) in enumerate(boxes):
                    if f != owner and x0 <= px <= x9 and y0 <= py <= y9 and \
                            any(_in_polygon(px, py, poly) for poly in _rings(features[f]["geometry"])):
                        return f
    return None


def build_layers(features: list[dict], tolerance: float = TOLERANCE_DEG) -> dict:
    """The compact layer file (see the module docstring): shared arcs stored once, delta-encoded integers."""
    arcs, owners, feature_rings = _arcs(features)
    boxes = []
    for feature in features:
        pts = [p for poly in _rings(feature["geometry"]) for p in poly[0]]
        boxes.append((min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)))
    for a, own in enumerate(owners):
        if len(set(own)) == 1:
            other = _other_side(arcs[a], own[0], features, boxes)
            if other is not None:
                own.append(other)
    # Simplify each arc once, then store it in units of 1/SCALE degree
    def simplified(arc, tol):
        q = [(round(x / GRID * SCALE), round(y / GRID * SCALE)) for x, y in _simplify(arc, tol * GRID)]
        return [p for i, p in enumerate(q) if i == 0 or p != q[i - 1]]

    def arc_tolerance(arc, own):
        """An island (a closed outline no other area shares) is simplified in proportion to its size, so a small
        island keeps its shape instead of becoming a sliver; every other line at the full tolerance."""
        if len(set(own)) > 1 or arc[0] != arc[-1]:
            return tolerance
        xs, ys = [p[0] for p in arc], [p[1] for p in arc]
        return min(tolerance, max(max(xs) - min(xs), max(ys) - min(ys)) / GRID / 15)

    quantised = [simplified(arc, arc_tolerance(arc, own)) for arc, own in zip(arcs, owners)]

    def ring_points(ring_arcs):
        points = []
        for a, flip in ring_arcs:
            pts = quantised[a][::-1] if flip else quantised[a]
            points += pts if not points else pts[1:]
        return [(x / SCALE, y / SCALE) for x, y in points]

    # A small island would flatten into a line at the full tolerance and vanish: its outline is simplified more
    # finely instead, so every island of a district (Lakshadweep, Andaman and Nicobar) stays on the map
    for polys in feature_rings:
        for rings in polys:
            for tol in (tolerance / 4, tolerance / 16, 0):
                if len(ring_points(rings[0])) >= 4:
                    break
                for a, _ in rings[0]:
                    quantised[a] = simplified(arcs[a], tol)

    used, refs = {}, []   # arcs that are drawn get new, dense ids

    def ref(a, flip):
        if a not in used:
            used[a] = len(refs)
            refs.append(a)
        return ~used[a] if flip else used[a]

    by_district = defaultdict(list)
    disputed = []   # the areas SOI marks disputed between two states: land of India, in no district
    for f, polys in enumerate(feature_rings):
        props = features[f]["properties"]
        for rings in polys:
            outer = ring_points(rings[0])
            if len(outer) < 4 or _area(outer) < 1e-8:   # only a speck smaller than the stored grid
                continue
            kept = [rings[0]] + [r for r in rings[1:] if len(ring_points(r)) >= 4]
            polygon = [[ref(a, flip) for a, flip in r] for r in kept]
            if props["dc"]:
                by_district[props["dc"]].append((outer, polygon, props))
            else:
                disputed.append(polygon)
    districts = []
    for dc, parts in by_district.items():
        props = parts[0][2]
        largest = max(parts, key=lambda part: _area(part[0]))
        lx, ly = label_point([largest[0]])
        districts.append({"dc": dc, "name": props["district"], "sc": props["sc"], "state": props["state"],
                          "lx": lx, "ly": ly, "area": round(sum(_area(p[0]) for p in parts), 4),
                          "polygons": [p[1] for p in parts]})
    lines = {"district": [], "state": [], "india": []}
    for a, own in enumerate(owners):
        sides = {(features[f]["properties"]["dc"], features[f]["properties"]["sc"]) for f in own}
        if len(quantised[a]) < 2 or len(sides) < 2 and len(own) > 1:
            continue   # inside one district (the parts of a merged district)
        if len(own) == 1:
            kind = "india"   # coast or the border of India
        elif len({sc for _, sc in sides if sc}) > 1:   # (the edge of a disputed area is drawn as a district line)
            kind = "state"
        else:
            kind = "district"
        lines[kind].append(ref(a, False))
    encoded = []
    for a in refs:
        pts = quantised[a]
        flat = [pts[0][0], pts[0][1]]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            flat += [x2 - x1, y2 - y1]
        encoded.append(flat)
    # A point for each state's label: the centre of its districts, if it falls inside one of them, else the label
    # point of its largest district
    states = []
    for sc in sorted({d["sc"] for d in districts}):
        own = [d for d in districts if d["sc"] == sc]
        total = sum(d["area"] for d in own) or 1
        cx, cy = sum(d["lx"] * d["area"] for d in own) / total, sum(d["ly"] * d["area"] for d in own) / total
        inside = any(_in_polygon(cx, cy, [ring_points([(refs[~r] if r < 0 else refs[r], r < 0) for r in ring])
                                          for ring in poly]) for d in own for poly in d["polygons"])
        biggest = max(own, key=lambda d: d["area"])
        lx, ly = (round(cx, 3), round(cy, 3)) if inside else (biggest["lx"], biggest["ly"])
        states.append({"sc": sc, "name": own[0]["state"], "lx": lx, "ly": ly, "area": round(total, 3)})
    return {"scale": SCALE, "arcs": encoded, "districts": districts, "states": states, "lines": lines,
            "disputed": disputed}


def download_soi() -> None:
    """The SOI district shapefile (a zip in two parts) into data/reference/soi/."""
    data = b""
    for part in SOI_PARTS:
        resp = requests.get(SOI_REPO + part, timeout=600)
        resp.raise_for_status()
        data += resp.content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(SOI_DIR)


def build() -> None:
    if not SOI_DISTRICTS.exists():
        download_soi()
    features, problems = match_districts(read_soi())
    for problem in problems:
        print("  !", problem)
    layers = build_layers(features)
    DISTRICTS_FILE.write_text(json.dumps(layers, separators=(",", ":")), encoding="utf-8")
    covered = {d["dc"] for d in layers["districts"]}
    missing = [f"{r['district']} ({display_state(r['state'])})" for r in _lgd_rows("districts")
               if int(r["district_code"]) not in covered]
    print(f"Wrote {len(covered)} districts and their borders to {DISTRICTS_FILE} "
          f"({DISTRICTS_FILE.stat().st_size / 1e6:.1f} MB)")
    print(f"{len(missing)} LGD districts have no boundary of their own (drawn inside their parent): "
          + ", ".join(missing))


if __name__ == "__main__":
    build()
