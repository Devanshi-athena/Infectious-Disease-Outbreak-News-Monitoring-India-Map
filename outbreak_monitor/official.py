"""Official outbreak reports: IDSP weekly outbreak reports published by NCDC.

Each weekly PDF lists every disease outbreak reported by states/UTs through the Integrated Disease
Surveillance Programme: unique ID, state, district, disease, cases, deaths, date of start, date of
reporting, status and action taken. These are official surveillance records (not news), published
roughly two months after the reporting week, and are kept in their own table (official_reports).

    python -m outbreak_monitor.official      # fetch the latest weekly reports now
"""

import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin

import pdfplumber
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process
from sqlalchemy import select

from .config import load_disease_config, load_official_sources
from .db import OfficialReport, get_session_factory, utcnow
from .fetch import allowed_by_robots, get
from .gazetteer import load_india, normalise_name
from .logs import setup_logging
from .matcher import KeywordMatcher

log = logging.getLogger(__name__)

REPORT_ID = re.compile(r"^[A-Z]{2}/[A-Z]{2,4}/\d{4}/\d{1,2}/\s*\d+")
WEEK_LINK = re.compile(r"/(\d{4})/week(\d+)_", re.I)
DISTRICT_MATCH_MIN = 85   # rapidfuzz ratio for spelling variants of district names
DISEASE_START = re.compile(r"(?i)^(suspected|acute|fever|food|viral|human|chickenpox|measles|dengue|malaria|cholera)$")
# Column order of the main outbreak table; used only if a table's header cannot be read.
FIELDS = ["report_id", "state", "district", "disease", "cases", "deaths", "onset", "reported", "status", "action"]
# Header text (joined over the header rows of a column) -> field, first match wins.
HEADER_WORDS = [("unique", "report_id"), ("state", "state"), ("district", "district"), ("illness", "disease"),
                ("disease", "disease"), ("death", "deaths"), ("case", "cases"), ("star", "onset"),  # "Date of Star"+"t"
                ("report", "reported"), ("status", "status"), ("comment", "action"), ("action", "action")]


@dataclass
class WeeklyReport:
    year: int
    week: int
    url: str


def list_weekly_reports(listing_url: str) -> list[WeeklyReport]:
    """All weekly report PDFs on the NCDC listing page, newest first."""
    if not allowed_by_robots(listing_url):
        raise PermissionError(f"robots.txt disallows {listing_url}")
    resp = get(listing_url)
    soup = BeautifulSoup(resp.content, "lxml")
    found = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(resp.url, a["href"])
        if url.lower().endswith(".pdf") and (m := WEEK_LINK.search(url)):
            found[(int(m[1]), int(m[2]))] = url
    return [WeeklyReport(y, w, u) for (y, w), u in sorted(found.items(), reverse=True)]


JOINING_WORDS = {"with", "and", "of", "in", "the", "due", "to", "from", "or", "by", "like", "at", "on", "for", "were",
                 "was", "is", "are", "after", "among", "under", "all", "a", "an"}


def _join(text: str, more: str) -> str:
    """Append a wrapped fragment: 'Gastroente' + 'ritis' -> 'Gastroenteritis', 'Fever' + 'with Rash' ->
    'Fever with Rash'. The table cells are narrow, so words are often broken without a hyphen."""
    if not text:
        return more
    first, last = more.split(" ", 1)[0], text.rsplit(" ", 1)[-1]
    if (text[-1:].isalpha() and text[-1:].islower() and first[:1].islower()
            and first.lower() not in JOINING_WORDS and last.lower() not in JOINING_WORDS):
        return text + more
    return f"{text} {more}"


def _lines(cell: str | None) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in (cell or "").splitlines() if line.strip()]


def _nearest(columns: dict[int, str], index: int) -> str | None:
    """Field of the column closest to a cell's position."""
    return columns[min(columns, key=lambda i: (abs(i - index), i))] if columns else None


def _header_columns(table: list[list]) -> dict[int, str]:
    """Column index -> field, from the header rows above the first record. Header words are split over
    several rows and sometimes over neighbouring cells ("N" / "o. of Case" / "s"), so the text of each
    column is joined first; a field is taken by the first column that names it."""
    text: dict[int, str] = {}
    for row in table:
        cells = [(i, " ".join(_lines(c))) for i, c in enumerate(row) if _lines(c)]
        if cells and REPORT_ID.match(cells[0][1]):
            break
        for i, t in cells:
            text[i] = f"{text.get(i, '')} {t}".lower()
    # Glue split words: a cell that is a fragment ("n", "s") belongs to its neighbour.
    joined = {i: t for i, t in text.items()}
    for i in sorted(text):
        if len(text[i].strip()) <= 2 and (i + 1) in text:
            joined[i + 1] = text[i] + text[i + 1]
            joined.pop(i, None)
    columns: dict[int, str] = {}
    for i in sorted(joined):
        squashed = joined[i].replace(" ", "")
        for word, name in HEADER_WORDS:
            if word in squashed and name not in columns.values():
                columns[i] = name
                break
    return columns if {"report_id", "state", "district", "disease"} <= set(columns.values()) else {}


def _number(text: str) -> int | None:
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def _date(text: str, default_year: int | None = None) -> date | None:
    """'30-07-2026', '3/8/2026' -> date. A date whose year was cut off by a page break ('15-07-')
    takes default_year (the year in the report ID)."""
    m = re.search(r"(\d{1,2})[-./](\d{1,2})(?:[-./](\d{2,4}))?", text or "")
    if not m or (m[3] is None and default_year is None):
        return None
    day, month, year = int(m[1]), int(m[2]), int(m[3]) if m[3] else default_year
    try:
        return date(year + 2000 if year < 100 else year, month, day)
    except ValueError:
        return None


def parse_report(pdf_bytes: bytes) -> list[dict]:
    """Outbreak records from one weekly report.

    A record starts with a row holding its unique ID; that row's cells, in table order, are the
    fields (ID, state, district, disease, cases, deaths, start, reported, status, action) and their
    positions define the record's columns. Following rows without an ID continue the record (a
    disease name or the action text wrapping onto further lines) and are joined column by column.
    """
    records: list[dict[str, list[str]]] = []
    order = FIELDS  # field order of the last header seen; a table continuing on a new page has no header
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                header = _header_columns(table)
                if header:
                    order = [name for _, name in sorted(header.items())]
                else:
                    # Positions differ from page to page: take them from this table's fullest record row.
                    id_rows = [[i for i, c in enumerate(row) if _lines(c)] for row in table
                               if any(_lines(c) for c in row)
                               and REPORT_ID.match(" ".join(_lines(next(c for c in row if _lines(c)))))]
                    header = dict(zip(max(id_rows, key=len), order)) if id_rows else {}
                current = None
                for row in table:
                    cells = [(i, _lines(c)) for i, c in enumerate(row)]
                    cells = [(i, lines) for i, lines in cells if lines]
                    if not cells:
                        continue
                    if REPORT_ID.match(" ".join(cells[0][1])):
                        columns = header or {i: name for (i, _), name in zip(cells, order)}
                        current = {"report_id": cells[0][1]}
                        cells = cells[1:]
                    elif current is None:
                        continue  # header rows
                    for index, lines in cells:
                        name = _nearest(columns, index)
                        if name and name != "report_id":
                            current.setdefault(name, []).extend(lines)
                    if current not in records:
                        records.append(current)
    cleaned: dict[str, dict] = {}
    for record in map(_clean, records):
        filled = sum(v is not None for v in record.values())
        if filled > 1 and filled > sum(v is not None for v in cleaned.get(record["report_id"], {}).values()):
            cleaned[record["report_id"]] = record  # an ID repeated by a page break keeps its fuller record
    return list(cleaned.values())


def _rejoin(text: str) -> str:
    """Re-join a word whose last letters wrapped onto the next line ("Murshidaba d", "Gastroenteriti s")."""
    return re.sub(r"(?<=[a-z]{3}) (?=(?!(?:of|in|on|at|by|to|or|an)\b)[a-z]{1,2}\b)", "", text)


def _clean(r: dict[str, list[str]]) -> dict:
    """Field fragments -> values. The narrow disease and status columns break words without a hyphen
    ("Gastroente" / "ritis"), so their fragments are re-joined; other columns wrap between words."""
    text = lambda name: re.sub(r"-\s+(?=\d)", "-", " ".join(r.get(name, [])).strip())  # "30-07- 2026" -> "30-07-2026"
    disease = ""
    for fragment in r.get("disease", []):
        disease = _join(disease, fragment)
    # Dates and status are read by content, since a narrow layout can push them into a neighbouring
    # column: the first date is the start of the outbreak, the second the date of reporting.
    dated = " ".join(text(name) for name in ("deaths", "onset", "reported", "status"))
    id_year = re.search(r"/(\d{4})/", text("report_id"))
    dates = [d for d in (_date(m.group(0), int(id_year[1]) if id_year else None)
                         for m in re.finditer(r"\d{1,2}[-./]\d{1,2}(?:[-./]\d{2,4})?", dated)) if d]
    action = text("action")
    leading = re.match(r"(?i)under\s*(surveillance|control)\b\s*", action)  # status pushed into the comment column
    if leading:
        action = action[leading.end():]
    elif interleaved := re.match(r"(?i)under\s+((?:\S+\s+){0,12}?)(surveillance|control)\b\s*", action):
        # Status lines interleaved with the comment's first lines: "Under Cases of loose Surveillance motion..."
        leading = interleaved
        action = interleaved[1] + action[interleaved.end():]
    status_text = (dated + (leading.groups()[-1] if leading else "")).lower().replace(" ", "")
    status = ("Under Surveillance" if "surveil" in status_text else "Under Control" if "control" in status_text
              else None)
    return {"report_id": re.sub(r"\s+", "", text("report_id")), "state": _rejoin(text("state")) or None,
            "district": _rejoin(text("district")) or None,
            "disease_raw": _rejoin(re.sub(r"\s*-\s*", "-", disease).strip()) or None,
            "cases": _number(text("cases")), "deaths": _number(text("deaths")),
            "onset_date": dates[0] if dates else None, "reported_date": dates[1] if len(dates) > 1 else None,
            "status": status, "action_taken": action or None}


class DistrictLocator:
    """Coordinates for an official report's state and district, from the reference place data."""

    def __init__(self):
        places = load_india()
        self.states = {normalise_name(p.name): p for p in places if p.kind == "state"}
        self.districts: dict[str, dict[str, object]] = {}
        for p in places:
            if p.kind == "district":
                self.districts.setdefault(p.state, {})[normalise_name(p.name)] = p
                for alias in p.aliases:
                    self.districts[p.state].setdefault(alias, p)

    def locate(self, state: str | None, district: str | None) -> dict:
        state_place = self._state(state) or self._state_of_district(district)
        if not state_place:
            return {"state": state, "district": district, "latitude": None, "longitude": None,
                    "location_level": None, "location_confidence": "none"}
        result = {"state": state_place.name, "district": None, "latitude": state_place.latitude,
                  "longitude": state_place.longitude, "location_level": "state", "location_confidence": "low"}
        names = self.districts.get(state_place.name, {})
        place, confidence = self._district(names, district)
        if place:
            result.update(district=place.name, latitude=place.latitude, longitude=place.longitude,
                          location_level="district", location_confidence=confidence)
        return result

    def repair(self, r: dict) -> dict:
        """Undo column shifts of the table layout, using what the values are: a state name in the
        disease column (state, district and disease moved one column right) or a disease appended
        to the district ("Khagaria Suspected Food Poisoning")."""
        r = dict(r)
        if not self._state(r["state"]) and self._state(r["disease_raw"]):
            r["state"], r["district"], r["disease_raw"] = r["disease_raw"], r["state"], r["district"]
        state = self._state(r["state"]) or self._state_of_district(r["district"])
        if state and r["district"] and not r["disease_raw"]:
            words = r["district"].split()
            names = self.districts.get(state.name, {})
            split = next((n for n in range(len(words) - 1, 0, -1) if self._district(names, " ".join(words[:n]))[0]),
                         None)
            if split is None:
                split = next((i for i, w in enumerate(words) if i and DISEASE_START.match(w)), None)
            if split:
                r["district"], r["disease_raw"] = " ".join(words[:split]), " ".join(words[split:])
        return r

    @staticmethod
    def _district(names: dict, district: str | None):
        """(place, confidence) of a district name within one state's districts, or (None, None)."""
        key = normalise_name(re.sub(r"\(.*?\)", "", district or ""))
        if not key:
            return None, None
        if key in names:
            return names[key], "high"
        # Spelling variants ("Balodabazar" vs "Baloda Bazar"): compare names without spaces, for the whole
        # name and for each part of a combined name ("Balodabazar -Bhatapara"). No partial matching:
        # a wrong district is worse than a state-level location.
        compact = {name.replace(" ", ""): name for name in names}
        parts = [key] + [normalise_name(p) for p in re.split(r"[-/&]| and ", district or "") if p.strip()]
        for part in dict.fromkeys(p.replace(" ", "") for p in parts if p):
            best = process.extractOne(part, list(compact), scorer=fuzz.ratio, score_cutoff=DISTRICT_MATCH_MIN)
            if best:
                return names[compact[best[0]]], "medium"
        return None, None

    def _state_of_district(self, district: str | None):
        """The state of a district name that exists in exactly one state (when the state cell is empty)."""
        key = normalise_name(re.sub(r"\(.*?\)", "", district or ""))
        owners = [state for state, names in self.districts.items() if key and key in names]
        return self.states.get(normalise_name(owners[0])) if len(owners) == 1 else None

    def _state(self, state: str | None):
        key = normalise_name(state or "")
        if key in self.states:
            return self.states[key]
        best = process.extractOne(key, list(self.states), scorer=fuzz.ratio, score_cutoff=80) if key else None
        return self.states[best[0]] if best else None


def collect_official(session_factory=None) -> dict:
    """Fetch the configured official reports that are not stored yet. Returns statistics."""
    session_factory = session_factory or get_session_factory()
    matcher = KeywordMatcher(load_disease_config().diseases)
    locator = DistrictLocator()
    stats = {"reports_read": 0, "records_new": 0, "records_updated": 0, "errors": []}
    for source in (s for s in load_official_sources() if s.enabled):
        try:
            weekly = list_weekly_reports(source.listing)[: source.weeks]
        except Exception as exc:
            stats["errors"].append({"source": source.name, "url": source.listing, "error": f"{type(exc).__name__}: {exc}"})
            log.warning("FAILED %s listing: %s", source.name, exc)
            continue
        with session_factory() as session:
            done = set(session.scalars(select(OfficialReport.report_url).distinct()))
            for report in weekly:
                if report.url in done:
                    continue
                try:
                    records = parse_report(get(report.url).content)
                except Exception as exc:
                    stats["errors"].append({"source": source.name, "url": report.url,
                                            "error": f"{type(exc).__name__}: {exc}"[:300]})
                    log.warning("FAILED %s week %s/%s: %s", source.name, report.week, report.year, exc)
                    continue
                stats["reports_read"] += 1
                for r in map(locator.repair, records):
                    # Only the report's own disease column: the comment also names diseases that were ruled out.
                    diseases = matcher.match(r["disease_raw"])
                    row = session.get(OfficialReport, r["report_id"])
                    if row is None:
                        row = OfficialReport(report_id=r["report_id"])
                        session.add(row)
                        stats["records_new"] += 1
                    else:
                        stats["records_updated"] += 1  # follow-up of an outbreak reported earlier
                    for key, value in {**r, **locator.locate(r["state"], r["district"])}.items():
                        if key != "report_id":
                            setattr(row, key, value)
                    row.diseases = diseases
                    row.source_name, row.report_url = source.name, report.url
                    row.report_year, row.report_week = report.year, report.week
                    row.collected_at = utcnow()
                session.commit()
                log.info("%s week %d/%d: %d outbreak records", source.name, report.week, report.year, len(records))
        with session_factory() as session:  # diseases.yaml may have changed: map every stored record again
            for row in session.scalars(select(OfficialReport)):
                row.diseases = matcher.match(row.disease_raw)
            session.commit()
    return stats


def main():
    setup_logging()
    log.info("Official reports: %s", collect_official())


if __name__ == "__main__":
    main()
