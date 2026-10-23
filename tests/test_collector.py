from unittest.mock import patch

from sqlalchemy import select

from outbreak_monitor import collector
from outbreak_monitor.config import Disease, DiseaseConfig, Settings, Source
from outbreak_monitor.db import Article, CollectionRun, SeenLink, get_session_factory, last_successful_run, utcnow
from outbreak_monitor.discovery import Discovery
from outbreak_monitor.matcher import KeywordMatcher

DISEASES = [Disease("Dengue", ["dengue"]), Disease("Hepatitis", ["hepatitis A", "jaundice outbreak"])]
CFG = DiseaseConfig(DISEASES, Settings(lookback_days=30))
SOURCES = [Source("Site", domain="site.in")]


def text(n: int) -> str:
    return f"Health officials reported {n} new dengue cases in the district this week. " * 20


def cand(url, method="rss", headline="Dengue cases rise", published=None, **kw):
    return {"url": url, "headline": headline, "published_at": published or utcnow(), "excerpt": None,
            "meta_keywords": None, "method": method, "listing_url": "https://site.in/listing", **kw}


def test_archive_entry_matched_on_slug_takes_title_from_page():
    candidates = [cand("https://site.in/city/jaundice-outbreak-in-village-77", method="archive",
                       headline=None, published=None) | {"published_at": None},
                  cand("https://site.in/city/budget-session-78", method="archive", headline=None) | {"published_at": None}]
    pages = {candidates[0]["url"]: ok(candidates[0]["url"], text(7), title="Jaundice outbreak hits village")}
    result, factory, fetched = run(candidates, pages)
    assert fetched == [candidates[0]["url"]] and result["new"] == 1 and result["no_keyword"] == 1
    with factory() as s:
        article = s.scalars(select(Article)).one()
        assert article.headline == "Jaundice outbreak hits village" and article.collection_method == "archive"


def ok(url, body, title=None):
    return {"extraction_status": "ok", "article_text": body, "canonical_url": url, "title": title}


def run(candidates, pages, factory=None, errors=(), modes=None):
    """collect() with fake discovery results and fake article pages {url: extract result}."""
    factory = factory or get_session_factory("sqlite://")
    fetched = []

    def fake_discover(source, diseases, *, backfill, cutoff, max_pages, known_urls):
        if modes is not None:
            modes.append(backfill)
        return Discovery(candidates=[dict(c) for c in candidates], listings_ok=1, errors=list(errors))

    def fake_extract(url):
        fetched.append(url)
        return pages.get(url, {"extraction_status": "failed", "error": "HTTP 403"})

    with patch.object(collector, "discover", fake_discover), patch.object(collector, "extract_article", fake_extract):
        return collector.collect(factory, SOURCES, CFG), factory, fetched


def test_matcher_whole_words_and_phrases():
    m = KeywordMatcher(DISEASES)
    assert m.match("DENGUE cases rise in Pune") == ["Dengue"]
    assert m.match("Headline", "Hepatitis-A and dengue reported") == ["Dengue", "Hepatitis"]
    assert m.match("Jaundice\noutbreak in village") == ["Hepatitis"]
    assert m.match("/city/pune/jaundice-outbreak-kills-one-123") == ["Hepatitis"]  # URL slugs
    assert m.match("Hepatitis alert", "anti-dengues") == []  # partial words don't match


def test_merge_candidates_combines_methods_and_metadata():
    merged = collector.merge_candidates([
        cand("https://site.in/a", method="keyword_page", headline=None, published=None) | {"published_at": None},
        cand("https://site.in/a", method="sitemap", headline="Dengue in Pune"),
    ])
    assert len(merged) == 1
    assert merged[0]["methods"] == ["keyword_page", "sitemap"] and merged[0]["headline"] == "Dengue in Pune"


def test_prefilter_and_store():
    candidates = [
        cand("https://site.in/a"),                                              # rss, keyword in headline
        cand("https://site.in/b", headline="Election results"),                 # rss, no keyword: not fetched
        cand("https://site.in/old", published=utcnow().replace(year=2000)),    # too old: not fetched
        cand("https://site.in/c", method="keyword_page", headline="Ward update"),  # keyword only in article text
        cand("https://site.in/d", method="keyword_page", headline="Budget news"),  # no keyword anywhere
        cand("https://site.in/e", method="sitemap", headline="Ward news", meta_keywords="Jaundice outbreak, Kerala"),
    ]
    pages = {
        "https://site.in/a": ok("https://site.in/a", text(1)),
        "https://site.in/c": ok("https://site.in/c", text(2), title="Ward 4 reports dengue cluster"),
        "https://site.in/d": ok("https://site.in/d", "Budget allocations for roads. " * 30),
    }
    modes = []
    first, factory, fetched = run(candidates, pages, modes=modes)
    assert sorted(fetched) == ["https://site.in/a", "https://site.in/c", "https://site.in/d", "https://site.in/e"]
    assert first["status"] == "success" and first["mode"] == "backfill"
    assert (first["new"], first["no_keyword"], first["too_old"], first["text_missing"]) == (3, 2, 1, 1)
    assert first["new_by_method"] == {"rss": 1, "keyword_page": 1, "sitemap": 1}

    with factory() as s:
        articles = {a.link_url: a for a in s.scalars(select(Article))}
        assert articles["https://site.in/c"].headline == "Ward 4 reports dengue cluster"  # title from page
        assert articles["https://site.in/c"].collection_method == "keyword_page"
        assert articles["https://site.in/e"].disease_tags == ["Hepatitis"]
        assert articles["https://site.in/e"].extraction_status == "failed"  # stored with metadata only
        assert s.get(SeenLink, "https://site.in/d").outcome == "no_keyword"

    # Second run: incremental (backfill done), nothing downloaded again, nothing new stored.
    second, _, fetched_again = run(candidates, pages, factory, modes=modes)
    assert second["mode"] == "incremental" and modes == [True, False]
    assert fetched_again == [] and second["new"] == 0 and second["already_known"] == 4


def test_dedup_by_canonical_url_and_fingerprint():
    body = text(3)
    candidates = [cand("https://site.in/story"), cand("https://site.in/amp/story"), cand("https://site.in/copy")]
    pages = {
        "https://site.in/story": ok("https://site.in/story", body),
        "https://site.in/amp/story": ok("https://site.in/story", body),     # same canonical URL
        "https://site.in/copy": ok("https://site.in/copy", body.upper()),   # same text, other URL
    }
    result, factory, _ = run(candidates, pages)
    assert result["new"] == 1 and result["duplicates"] == 2


def test_dedup_same_story_republished_under_another_section():
    candidates = [cand("https://site.in/city/pune/123", headline="Dengue: three Pune scientists in top 2% list"),
                  cand("https://site.in/education/456", headline="Dengue - Three Pune scientists in top 2% list!"),
                  cand("https://site.in/other/789", headline="Dengue cases rise in Pune")]
    pages = {c["url"]: ok(c["url"], text(n)) for n, c in enumerate(candidates)}
    result, _, _ = run(candidates, pages)
    assert result["new"] == 2 and result["duplicates"] == 1


def test_failed_listing_marks_run_partial_and_is_logged():
    errors = [{"source": "Site", "url": "https://site.in/rss", "error": "ConnectionError: down"}]
    result, factory, _ = run([cand("https://site.in/a")], {}, errors=errors)
    assert result["status"] == "partial" and result["new"] == 1
    with factory() as s:
        [r] = s.scalars(select(CollectionRun)).all()
        assert r.errors == errors and r.finished_at is not None
        assert last_successful_run(s).run_id == r.run_id
