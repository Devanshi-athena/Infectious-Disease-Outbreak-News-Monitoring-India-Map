"""Local Government Directory (LGD) codes: the official codes of India's states, districts and sub-districts
(Ministry of Panchayati Raj, https://lgdirectory.gov.in).

The lists are downloaded once from LGD's public web service into reference/lgd/ and read from there:

    python -m outbreak_monitor.lgd --download

Each request is spaced like any other collection request (at most one per second).
"""

import argparse
import csv
import logging
import time
from pathlib import Path

import requests

from .fetch import MIN_SECONDS_PER_DOMAIN, USER_AGENT

log = logging.getLogger(__name__)

LGD_DIR = Path(__file__).resolve().parent.parent / "reference" / "lgd"
SERVICE = "https://lgdirectory.gov.in/webservices/lgdws/"
FILES = {"states": LGD_DIR / "states.csv", "districts": LGD_DIR / "districts.csv",
         "subdistricts": LGD_DIR / "subdistricts.csv"}


def _post(path: str) -> list[dict]:
    """One call to the LGD web service (it answers POST requests only)."""
    for attempt in range(3):
        try:
            time.sleep(MIN_SECONDS_PER_DOMAIN)
            resp = requests.post(SERVICE + path, headers={"User-Agent": USER_AGENT}, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 2:
                raise
            log.warning("LGD %s failed (%s); retrying", path, exc)
            time.sleep(5 * (attempt + 1))
    return []


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def download() -> dict[str, int]:
    """Download states, districts and sub-districts with their LGD and Census 2011 codes."""
    LGD_DIR.mkdir(parents=True, exist_ok=True)
    states = [dict(state_code=s["stateCode"], state=_clean(s["stateNameEnglish"]),
                   census2011=_clean(s.get("census2011Code"))) for s in _post("stateList")]
    districts, subdistricts = [], []
    for s in states:
        for d in _post(f"districtList?stateCode={s['state_code']}"):
            districts.append(dict(district_code=d["districtCode"], district=_clean(d["districtNameEnglish"]),
                                  state_code=s["state_code"], state=s["state"],
                                  census2011=_clean(d.get("census2011Code"))))
        log.info("LGD %s: %d districts", s["state"], sum(1 for d in districts if d["state_code"] == s["state_code"]))
    for d in districts:
        for sd in _post(f"subdistrictList?districtCode={d['district_code']}"):
            subdistricts.append(dict(subdistrict_code=sd["subdistrictCode"], subdistrict=_clean(sd["subdistrictNameEnglish"]),
                                     district_code=d["district_code"], district=d["district"],
                                     state_code=d["state_code"], state=d["state"],
                                     census2011=_clean(sd.get("census2011Code"))))
    for key, rows in (("states", states), ("districts", districts), ("subdistricts", subdistricts)):
        rows.sort(key=lambda r: tuple(str(v).lower() for v in r.values()))
        with open(FILES[key], "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return {"states": len(states), "districts": len(districts), "subdistricts": len(subdistricts)}


VILLAGES_FILE = LGD_DIR / "villages.csv.gz"
VILLAGE_FIELDS = ["village_code", "village", "subdistrict_code", "district_code", "census2011"]
VILLAGE_WORKERS = 4    # requests at a time; each still waits a second before asking


def download_villages() -> dict[str, int]:
    """Download the villages of every sub-district into VILLAGES_FILE (one request per sub-district, about
    3 hours). Progress is kept in data/lgd_villages_partial.csv, so an interrupted run continues where it
    stopped."""
    import gzip
    partial = LGD_DIR.parent.parent / "data" / "lgd_villages_partial.csv"
    partial.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if partial.exists():
        with open(partial, encoding="utf-8") as f:
            done = {r["subdistrict_code"] for r in csv.DictReader(f)}
    from concurrent.futures import ThreadPoolExecutor
    subdistricts = load("subdistricts")
    todo = [sd for sd in subdistricts if sd["subdistrict_code"] not in done]
    new_file = not partial.exists()
    with open(partial, "a", newline="", encoding="utf-8") as f, ThreadPoolExecutor(VILLAGE_WORKERS) as pool:
        writer = csv.DictWriter(f, fieldnames=VILLAGE_FIELDS)
        if new_file:
            writer.writeheader()
        answers = pool.map(lambda sd: _post(f"villageList?subDistrictCode={sd['subdistrict_code']}"), todo)
        for i, (sd, villages) in enumerate(zip(todo, answers), len(done) + 1):
            # a sub-district without villages (towns only) is marked with an empty row, so it is not asked again
            writer.writerows([dict(village_code=v["villageCode"], village=_clean(v["villageNameEnglish"]),
                                   subdistrict_code=sd["subdistrict_code"], district_code=sd["district_code"],
                                   census2011=_clean(v.get("census2011Code"))) for v in villages]
                             or [dict(village_code="", village="", subdistrict_code=sd["subdistrict_code"],
                                      district_code=sd["district_code"], census2011="")])
            f.flush()
            if i % 100 == 0:
                log.info("LGD villages: %d of %d sub-districts", i, len(subdistricts))
    with open(partial, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["village_code"]]
    rows.sort(key=lambda r: int(r["village_code"]))
    with gzip.open(VILLAGES_FILE, "wt", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=VILLAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"villages": len(rows), "subdistricts": len(subdistricts)}


def load(kind: str) -> list[dict]:
    """The downloaded list: 'states', 'districts', 'subdistricts' or 'villages'."""
    if kind == "villages":
        import gzip
        with gzip.open(VILLAGES_FILE, "rt", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    with open(FILES[kind], encoding="utf-8") as f:
        return list(csv.DictReader(f))


FUZZY_MIN_SCORE = 85   # close spelling of a district within the same state ("Charki Dadri" ~ "Charkhi Dadri")


class LgdCodes:
    """LGD codes for the reference place names (state, district, locality).

    A district matches by its name, by the reference data's other spellings, by config/lgd.yaml (renamed
    districts, places LGD keeps as a sub-district), or by a close spelling within the same state. A
    locality gets a sub-district code only if it is the name of exactly one sub-district of that district."""

    def __init__(self, places=None, overrides: dict | None = None):
        from .config import CONFIG_DIR, _read_yaml
        from .gazetteer import load_india, normalise_name
        from rapidfuzz import fuzz, process
        self._norm = normalise_name
        overrides = overrides if overrides is not None else _read_yaml(CONFIG_DIR / "lgd.yaml")
        places = places if places is not None else load_india()
        lgd_states = {normalise_name(s["state"]): s for s in load("states")}
        self.lgd_districts: dict[str, dict[str, dict]] = {}          # state code -> name -> district row
        for d in load("districts"):
            self.lgd_districts.setdefault(d["state_code"], {})[normalise_name(d["district"])] = d
        self.subdistricts: dict[str, dict[str, list[dict]]] = {}     # district code -> name -> rows
        self.subdistrict_names = {int(sd["subdistrict_code"]): normalise_name(sd["subdistrict"])
                                  for sd in load("subdistricts")}
        for sd in load("subdistricts"):
            self.subdistricts.setdefault(sd["district_code"], {}).setdefault(normalise_name(sd["subdistrict"]), []).append(sd)

        # towns and cities of the reference data: LGD's village list still has many places that are now towns
        # or parts of a city (Ichalkaranji, Virar), so these never get a village code
        self.towns = {(p.state, n) for p in places if p.kind == "town" for n in (normalise_name(p.name), *p.aliases)}
        self.states: dict[str, dict] = {}                            # reference state name -> LGD state row
        for p in (p for p in places if p.kind == "state"):
            names = [(overrides.get("states") or {}).get(p.name, p.name), p.name, *p.aliases]
            hit = next((lgd_states[normalise_name(n)] for n in names if normalise_name(n) in lgd_states), None)
            if hit:
                self.states[p.name] = hit
        self.districts: dict[tuple[str, str], tuple[dict, dict | None]] = {}   # (state, district) -> (district, sub)
        for p in (p for p in places if p.kind == "district"):
            state = self.states.get(p.state)
            pool = self.lgd_districts.get(state["state_code"], {}) if state else {}
            if not pool:
                continue
            target = (overrides.get("districts") or {}).get(f"{p.state}|{p.name}")
            sub = None
            if isinstance(target, dict):
                district = pool.get(normalise_name(target["district"]))
                subs = self.subdistricts.get(district["district_code"], {}).get(normalise_name(target["subdistrict"])) \
                    if district else None
                sub = subs[0] if subs and len(subs) == 1 else None
            elif target:
                district = pool.get(normalise_name(target))
            else:
                district = next((pool[n] for n in [normalise_name(p.name), *p.aliases] if n in pool), None)
                if district is None:
                    best = process.extractOne(normalise_name(p.name), list(pool), scorer=fuzz.ratio,
                                              score_cutoff=FUZZY_MIN_SCORE)
                    district = pool[best[0]] if best else None
            if district:
                self.districts[(p.state, p.name)] = (district, sub)
        # Stored locations use the display names of config/locations.yaml ("SAS Nagar (Mohali)" for Ajitgarh)
        display = _read_yaml(CONFIG_DIR / "locations.yaml").get("display_names") or {}
        for (state, name), codes in list(self.districts.items()):
            if name in display:
                self.districts.setdefault((state, display[name]), codes)

    def codes(self, state: str | None, district: str | None = None, locality: str | None = None,
              address: str | None = None) -> dict:
        """{'state_lgd_code', 'district_lgd_code', 'subdistrict_lgd_code', 'village_lgd_code'} for reference
        place names (None where a level is unknown or not uniquely identified).

        A village code is given only for a locality that is not a town, whose name belongs to exactly one
        village of the district, and whose geocoded address (OpenStreetMap) names that village's sub-district:
        LGD still lists many city areas as villages (Hadapsar in Pune), and a district may hold a village of
        the same name as one in a neighbouring district (Rajpur)."""
        out = dict(state_lgd_code=None, district_lgd_code=None, subdistrict_lgd_code=None, village_lgd_code=None)
        lgd_state = self.states.get(state) if state else None
        if not lgd_state:
            return out
        out["state_lgd_code"] = int(lgd_state["state_code"])
        lgd_district, sub = self.districts.get((state, district), (None, None)) if district else (None, None)
        if not lgd_district and district:   # a name LGD uses directly (e.g. in official reports)
            lgd_district = self.lgd_districts.get(lgd_state["state_code"], {}).get(self._norm(district))
        if not lgd_district:
            return out
        out["district_lgd_code"] = int(lgd_district["district_code"])
        if sub is None and locality:
            matches = self.subdistricts.get(lgd_district["district_code"], {}).get(self._norm(locality), [])
            sub = matches[0] if len(matches) == 1 else None
            village_like = (not matches and (state, self._norm(locality)) not in self.towns
                            and self._norm(locality) != self._norm(district))
            if village_like:   # not a sub-district or town: a village of that name, if the district has exactly one
                villages = self._villages().get(lgd_district["district_code"], {}).get(self._norm(locality), [])
                if len(villages) == 1 and address and self._names_subdistrict(address, villages[0][1]):
                    out["village_lgd_code"], out["subdistrict_lgd_code"] = villages[0]
        if sub:
            out["subdistrict_lgd_code"] = int(sub["subdistrict_code"])
        return out

    def _names_subdistrict(self, address: str, subdistrict_code: int) -> bool:
        """True if the address ('Bondari, Birsa Tahsil, Balaghat, ...') names the sub-district."""
        name = self.subdistrict_names.get(subdistrict_code)
        return bool(name) and f" {name} " in f" {self._norm(address)} "

    _village_index: dict[str, dict[str, list[tuple[int, int]]]] | None = None

    def _villages(self) -> dict[str, dict[str, list[tuple[int, int]]]]:
        """district code -> village name -> [(village code, sub-district code)], loaded on first use
        (empty if the village list has not been downloaded)."""
        if self._village_index is None:
            index: dict[str, dict[str, list[tuple[int, int]]]] = {}
            if VILLAGES_FILE.exists():
                import gzip
                with gzip.open(VILLAGES_FILE, "rt", encoding="utf-8") as f:
                    for code, name, sub, district, _ in csv.reader(f):
                        if code.isdigit() and name:
                            index.setdefault(district, {}).setdefault(self._norm(name), []).append((int(code), int(sub)))
            self._village_index = index
        return self._village_index


def build_new_districts(user_agent: str, delay: float = 1.1) -> dict:
    """Add the LGD districts that the reference places (GeoNames) lack, e.g. districts created since 2020,
    to reference/lgd_new_districts.csv, with coordinates from one OpenStreetMap Nominatim lookup each. A result
    is kept only if Nominatim places it in the district's state; districts without one are listed, not guessed."""
    from geopy.geocoders import Nominatim
    from .gazetteer import LGD_NEW_DISTRICTS_FILE, _read_places, INDIA_FILE, normalise_name
    reference = _read_places(INDIA_FILE)
    lookup = LgdCodes(places=reference)
    known = {int(d["district_code"]) for d, _ in lookup.districts.values()}
    state_name = {s["state_code"]: name for name, s in lookup.states.items()}   # LGD state code -> reference name
    geocoder = Nominatim(user_agent=user_agent, timeout=30)
    rows, missing = [], []
    for d in load("districts"):
        if int(d["district_code"]) in known or d["state_code"] not in state_name:
            continue
        state = state_name[d["state_code"]]
        name = d["district"].title() if d["district"].isupper() else d["district"]
        time.sleep(delay)
        hit = None
        for query in (f"{name} district, {state}, India", f"{name}, {state}, India"):
            hit = geocoder.geocode(query, country_codes="in", addressdetails=True, language="en")
            if hit and normalise_name(hit.raw.get("address", {}).get("state", "")) == normalise_name(state):
                break
            hit = None
            time.sleep(delay)
        if not hit:
            missing.append(f"{name}, {state}")
            continue
        aliases = sorted({normalise_name(n) for n in (d["district"], name.replace("-", " "))} - {normalise_name(name)})
        rows.append({"geonameid": f"LGD-{d['district_code']}", "name": name, "kind": "district", "state": state,
                     "district": name, "latitude": round(hit.latitude, 5), "longitude": round(hit.longitude, 5),
                     "population": 0, "aliases": "|".join(a for a in aliases if a)})
    with open(LGD_NEW_DISTRICTS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["geonameid", "name", "kind", "state", "district", "latitude", "longitude",
                                               "population", "aliases"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["state"], r["name"])))
    return {"added": len(rows), "not_found": missing}


_shared: LgdCodes | None = None


def lgd_codes() -> LgdCodes | None:
    """The shared lookup, or None if the LGD lists have not been downloaded."""
    global _shared
    if _shared is None and all(p.exists() for p in FILES.values()):
        _shared = LgdCodes()
    return _shared


def main():
    from .logs import setup_logging
    parser = argparse.ArgumentParser(description="LGD codes of states, districts and sub-districts.")
    parser.add_argument("--download", action="store_true", help=f"download the lists into {LGD_DIR}")
    parser.add_argument("--new-districts", action="store_true",
                        help="add the LGD districts missing from the reference places, located with Nominatim")
    parser.add_argument("--villages", action="store_true",
                        help="download the villages of every sub-district (about 3 hours, resumable)")
    args = parser.parse_args()
    setup_logging()
    if args.download:
        print("Downloaded:", download())
    if args.villages:
        print("Downloaded:", download_villages())
    if args.new_districts:
        from .config import CONFIG_DIR, _read_yaml
        geo = _read_yaml(CONFIG_DIR / "locations.yaml").get("geocoding") or {}
        print("New districts:", build_new_districts(geo["user_agent"], float(geo.get("min_delay_seconds", 1.1))))


if __name__ == "__main__":
    main()
