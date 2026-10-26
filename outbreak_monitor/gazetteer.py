"""India place reference data (states/UTs, districts, towns) and foreign place names.

Built from GeoNames (https://www.geonames.org, CC BY 4.0) into two compact files kept in the repo:
    reference/india_places.csv    every state/UT, every district, every town with population >= 5,000
    reference/foreign_places.csv  names, capitals and first-level regions of all other countries
Rebuild (downloads ~20 MB from GeoNames into data/reference/):
    python -m outbreak_monitor.gazetteer
Villages are deliberately not included (hundreds of thousands of repeated names would cause false
matches); they are geocoded with Nominatim inside the district the article names (see location.py).
"""

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "reference"
INDIA_FILE = REFERENCE_DIR / "india_places.csv"
FOREIGN_FILE = REFERENCE_DIR / "foreign_places.csv"
RAW_DIR = ROOT / "data" / "reference"
GEONAMES = "https://download.geonames.org/export/dump/"
MIN_TOWN_POPULATION = 5000
GEONAMES_COLUMNS = ("geonameid name asciiname alternatenames latitude longitude feature_class feature_code "
                    "country_code cc2 admin1 admin2 admin3 admin4 population elevation dem timezone modified").split()

KIND_RANK = {"state": 0, "district": 1, "town": 2}


@dataclass
class Place:
    geonameid: str
    name: str            # display name, e.g. "Kozhikode"
    kind: str            # state / district / town
    state: str
    district: str | None
    latitude: float
    longitude: float
    population: int
    aliases: list[str] = field(default_factory=list)  # other spellings (lower case)


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalise_name(text: str) -> str:
    """Lower-case ASCII form used for matching: 'Tiruchirāppalli' -> 'tiruchirappalli',
    "G’nagar" / "G'nagar" -> 'gnagar' (apostrophes dropped), 'Pimpri-Chinchwad' -> 'pimpri chinchwad'."""
    text = re.sub(r"['’‘`]", "", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", ascii_fold(text).lower())).strip()


def display_name(name: str) -> str:
    """'State of Kerala' -> 'Kerala', 'Gurugram district' -> 'Gurugram', 'District Hamirpur' -> 'Hamirpur'."""
    name = ascii_fold(name).strip()
    name = re.sub(r"^(State of|Union Territory of|District)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+(district|division)$", "", name, flags=re.I)
    return {"National Capital Territory of Delhi": "Delhi"}.get(name, name)


def _usable_alias(alias: str) -> bool:
    """Latin-script spellings that could appear in English text: at least 4 letters, at most 4 words,
    no codes. (GeoNames also lists other scripts, transliterations into other languages and codes.)"""
    return (4 <= len(alias) <= 50 and alias == ascii_fold(alias) and not alias.isupper()
            and not any(ch.isdigit() for ch in alias) and len(alias.split()) <= 4)


# ---------------------------------------------------------------- build

def _download(name: str) -> bytes:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / name
    if not path.exists():
        resp = requests.get(GEONAMES + name, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return path.read_bytes()


def _rows(text: str):
    for line in text.splitlines():
        if line and not line.startswith("#"):
            yield line.split("\t")


def build() -> None:
    with zipfile.ZipFile(io.BytesIO(_download("IN.zip"))) as z:
        india = [dict(zip(GEONAMES_COLUMNS, r)) for r in _rows(z.read("IN.txt").decode("utf-8"))]
    states = {r["admin1"]: r for r in india if r["feature_code"] == "ADM1"}
    districts = {(r["admin1"], r["admin2"]): r for r in india if r["feature_code"] == "ADM2"}

    def aliases(r, exclude):
        names = {r["name"], r["asciiname"], *r["alternatenames"].split(",")}
        found = {normalise_name(display_name(a)) for a in names if a and _usable_alias(ascii_fold(a))}
        return sorted(a for a in found if a and a != exclude)

    out = []
    for code, r in states.items():
        name = display_name(r["name"])
        out.append([r["geonameid"], name, "state", name, "", r["latitude"], r["longitude"], r["population"] or 0,
                    "|".join(aliases(r, normalise_name(name)))])
    for (a1, _), r in districts.items():
        name, state = display_name(r["name"]), display_name(states[a1]["name"])
        out.append([r["geonameid"], name, "district", state, name, r["latitude"], r["longitude"],
                    r["population"] or 0, "|".join(aliases(r, normalise_name(name)))])
    for r in india:
        if r["feature_class"] != "P" or int(r["population"] or 0) < MIN_TOWN_POPULATION:
            continue
        district = districts.get((r["admin1"], r["admin2"]))
        if r["admin1"] not in states:
            continue
        name = display_name(r["name"])
        out.append([r["geonameid"], name, "town", display_name(states[r["admin1"]]["name"]),
                    display_name(district["name"]) if district else "", r["latitude"], r["longitude"],
                    r["population"], "|".join(aliases(r, normalise_name(name)))])

    REFERENCE_DIR.mkdir(exist_ok=True)
    with open(INDIA_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["geonameid", "name", "kind", "state", "district", "latitude", "longitude", "population", "aliases"])
        writer.writerows(sorted(out, key=lambda row: (KIND_RANK[row[2]], row[3], row[1])))

    countries = list(_rows(_download("countryInfo.txt").decode("utf-8")))
    country_names = {r[0]: r[4] for r in countries}
    foreign = {(r[4], "country", r[4]) for r in countries if r[0] != "IN"}
    foreign |= {(r[5], "capital", r[4]) for r in countries if r[0] != "IN" and r[5]}
    for r in _rows(_download("admin1CodesASCII.txt").decode("utf-8")):
        cc = r[0].split(".")[0]
        if cc != "IN" and cc in country_names:
            foreign.add((r[2], "region", country_names[cc]))
    with open(FOREIGN_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "kind", "country"])
        writer.writerows(sorted(foreign))
    print(f"Wrote {len(out)} Indian places to {INDIA_FILE} and {len(foreign)} foreign names to {FOREIGN_FILE}")


# ---------------------------------------------------------------- load

def load_india(path: Path = INDIA_FILE) -> list[Place]:
    with open(path, encoding="utf-8") as f:
        return [Place(r["geonameid"], r["name"], r["kind"], r["state"], r["district"] or None,
                      float(r["latitude"]), float(r["longitude"]), int(r["population"]),
                      [a for a in r["aliases"].split("|") if a])
                for r in csv.DictReader(f)]


def load_foreign(path: Path = FOREIGN_FILE) -> dict[str, str]:
    """normalised name -> country, for countries, capitals and first-level regions (outside India)."""
    with open(path, encoding="utf-8") as f:
        names = {normalise_name(r["name"]): r["country"] for r in csv.DictReader(f)}
    names.update({"us": "United States", "usa": "United States", "u s": "United States", "america": "United States",
                  "uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom",
                  "uae": "United Arab Emirates", "dhaka": "Bangladesh"})
    return names


if __name__ == "__main__":
    build()
