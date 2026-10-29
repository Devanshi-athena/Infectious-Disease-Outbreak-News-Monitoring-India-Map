from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from outbreak_monitor import official
from outbreak_monitor.config import OfficialSource
from outbreak_monitor.dashboard_data import filter_official, load_official, official_popup_html
from outbreak_monitor.db import OfficialReport, get_session_factory
from outbreak_monitor.official import DistrictLocator, _clean, _date, _join, _rejoin, list_weekly_reports

LISTING = "https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php"


@pytest.fixture(scope="module")
def locator():
    return DistrictLocator()


def record(**kw):
    base = dict(report_id="KL/WAY/2026/33/1367", state="Kerala", district="Wayanad",
                disease_raw="Acute Diarrhoeal Disease", cases=17, deaths=0, onset_date=date(2026, 8, 6),
                reported_date=date(2026, 8, 7), status="Under Surveillance", action_taken="Cases of loose motion.")
    return {**base, **kw}


def test_listing_finds_weekly_pdfs_newest_first(monkeypatch):
    page = (b'<a href="../WriteReadData/l892s/2025/week45_2025.pdf">45</a>'
            b'<a href="/WriteReadData/l892s/2026/week34_2026.pdf">34</a>'
            b'<a href="/WriteReadData/l892s/2026/week3_2026.pdf">3</a><a href="/other.pdf">x</a>')
    monkeypatch.setattr(official, "allowed_by_robots", lambda url: True)
    monkeypatch.setattr(official, "get", lambda url: SimpleNamespace(content=page, url=LISTING))
    reports = list_weekly_reports(LISTING)
    assert [(r.year, r.week) for r in reports] == [(2026, 34), (2026, 3), (2025, 45)]
    assert reports[0].url == "https://ncdc.mohfw.gov.in/WriteReadData/l892s/2026/week34_2026.pdf"


def test_wrapped_words_are_rejoined():
    assert _join("Gastroente", "ritis") == "Gastroenteritis"
    assert _join("Fever", "with Rash") == "Fever with Rash"
    assert _join("Fever of", "unknown origin") == "Fever of unknown origin"
    assert [_rejoin(t) for t in ("Chikunguny a", "Murshidaba d", "Fever of unknown", "Hepatitis A")] == \
        ["Chikungunya", "Murshidabad", "Fever of unknown", "Hepatitis A"]


def test_dates_and_status_are_read_by_content():
    assert _date("30-07-2026") == date(2026, 7, 30) and _date("3/8/2026") == date(2026, 8, 3)
    assert _date("15-07-", default_year=2026) == date(2026, 7, 15)      # year cut off by a page break
    assert _date("15-07-") is None
    # Onset date pushed into the deaths column, status pushed into the comment column.
    r = _clean({"report_id": ["KL/WAY/2026/33/1367"], "state": ["Kerala"], "district": ["Wayanad"],
                "disease": ["Acute Diarrhoeal", "Disease"], "cases": ["17"], "deaths": ["0", "6/8/2026"],
                "action": ["Under Surveillance Cases of loose", "motion were reported."]})
    assert (r["deaths"], r["onset_date"], r["status"]) == (0, date(2026, 8, 6), "Under Surveillance")
    assert r["action_taken"] == "Cases of loose motion were reported."
    assert _clean({"report_id": ["X/Y/2026/1/1"], "status": ["Under"]})["status"] is None   # cut off: not guessed
    # Status lines interleaved with the comment's first lines
    r = _clean({"report_id": ["AS/SON/2026/29/1148"], "action": ["Under Cases of loose motions were reported",
                                                                  "control from Village No. 2."]})
    assert (r["status"], r["action_taken"]) == ("Under Control", "Cases of loose motions were reported from Village No. 2.")


def test_shifted_columns_are_repaired(locator):
    # State, district and disease one column to the right
    r = locator.repair(record(state="Chandauli", district="Leptospirosis", disease_raw="Uttar Pradesh"))
    assert (r["state"], r["district"], r["disease_raw"]) == ("Uttar Pradesh", "Chandauli", "Leptospirosis")
    # Disease appended to the district
    r = locator.repair(record(state="Bihar", district="Khagaria Suspected Food Poisoning", disease_raw=None))
    assert (r["district"], r["disease_raw"]) == ("Khagaria", "Suspected Food Poisoning")
    assert locator.repair(record()) == record()


def test_district_location(locator):
    loc = locator.locate("Kerala", "Wayanad")
    assert (loc["state"], loc["district"], loc["location_level"], loc["location_confidence"]) == \
        ("Kerala", "Wayanad", "district", "high")
    assert locator.locate("Chhattisgarh", "Balodabazar")["district"] != "Balod"   # no partial matching
    assert locator.locate(None, "Wayanad")["state"] == "Kerala"                  # state from a unique district
    unknown = locator.locate("Karnataka", "Nowhere Nagar")
    assert (unknown["location_level"], unknown["district"]) == ("state", None)
    assert locator.locate("Atlantis", "Nowhere")["location_level"] is None


def test_collect_official_stores_and_skips_known_reports(monkeypatch):
    factory = get_session_factory("sqlite://")
    source = OfficialSource(name="IDSP", type="idsp_weekly", listing=LISTING, weeks=1)
    monkeypatch.setattr(official, "load_official_sources", lambda: [source])
    monkeypatch.setattr(official, "list_weekly_reports", lambda url: [
        official.WeeklyReport(2026, 34, "https://x/week34.pdf"), official.WeeklyReport(2026, 33, "https://x/week33.pdf")])
    monkeypatch.setattr(official, "get", MagicMock(return_value=SimpleNamespace(content=b"pdf")))
    monkeypatch.setattr(official, "parse_report", lambda content: [
        record(), record(report_id="BR/KHA/2026/34/1366", state="Bihar",
                         district="Khagaria Suspected Food Poisoning", disease_raw=None,
                         action_taken="Dengue was ruled out.")])
    stats = official.collect_official(factory)
    assert (stats["reports_read"], stats["records_new"], stats["errors"]) == (1, 2, [])  # weeks=1: newest only
    with factory() as s:
        rows = {r.report_id: r for r in s.scalars(select(OfficialReport))}
    assert rows["KL/WAY/2026/33/1367"].diseases == ["Acute Diarrhoeal Disease"]
    assert rows["KL/WAY/2026/33/1367"].location_level == "district" and rows["KL/WAY/2026/33/1367"].latitude
    kha = rows["BR/KHA/2026/34/1366"]
    assert (kha.district, kha.disease_raw, kha.report_week) == ("Khagaria", "Suspected Food Poisoning", 34)
    assert "Dengue" not in kha.diseases               # diseases come from the disease column, not the comment
    assert official.collect_official(factory)["reports_read"] == 0   # already stored


def test_dashboard_official_layer():
    factory = get_session_factory("sqlite://")
    with factory() as s:
        s.add_all([
            OfficialReport(report_id="A/1", source_name="IDSP", report_url="https://x/w34.pdf", report_year=2026,
                           report_week=34, state="Kerala", district="Wayanad", disease_raw="Dengue <b>",
                           diseases=["Dengue"], cases=12, deaths=0, latitude=11.6, longitude=76.1),
            OfficialReport(report_id="A/2", source_name="IDSP", report_url="https://x/w33.pdf", report_year=2026,
                           report_week=33, state="Assam", district="Barpeta", disease_raw="Chickenpox",
                           diseases=[], cases=8, deaths=0)])
        s.commit()
        df = load_official(s)
    assert list(df["report_id"]) == ["A/1", "A/2"]                     # newest week first
    assert list(filter_official(df, ["Dengue"], [])["report_id"]) == ["A/1"]
    assert list(filter_official(df, [], ["Assam"])["report_id"]) == ["A/2"]
    assert len(filter_official(df, [], [])) == 2                      # unmapped diseases shown without a filter
    popup = official_popup_html(next(df.itertuples()))
    assert "Dengue &lt;b&gt;" in popup and "Week 34, 2026" in popup and "https://x/w34.pdf" in popup
