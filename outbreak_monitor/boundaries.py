"""State and district boundaries for the map, linked to the Local Government Directory (LGD).

Built once with `python -m outbreak_monitor.boundaries`; the app only reads the small file it writes,
reference/india_districts.json, a compact topology (the idea of TopoJSON):

  arcs       every border line once, as integers in 1/scale degree: [x0, y0, dx1, dy1, dx2, dy2, ...]
  districts  one per LGD district: LGD code (dc), name, state code (sc) and state, a point inside it for its label
             (lx, ly), and its polygons as rings of arc ids (~id: the arc runs backwards)
  states     one per state / union territory: name, code and a label point
  lines      arc ids of the borders between districts, between states, and of India's border and coast

Source: district boundaries of India (2023 edition, follows the official boundary of India, MIT licence) from
https://github.com/datta07/INDIAN-SHAPEFILES. Names in the source are partly garbled ("K>NGRA", "Bengal#ru") and its
codes mix LGD and Census 2011 codes, so each polygon is matched to the LGD list by code and name within its state.
Districts created after that edition (e.g. the new districts of Ladakh and Delhi) are drawn inside the district they
were carved from; districts abolished since (Rajasthan, December 2024) are merged back into their parent district.

Shared borders are simplified once, as lines between junctions, so neighbouring districts stay aligned and
no gaps open between them (the method of topology-aware GIS tools such as mapshaper and QGIS).
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import requests
from rapidfuzz import fuzz, process

from .gazetteer import _simplify

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "reference"
RAW_DIR = ROOT / "data" / "reference"
DISTRICTS_FILE = REFERENCE_DIR / "india_districts.json"
SOURCE_URL = "https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master/INDIA/INDIA_DISTRICTS.geojson"
TOLERANCE_DEG = 0.006   # about 600 m: smooth down to district zoom
GRID = 1e5              # coordinates are compared at 1e-5 degrees (about 1 m)
SCALE = 1000            # and stored in 1/1000 degree (about 100 m)

# Source state names that are not LGD names
STATE_ALIASES = {"DADRA & NAGAR HAVELI & DAMAN & DIU": "The Dadra And Nagar Haveli And Daman And Diu"}
# (LGD state, source district) -> LGD district: renamed districts, and districts abolished in Rajasthan in
# December 2024 that went back to their parent district
DISTRICT_ALIASES = {
    ("Rajasthan", "DUDU"): "Jaipur", ("Rajasthan", "JAIPUR(GRAMIN)"): "Jaipur",
    ("Rajasthan", "JODHPUR GRAMIN"): "Jodhpur", ("Rajasthan", "KEKRI"): "Ajmer",
    ("Rajasthan", "NEEM-KA-THANA"): "Sikar", ("Rajasthan", "SANCHORE"): "Jalore",
    ("Rajasthan", "GANGAPURCITY"): "Sawai Madhopur", ("Rajasthan", "SHAHPURA"): "Bhilwara",
    ("Rajasthan", "ANOOPGARH"): "Ganganagar",
    ("Delhi", "NAZUL"): "Central",
    ("Puducherry", "YANAM"): "Puducherry", ("Puducherry", "MAHE"): "Puducherry",
    ("West Bengal", "HUGLI"): "Hooghly", ("West Bengal", "H>ORA"): "Howrah",
    ("West Bengal", "KOCH BIH>R"): "Cooch Behar", ("Odisha", "CUTTACK"): "Kataka",
    ("Odisha", "BALASORE"): "Baleshwar", ("Odisha", "DEOGARH"): "Debagada", ("Odisha", "BARGARH"): "Baragada",
    ("Maharashtra", "AHMEDNAGAR"): "Ahilyanagar", ("Maharashtra", "RAIGARH"): "Raigad",
    ("Assam", "KARIMGANJ"): "Sribhumi", ("Ladakh", "LEH"): "Leh Ladakh",
    ("Karnataka", "R"): "Bengaluru South",   # Ramanagara, renamed in 2025
    ("Telangana", "KOMARRAM BHEEM"): "Kumuram Bheem Asifabad", ("Telangana", "WANPARTI"): "Wanaparthy",
    ("Chhattisgarh", "KAWARDHA (KABIRDHAM)"): "Kabeerdham",
}
# LGD names shown on the map in their common English form (LGD uses Odia spellings for Odisha's districts);
# the LGD code is still shown with them
COMMON_NAMES = {"Kataka": "Cuttack", "Baleshwar": "Balasore", "Debagada": "Deogarh", "Baragada": "Bargarh",
                "Anugola": "Angul", "Sundaragada": "Sundargarh", "Nayagada": "Nayagarh", "Kendrapada": "Kendrapara",
                "Kandhamala": "Kandhamal", "Kendujhar": "Keonjhar", "Jagatsinghapur": "Jagatsinghpur",
                "Lakshadweep District": "Lakshadweep", "Leh Ladakh": "Leh", "Lahaul And Spiti": "Lahaul and Spiti"}
# Districts with a boundary but no LGD entry: shown with their own name (no LGD code)
UNLINKED_NAMES = {"MIRPUR": "Mirpur", "MUZAFFARABAD": "Muzaffarabad", "SHAHADARA": "Shahdara"}
# Characters the source uses in place of accented letters
GARBLED = str.maketrans({">": "a", "|": "i", "@": "u", "#": "u", "\\": "i"})


def _norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.translate(GARBLED).lower().replace("&", "and"))


def display_state(name: str) -> str:
    """LGD spells state names in title case ('Jammu And Kashmir'); maps use 'Jammu and Kashmir'."""
    name = re.sub(r"^The ", "", name)
    return re.sub(r" (And|Of) ", lambda m: f" {m.group(1).lower()} ", name)


def _lgd_rows(name: str) -> list[dict]:
    with open(REFERENCE_DIR / "lgd" / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def match_districts(features: list[dict]) -> tuple[list[dict], list[str]]:
    """Give every source polygon its LGD state and district. Returns the features with properties
    {dc, district, sc, state} (dc = sc = 0 for the unnamed slivers along a few state borders) and a list of
    polygons that could not be matched."""
    states = {_norm(r["state"]): r for r in _lgd_rows("states")}
    districts = _lgd_rows("districts")
    out, problems = [], []
    for feature in features:
        p = feature["properties"]
        if not p.get("state") or not p.get("district"):   # "DISPUTED (...)" slivers in the source
            out.append({"geometry": feature["geometry"], "properties": {"dc": 0, "district": "", "sc": 0, "state": ""}})
            continue
        state = states.get(_norm(STATE_ALIASES.get(p["state"], p["state"])))
        if state is None:
            problems.append(f"no LGD state for {p['state']} / {p['district']}")
            continue
        rows = [r for r in districts if r["state_code"] == state["state_code"]]
        by_name = {_norm(r["district"]): r for r in rows}
        alias = DISTRICT_ALIASES.get((display_state(state["state"]), p["district"]))
        row = by_name.get(_norm(alias)) if alias else None
        name = _norm(p["district"])
        if row is None:   # 1. the same name, give or take a spelling
            best = process.extractOne(name, list(by_name), scorer=fuzz.ratio)
            row = by_name[best[0]] if best and best[1] >= 85 else None
        if row is None:   # 2. the code; new districts share their parent's Census code, so the name decides
            code = str(p.get("dist_code") or "").lstrip("0")
            by_code = [r for r in rows if code.isdigit() and code in (r["district_code"], r["census2011"].lstrip("0"))]
            if by_code:
                row = max(by_code, key=lambda r: fuzz.ratio(_norm(r["district"]), name))
                truncated = len(name) < 6 or re.search(r"[>|@#\\]", p["district"])   # garbled in the source
                if fuzz.ratio(_norm(row["district"]), name) < 60 and not truncated:
                    row = None   # the code is of another district (the source mixes LGD and Census codes)
        if row is None and len(name) >= 6:   # 3. a longer form of the name ("Kawardha (Kabirdham)")
            best = process.extractOne(name, list(by_name), scorer=fuzz.WRatio)
            row = by_name[best[0]] if best and best[1] >= 85 else None
        if row is None and p["district"] in UNLINKED_NAMES:
            out.append({"geometry": feature["geometry"],
                        "properties": {"dc": -len(out) - 1, "district": UNLINKED_NAMES[p["district"]],
                                       "sc": int(state["state_code"]), "state": display_state(state["state"])}})
            continue
        if row is None:
            problems.append(f"no LGD district for {state['state']} / {p['district']} ({p.get('dist_code')})")
            continue
        out.append({"geometry": feature["geometry"],
                    "properties": {"dc": int(row["district_code"]),
                                   "district": COMMON_NAMES.get(row["district"], row["district"]),
                                   "sc": int(state["state_code"]), "state": display_state(state["state"])}})
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
    quantised = []
    for arc in arcs:
        q = [(round(x / GRID * SCALE), round(y / GRID * SCALE)) for x, y in _simplify(arc, tolerance * GRID)]
        quantised.append([p for i, p in enumerate(q) if i == 0 or p != q[i - 1]])

    def ring_points(ring_arcs):
        points = []
        for a, flip in ring_arcs:
            pts = quantised[a][::-1] if flip else quantised[a]
            points += pts if not points else pts[1:]
        return [(x / SCALE, y / SCALE) for x, y in points]

    used, refs = {}, []   # arcs that are drawn get new, dense ids

    def ref(a, flip):
        if a not in used:
            used[a] = len(refs)
            refs.append(a)
        return ~used[a] if flip else used[a]

    by_district = defaultdict(list)
    for f, polys in enumerate(feature_rings):
        props = features[f]["properties"]
        if not props["dc"]:
            continue
        for rings in polys:
            outer = ring_points(rings[0])
            if len(outer) < 4 or _area(outer) < 1e-6:
                continue
            kept = [rings[0]] + [r for r in rings[1:] if len(ring_points(r)) >= 4]
            by_district[props["dc"]].append((outer, [[ref(a, flip) for a, flip in r] for r in kept], props))
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
        elif len({sc for _, sc in sides}) > 1:
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
    return {"scale": SCALE, "arcs": encoded, "districts": districts, "states": states, "lines": lines}


def build() -> None:
    raw = RAW_DIR / "INDIA_DISTRICTS.geojson"
    if not raw.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(SOURCE_URL, timeout=600)
        resp.raise_for_status()
        raw.write_bytes(resp.content)
    features, problems = match_districts(json.loads(raw.read_text(encoding="utf-8"))["features"])
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


def load(path: Path = DISTRICTS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    build()
