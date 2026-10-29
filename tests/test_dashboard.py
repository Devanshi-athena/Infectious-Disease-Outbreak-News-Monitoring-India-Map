from datetime import date, datetime

import pytest
from sqlalchemy import select

from outbreak_monitor.dashboard_data import (Filters, apply_filters, district_zoom_options, load_articles, options,
                                             save_reviews, story_markers, summary)
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


def test_story_markers_and_totals(factory):
    with factory() as s:
        df = apply_filters(load_articles(s), Filters(include_abroad=True))
        totals = summary(df, s)
    markers = story_markers(df)
    assert sorted(m.article_ids for m in markers) == [[1, 2], [3], [6]]          # 1+2 one story; 5 abroad: no marker
    assert (totals["articles"], totals["locations"]) == (5, 2)
    assert totals["last_refresh"].strftime("%H:%M %Z") == "15:00 IST"
    story = next(m for m in markers if m.article_ids == [1, 2])
    assert "Site A" in story.popup_html and "Site B" in story.popup_html and "Pune, Maharashtra" in story.popup_html
    assert 'href="https://b.in/2"' in story.popup_html


def test_popup_escapes_external_text(factory):
    with factory() as s:
        df = apply_filters(load_articles(s), Filters(view="all"))
    [marker] = [m for m in story_markers(df) if m.article_ids == [7]]
    assert "<script>" not in marker.popup_html and "&lt;script&gt;" in marker.popup_html


def test_district_zoom_options(factory):
    with factory() as s:
        df = apply_filters(load_articles(s), Filters())
    zoom = district_zoom_options(df)
    assert list(zoom) == ["Kozhikode, Kerala", "Pune, Maharashtra"] and zoom["Pune, Maharashtra"] == (18.5, 73.8)


def test_save_reviews(factory):
    with factory() as s:
        assert save_reviews(s, {1: "irrelevant", 3: "relevant", 6: "relevant"}) == 2   # 6 unchanged
        assert dict(s.execute(select(Article.article_id, Article.relevance_status)).all())[1] == "irrelevant"
        with pytest.raises(ValueError):
            save_reviews(s, {1: "maybe"})
        df = load_articles(s)
    assert 1 not in ids(apply_filters(df, Filters()))


def test_app_runs_without_errors(factory, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import outbreak_monitor.db as db
    monkeypatch.setattr(db, "_default_factory", factory)  # the app reads the test database
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception
    # matching articles, stories on the map (articles 1+2 are one story), distinct locations
    assert [m.value for m in at.metric][:3] == ["4", "3", "2"]
    state_filter = at.sidebar.multiselect[2]      # group, disease, state, source
    state_filter.set_value(["Kerala"]).run()
    assert not at.exception and at.metric[0].value == "1"
    at.sidebar.multiselect[2].set_value([])
    at.sidebar.multiselect[0].set_value(["Respiratory and vaccine-preventable"]).run()   # Measles is in this group
    assert not at.exception and at.metric[0].value == "1"
    at.sidebar.multiselect[0].set_value([])
    at.sidebar.radio[0].set_value("all").run()
    assert not at.exception and at.metric[0].value == "6"


def test_latest_reports_and_states(factory):
    from outbreak_monitor.dashboard_data import latest_reports, reports_by_state
    with factory() as s:
        df = apply_filters(load_articles(s), Filters())
    reports = latest_reports(df, limit=10)
    assert len(reports) == 3                      # articles 1 and 2 are one story
    assert reports[0]["published"].strftime("%Z") == "IST"
    assert reports_by_state(df) == [("Maharashtra", 2), ("Kerala", 1)]
