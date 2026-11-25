import pandas as pd
from datetime import date, datetime

import pytest
from sqlalchemy import select

from outbreak_monitor.dashboard_data import (Filters, apply_filters, district_zoom_options, load_articles, options,
                                             save_reviews, summary)
from outbreak_monitor.db import Article, CollectionRun, get_session_factory


def article(i, **kw):
    base = dict(article_id=i, headline=f"Dengue cases rise {i}", source_name="Site A", article_url=f"https://a.in/{i}",
                link_url=f"https://a.in/{i}", collection_method="rss", published_at=datetime(2026, 10, 20, 6, 0),
                disease_tags=["Dengue"], relevant_diseases=["Dengue"], relevance="relevant",
                content_type="outbreak_report", event_status="new", relevance_status="pending", country="India",
                state="Maharashtra", district="Pune", latitude=18.5, longitude=73.8, location_level="district",
                location_confidence="high", location_review=False, story_id=i)
    return Article(**{**base, **kw})


@pytest.fixture
def factory(tmp_path):
    factory = get_session_factory(f"sqlite:///{tmp_path / 'test.db'}")  # a file: the app runs in another thread
    with factory() as s:
        s.add_all([
            article(1),
            article(2, source_name="Site B", article_url="https://b.in/2", story_id=1),       # same story as 1
            article(3, state="Kerala", district="Kozhikode", latitude=11.25, longitude=75.78, disease_tags=["Measles"],
                    relevant_diseases=["Measles"], published_at=datetime(2026, 10, 1), location_review=True),
            article(4, relevance="irrelevant", content_type="research", event_status=None),
            article(5, country="United States", state=None, district=None, latitude=None, longitude=None),
            article(6, relevance="irrelevant", relevance_status="relevant"),                 # reviewer says relevant
            article(7, headline="<script>alert(1)</script> Dengue", relevance_status="irrelevant"),  # reviewer says no
        ])
        s.add(CollectionRun(status="success", started_at=datetime(2026, 10, 26, 9, 0), finished_at=datetime(2026, 10, 26, 9, 30)))
        s.commit()
    return factory


def ids(df):
    return sorted(int(i) for i in df["article_id"])


def test_views_and_review_override(factory):
    with factory() as s:
        df = load_articles(s)
    assert ids(apply_filters(df, Filters())) == [1, 2, 3, 6]               # 4: irrelevant, 5: abroad, 7: reviewer said no
    assert ids(apply_filters(df, Filters(include_abroad=True))) == [1, 2, 3, 5, 6]
    assert ids(apply_filters(df, Filters(view="review"))) == [3]
    assert ids(apply_filters(df, Filters(view="all"))) == [1, 2, 3, 4, 6, 7]


def test_india_boundary_and_mask():
    from outbreak_monitor.gazetteer import _simplify, india_mask, load_boundary
    boundary = load_boundary()
    polygons = boundary["features"][0]["geometry"]["coordinates"]
    points = [p for poly in polygons for p in poly[0]]
    lats, lons = [p[1] for p in points], [p[0] for p in points]
    # official boundary: from Indira Point (~6.75 N) to the northern tip (~37.1 N), incl. the islands
    assert min(lats) < 7 and max(lats) > 37 and min(lons) < 68.5 and max(lons) > 97
    mask = india_mask(boundary)["geometry"]["coordinates"]
    assert len(mask) == 1 + len(polygons)                     # the world with every part of India cut out
    assert _simplify([[0, 0], [1, 0.001], [2, 0]], 0.01) == [[0, 0], [2, 0]]


def test_place_label_handles_missing_values():
    import pandas as pd
    from outbreak_monitor.dashboard_data import place_label
    row = lambda **kw: pd.Series({"country": float("nan"), "state": float("nan"), "district": float("nan"),
                                  "locality": float("nan"), **kw})
    assert place_label(row()) == "Not determined"
    assert place_label(row(country="United States")) == "United States (outside India)"
    assert place_label(row(country="India", state="Kerala")) == "Kerala"
    assert place_label(row(country="India", state="Delhi", district="New Delhi", locality="New Delhi")) == \
        "New Delhi, Delhi"


def test_filters(factory):
    with factory() as s:
        df = load_articles(s)
    assert options(df)["diseases"] == ["Dengue", "Measles"]
    assert ids(apply_filters(df, Filters(diseases=["Measles"]))) == [3]
    assert ids(apply_filters(df, Filters(states=["Maharashtra"]))) == [1, 2, 6]
    assert ids(apply_filters(df, Filters(sources=["Site B"]))) == [2]
    assert ids(apply_filters(df, Filters(start=date(2026, 10, 10), end=date(2026, 10, 31)))) == [1, 2, 6]


def test_map_items_and_totals(factory):
    from outbreak_monitor.map_view import news_items
    with factory() as s:
        df = apply_filters(load_articles(s), Filters(include_abroad=True))
        totals = summary(df, s)
    items = news_items(df)
    assert len(items) == 3                                    # articles 1+2 are one story; 5 is abroad: not mapped
    assert (totals["articles"], totals["locations"]) == (5, 2)
    assert totals["last_refresh"].strftime("%H:%M %Z") == "15:00 IST"
    card = next(i["c"] for i in items if "Site B" in i["c"])
    assert "Site A" in card and "Pune, Maharashtra" in card and 'href="https://b.in/2"' in card


def test_map_cards_escape_external_text(factory):
    from outbreak_monitor.map_view import news_items
    with factory() as s:
        df = apply_filters(load_articles(s), Filters(view="all"))
    cards = " ".join(i["c"] + i["h"] + i["p"] for i in news_items(df))
    assert "<script>" not in cards and "&lt;script&gt;" in cards


def test_district_zoom_options(factory):
    with factory() as s:
        df = apply_filters(load_articles(s), Filters())
    zoom = district_zoom_options(df)
    assert list(zoom) == ["Kozhikode, Kerala", "Pune, Maharashtra"] and zoom["Pune, Maharashtra"][:2] == (18.5, 73.8)


def test_save_reviews(factory):
    with factory() as s:
        assert save_reviews(s, {1: "irrelevant", 3: "relevant", 6: "relevant"}) == 2   # 6 unchanged
        assert dict(s.execute(select(Article.article_id, Article.relevance_status)).all())[1] == "irrelevant"
        with pytest.raises(ValueError):
            save_reviews(s, {1: "maybe"})
        df = load_articles(s)
    assert 1 not in ids(apply_filters(df, Filters()))


def kpi_values(at) -> list[str]:
    """Figures shown in the key-figure cards, in order."""
    import re
    cards = next(m.value for m in at.markdown if 'class="kpis"' in m.value)
    return re.findall(r'<div class="value[^"]*">([^<]*)</div>', cards)


def test_app_runs_without_errors(factory, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import outbreak_monitor.db as db
    monkeypatch.setattr(db, "_default_factory", factory)  # the app reads the test database
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception
    # matching articles, stories on the map (articles 1+2 are one story), distinct locations
    assert kpi_values(at)[:3] == ["4", "3", "2"]
    state_filter = at.sidebar.multiselect[2]      # group, disease, state, source
    state_filter.set_value(["Kerala"]).run()
    assert not at.exception and kpi_values(at)[0] == "1"
    at.sidebar.multiselect[2].set_value([])
    at.sidebar.multiselect[0].set_value(["Vaccine-preventable"]).run()   # Measles is in this group
    assert not at.exception and kpi_values(at)[0] == "1"
    at.sidebar.multiselect[0].set_value([])
    at.sidebar.radio[0].set_value("all").run()
    assert not at.exception and kpi_values(at)[0] == "6"


def test_latest_reports_and_states(factory):
    from outbreak_monitor.dashboard_data import latest_reports, reports_by_state
    with factory() as s:
        df = apply_filters(load_articles(s), Filters())
    reports = latest_reports(df, limit=10)
    assert len(reports) == 3                      # articles 1 and 2 are one story
    assert reports[0]["published"].strftime("%Z") == "IST"
    assert reports_by_state(df) == [("Maharashtra", 2), ("Kerala", 1)]


def test_export_samples(factory, tmp_path):
    import csv, json
    from outbreak_monitor.export_samples import main
    counts = main(factory, tmp_path / "samples")
    assert counts["articles"] >= 1
    with open(tmp_path / "samples" / "articles_sample.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert "article_text" not in rows[0]                      # publishers' text is never exported
    assert json.loads(rows[0]["disease_tags"]) == ["Dengue"]  # JSON columns as JSON
    assert "CREATE TABLE official_reports" in (tmp_path / "samples" / "schema.sql").read_text(encoding="utf-8")


def test_state_disease_table_counts_each_story_once(factory):
    from outbreak_monitor.dashboard_data import state_disease_table
    with factory() as s:
        s.add(article(8, state="Kerala", district="Kozhikode", disease_tags=["Dengue", "Measles"],
                      relevant_diseases=["Dengue", "Measles"], story_id=8))   # one report naming two diseases
        s.commit()
        df = load_articles(s)
    table = state_disease_table(apply_filters(df, Filters(view="relevant")), ["Dengue", "Measles"])
    assert list(table.columns) == ["Total", "Dengue", "Measles"]     # most-reported disease first (3 vs 2)
    assert list(table.index) == ["Kerala", "Maharashtra"]              # most reports first (tie: alphabetical)
    # Maharashtra: articles 1 and 2 are one story, 6 is another; the US report (5) is not counted
    assert table.loc["Maharashtra"].tolist() == [2, 2, 0]
    # Kerala: report 3 (measles) and report 8 (dengue + measles): 2 reports in total, not 3
    assert table.loc["Kerala"].tolist() == [2, 1, 2]


def _reports(rows):
    """Minimal article rows: (story, disease, state, district, published date, language, type, status)."""
    import pandas as pd
    from datetime import datetime
    out = []
    for i, (story, disease, state, district, day, language, ctype, status) in enumerate(rows):
        out.append(dict(article_id=i, story_id=story, diseases=[disease], state=state, district=district,
                        country="India", effective_relevance="relevant", published_date=day,
                        published_at=datetime.combine(day, datetime.min.time()), language_name=language,
                        source_name=f"source {i}", headline=f"headline {i}", article_url=f"https://x/{i}",
                        content_type=ctype, event_status=status, state_lgd_code=9, district_lgd_code=133))
    return pd.DataFrame(out)


def test_reports_of_one_disease_place_and_week_are_one_outbreak():
    from datetime import date
    from outbreak_monitor.dashboard_data import outbreak_events
    ob = ("outbreak_report", "new")
    df = _reports([(1, "Dengue", "Uttar Pradesh", "Budaun", date(2026, 11, 16), "Hindi", *ob),
                   (1, "Dengue", "Uttar Pradesh", "Budaun", date(2026, 11, 17), "English", *ob),   # same story
                   (2, "Dengue", "Uttar Pradesh", "Budaun", date(2026, 11, 18), "English", *ob),
                   (3, "Dengue", "Uttar Pradesh", "Budaun", date(2026, 11, 9), "Hindi", *ob),      # week before
                   (4, "Malaria", "Uttar Pradesh", "Budaun", date(2026, 11, 16), "Hindi", *ob)])
    events = outbreak_events(df)
    assert len(events) == 3
    top = events.iloc[0]
    assert (top.disease, top.place, top.reports, top.languages) == ("Dengue", "Budaun, Uttar Pradesh", 2, "Hindi, English")


def test_alerts_rise_and_first_report():
    from datetime import date, timedelta
    from outbreak_monitor.dashboard_data import outbreak_alerts
    ob, today = ("outbreak_report", "new"), date(2026, 11, 18)
    rows = [(s, "Dengue", "Kerala", "Malappuram", today - timedelta(days=s), "English", *ob) for s in range(4)]
    rows += [(10, "Dengue", "Kerala", "Malappuram", today - timedelta(days=20), "English", *ob)]   # 0.25 a week before
    rows += [(20, "Cholera", "Bihar", "Patna", today, "Hindi", *ob)]                                 # first in 8 weeks
    rows += [(30, "Malaria", "Bihar", "Gaya", today, "Hindi", *ob),
             (31, "Malaria", "Bihar", "Gaya", today - timedelta(days=30), "Hindi", *ob)]             # seen before
    rows += [(40 + i, "Ebola Virus Disease", "Jharkhand", None, today, "Hindi", "public_health_response", "unclear")
             for i in range(3)]                                                                       # preparedness news
    alerts = outbreak_alerts(_reports(rows))
    got = {(a.alert, a.disease, a.place, a.reports) for a in alerts.itertuples()}
    assert got == {("Rise", "Dengue", "Malappuram, Kerala", 4), ("First report", "Cholera", "Patna, Bihar", 1)}


def test_states_without_reports():
    import pandas as pd
    from outbreak_monitor.dashboard_data import states_without_reports
    df = pd.DataFrame({"effective_relevance": ["relevant", "irrelevant"], "country": ["India", "India"],
                       "state_lgd_code": [27, 17]})
    states = [{"state_code": "27", "state": "Maharashtra"}, {"state_code": "17", "state": "Meghalaya"},
              {"state_code": "37", "state": "Ladakh"}]
    assert states_without_reports(df, states) == ["Ladakh", "Meghalaya"]


def test_district_boundaries_cover_lgd_and_decode():
    from outbreak_monitor.lgd import load as load_lgd
    from outbreak_monitor.map_view import _decoded, district_at, state_bounds, topology
    topo = topology()
    codes = {d["dc"] for d in topo["districts"]}
    lgd = {int(r["district_code"]) for r in load_lgd("districts")}
    assert len(lgd - codes) <= 20                       # only districts created after the 2023 boundaries
    assert all(d["name"] and d["state"] for d in topo["districts"])
    assert all(ord(ch) < 128 for d in topo["districts"] for ch in d["name"] + d["state"])   # English names only
    assert {"Muzaffarabad", "Mirpur"} <= {d["name"] for d in topo["districts"] if d["state"] == "Jammu and Kashmir"}
    assert set(topo["lines"]) == {"district", "state", "india"}
    assert len(_decoded()) == len(topo["districts"])
    assert district_at(77.209, 28.614) in {d["dc"] for d in topo["districts"] if d["state"] == "Delhi"}   # New Delhi
    assert district_at(72.87, 19.07) is not None                                                         # Mumbai
    assert district_at(60.0, 20.0) is None                                                               # Arabian Sea
    (s, w), (n, e) = state_bounds()["Kerala"]
    assert 8 < s < 9 and 12 < n < 13 and 74 < w < 75.5 and 77 < e < 78


def test_every_island_is_kept_and_small_islands_have_an_outline():
    from outbreak_monitor.map_view import island_halos, topology
    parts = {d["name"]: len(d["polygons"]) for d in topology()["districts"]}
    # as many islands as the source file: none lost to simplification
    assert (parts["Lakshadweep"], parts["Nicobars"], parts["South Andamans"]) == (11, 19, 34)
    lakshadweep = next(d["dc"] for d in topology()["districts"] if d["name"] == "Lakshadweep")
    assert list(island_halos()) == [lakshadweep]                      # only a group too small to see gets one
    lats, lons = zip(*island_halos()[lakshadweep])
    assert 7.5 < min(lats) and max(lats) < 12.5 and 71.5 < min(lons) and max(lons) < 74.5


def test_district_counts_count_stories_once():
    import pandas as pd
    from outbreak_monitor.map_view import district_counts, news_items, shade
    df = pd.DataFrame({"country": ["India"] * 4, "story_id": [1, 1, 2, 3],
                       "district_lgd_code": [102.0, 102.0, 102.0, None],
                       "latitude": [26.9, 26.9, 26.9, 27.0], "longitude": [75.8, 75.8, 75.8, 75.0],
                       "diseases": [["Dengue"], ["Dengue"], ["Dengue", "Malaria"], ["Dengue"]],
                       "published_at": pd.to_datetime(["2026-11-20"] * 4), "event_status": ["new"] * 4,
                       "headline": ["a", "a", "b", "c"], "headline_original": [None] * 4,
                       "language_name": ["English"] * 4,
                       "article_url": ["https://x.in/1", "https://y.in/1", "https://x.in/2", "https://x.in/3"],
                       "source_name": ["X", "Y", "X", "X"], "place": ["Jaipur"] * 3 + ["Rajasthan"],
                       "state": ["Rajasthan"] * 4})
    items = news_items(df)
    assert len(items) == 3 and {i["dc"] for i in items} == {102, None}   # story 3 names only the state
    counts = district_counts(items)
    assert counts == {102: {"n": 2, "top": "Dengue 2, Malaria 1"}}
    assert shade(0) is None and shade(1) != shade(2) and shade(16) == shade(100)


def test_map_renders_without_foreign_tiles():
    import pandas as pd
    from outbreak_monitor.gazetteer import india_mask, load_boundary
    from outbreak_monitor.map_view import alert_districts, build_map
    alerts = alert_districts(pd.DataFrame([{"alert": "Rise", "disease": "Dengue", "reports": 5, "district_lgd_code": 102},
                                           {"alert": "First report", "disease": "Malaria", "reports": 1,
                                            "district_lgd_code": 102}]))
    assert alerts[102]["rise"] and len(alerts[102]["list"]) == 2
    item = {"la": 26.9, "lo": 75.8, "dc": 102, "st": "Rajasthan", "s": "new", "p": "Jaipur", "h": "x", "c": "<b>x</b>",
            "t": "2026-11-20"}
    for mode in ("news", "official", "both"):
        page = build_map([item], [], {102: {"n": 1, "top": ""}}, {}, alerts, mode,
                         india_mask(load_boundary())).get_root().render()
        assert "openstreetmap" not in page.lower() and "World_Shaded_Relief" in page
        assert '"maxZoom": 16' in page
        # the card is escaped so that data cannot close the script element; official mode carries no news items
        assert (r'<b>x<\/b>' in page) == (mode != "official")


def test_card_links_are_http_only_and_state_names_read_naturally():
    from outbreak_monitor.dashboard_data import href, states_without_reports
    assert href("https://a.in/x?a=1&b=2") == "https://a.in/x?a=1&amp;b=2"
    assert href("javascript:alert(1)") == "#" and href(None) == "#"
    states = [{"state_code": "38", "state": "The Dadra And Nagar Haveli And Daman And Diu"},
              {"state_code": "1", "state": "Jammu And Kashmir"}]
    assert states_without_reports(pd.DataFrame(), states) == ["Dadra and Nagar Haveli and Daman and Diu",
                                                               "Jammu and Kashmir"]


def test_alert_in_a_new_district_is_placed_by_its_point():
    from outbreak_monitor.map_view import alert_districts, district_at
    alerts = pd.DataFrame([{"alert": "Rise", "disease": "Dengue", "reports": 4, "district_lgd_code": 791,   # Polavaram
                            "latitude": 17.25, "longitude": 81.64}])
    assert list(alert_districts(alerts)) == [district_at(81.64, 17.25)]
