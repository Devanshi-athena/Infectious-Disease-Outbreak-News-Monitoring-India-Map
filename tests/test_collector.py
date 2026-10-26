from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

from outbreak_monitor import collector
from outbreak_monitor.config import Disease, DiseaseConfig, Settings, Source
from outbreak_monitor.db import (Article, CollectionRun, ListingStatus, SeenLink, get_session_factory,
                                 last_successful_run, utcnow)
from outbreak_monitor.discovery import Discovery
from outbreak_monitor.matcher import KeywordMatcher

DISEASES = [Disease("Dengue", ["dengue"]), Disease("Hepatitis", ["hepatitis A", "jaundice outbreak"])]
CFG = DiseaseConfig(DISEASES, Settings(lookback_days=30))
SITE = Source("Site", domain="site.in")


def text(n: int) -> str:
    return f"Health officials reported {n} new dengue cases in the district this week. " * 20


def cand(url, method="rss", headline="Dengue cases rise", published=None, **kw):
    return {"url": url, "headline": headline, "published_at": published or utcnow(), "excerpt": None,
            "meta_keywords": None, "method": method, "listing_url": "https://site.in/listing", **kw}


def ok(url, body, title=None):
    return {"extraction_status": "ok", "article_text": body, "canonical_url": url, "title": title}


def run(candidates, pages, factory=None, listings=None, sources=(SITE,)):
    """collect() with fake discovery and fake article pages {url: extract result}.
    candidates: a list (every source) or {source name: list}."""
    factory = factory or get_session_factory("sqlite://")
    fetched = []

    def fake_discover(source, diseases, *, cutoff, deep, max_pages, known_urls):
        mine = candidates.get(source.name, []) if isinstance(candidates, dict) else candidates
        return Discovery(candidates=[dict(c) for c in mine], listings=list(listings or [
            {"source": source.name, "url": f"https://{source.domain}/rss", "kind": "rss", "deep": False,
             "ok": True, "error": None}]))

    def fake_extract(url):
        fetched.append(url)
        result = pages.get(url, {"extraction_status": "unavailable", "error": "HTTP 403"})
        return result.pop(0) if isinstance(result, list) else result

    with patch.object(collector, "discover", fake_discover), patch.object(collector, "extract_article", fake_extract):
        return collector.collect(factory, list(sources), CFG), factory, fetched


def test_matcher_whole_words_phrases_and_details():
    m = KeywordMatcher(DISEASES)
    assert m.match("DENGUE cases rise in Pune") == ["Dengue"]
    assert m.match("Headline", "Hepatitis-A and dengue reported") == ["Dengue", "Hepatitis"]
    assert m.match("Jaundice\noutbreak in village") == ["Hepatitis"]
    assert m.match("Hepatitis alert", "anti-dengues") == []  # partial words don't match
    assert m.matched_keywords(headline="Jaundice outbreak in Kochi", text="Two hepatitis A cases, no dengue") == {
        "Dengue": {"text": ["dengue"]},
        "Hepatitis": {"headline": ["jaundice outbreak"], "text": ["hepatitis A"]}}


def test_merge_candidates_combines_methods_and_metadata():
    merged = collector.merge_candidates([
        cand("https://site.in/a", method="keyword_page", headline=None) | {"published_at": None},
        cand("https://site.in/a", method="sitemap", headline="Dengue in Pune"),
    ])
    assert len(merged) == 1
    assert merged[0]["methods"] == ["keyword_page", "sitemap"] and merged[0]["headline"] == "Dengue in Pune"


def test_prefilter_and_store():
    candidates = [
        cand("https://site.in/a"),                                                   # rss, keyword in headline
        cand("https://site.in/b", headline="Election results"),                      # rss, no keyword: not fetched
        cand("https://site.in/old", published=utcnow().replace(year=2000)),         # too old: not fetched
        cand("https://site.in/c", method="keyword_page", headline="Ward update"),    # keyword only in article text
        cand("https://site.in/d", method="keyword_page", headline="Budget news"),    # no keyword anywhere
        cand("https://site.in/e", method="sitemap", headline="Ward news",           # keyword only in publisher's
             meta_keywords="Jaundice outbreak, Kerala"),                             # tags; page unavailable
        cand("https://site.in/f", headline="Dengue death in Pune"),                  # page unavailable (403)
    ]
    pages = {
        "https://site.in/a": ok("https://site.in/a", text(1)),
        "https://site.in/c": ok("https://site.in/c", text(2), title="Ward 4 reports dengue cluster | Pune News - Site"),
        "https://site.in/d": ok("https://site.in/d", "Budget allocations for roads. " * 30),
    }
    first, factory, fetched = run(candidates, pages)
    assert sorted(fetched) == ["https://site.in/a", "https://site.in/c", "https://site.in/d",
                               "https://site.in/e", "https://site.in/f"]
    assert first["status"] == "success" and first["mode"] == "incremental"
    assert (first["new"], first["no_keyword"], first["too_old"], first["text_missing"]) == (3, 3, 1, 1)

    with factory() as s:
        articles = {a.link_url: a for a in s.scalars(select(Article))}
        assert set(articles) == {"https://site.in/a", "https://site.in/c", "https://site.in/f"}
        a, c, f = articles["https://site.in/a"], articles["https://site.in/c"], articles["https://site.in/f"]
        assert a.matched_keywords == {"Dengue": {"headline": ["dengue"], "text": ["dengue"]}}
        assert a.date_source == "listing" and a.story_id == a.article_id
        assert c.headline == "Ward 4 reports dengue cluster"  # page title, site suffix removed
        assert c.collection_method == "keyword_page"
        assert f.extraction_status == "unavailable" and f.matched_keywords == {"Dengue": {"headline": ["dengue"]}}
        assert all(x.relevance in ("relevant", "irrelevant") for x in articles.values())  # classified on arrival
        assert all(x.relevance_status == "pending" for x in articles.values())           # manual review untouched
        # tags come from headline/text only, never from the sitemap's keywords or the URL
        assert s.get(SeenLink, "https://site.in/e").outcome == "no_keyword"
        assert s.get(SeenLink, "https://site.in/d").outcome == "no_keyword"

    second, _, fetched_again = run(candidates, pages, factory)
    assert fetched_again == [] and second["new"] == 0 and second["already_known"] == 5


def test_temporary_failure_is_retried_then_given_up():
    candidates = [cand("https://site.in/a", headline="Dengue death in Pune"),
                  cand("https://site.in/b", headline="Dengue cases rise to 12")]
    failed = {"extraction_status": "failed", "error": "ReadTimeout"}
    pages = {"https://site.in/a": [failed, failed, failed], "https://site.in/b": [failed, ok("https://site.in/b", text(4))]}
    factory = get_session_factory("sqlite://")

    first, _, _ = run(candidates, pages, factory)
    assert first["retry_later"] == 2 and first["new"] == 0
    second, _, _ = run(candidates, pages, factory)
    assert second["retry_later"] == 1 and second["new"] == 1                 # b succeeded on the 2nd try
    third, _, fetched = run(candidates, pages, factory)
    assert fetched == ["https://site.in/a"] and third["gave_up"] == 1 and third["new"] == 1  # a stored with metadata
    fourth, _, fetched = run(candidates, pages, factory)
    assert fetched == [] and fourth["new"] == 0
    with factory() as s:
        assert s.scalars(select(SeenLink)).all() == []  # retry records cleared once stored
        assert {a.link_url: a.extraction_status for a in s.scalars(select(Article))} == {
            "https://site.in/a": "failed", "https://site.in/b": "ok"}


def test_dedup_by_canonical_url_and_fingerprint():
    body = text(3)
    candidates = [cand("https://site.in/story"), cand("https://site.in/amp/story"), cand("https://site.in/copy")]
    pages = {
        "https://site.in/story": ok("https://site.in/story", body),
        "https://site.in/amp/story": ok("https://site.in/story", body),     # same canonical URL
        "https://site.in/copy": ok("https://site.in/copy", body.upper()),   # same text, other URL
    }
    result, _, _ = run(candidates, pages)
    assert result["new"] == 1 and result["duplicates"] == 2


def test_same_source_duplicate_needs_same_figures():
    candidates = [cand("https://site.in/1", headline="Dengue cases rise to 40 in Pune"),
                  cand("https://site.in/2", headline="Dengue cases rise to 48 in Pune"),    # new figure: new report
                  cand("https://site.in/3", headline="Dengue cases rise to 40 in Pune!")]   # republished: duplicate
    pages = {c["url"]: ok(c["url"], text(n)) for n, c in enumerate(candidates)}
    result, _, _ = run(candidates, pages)
    assert result["new"] == 2 and result["duplicates"] == 1


def test_same_story_from_two_sources_is_linked_not_dropped():
    other = Source("Other", domain="other.in")
    candidates = {"Site": [cand("https://site.in/1", headline="TN steps up dengue control as cases cross 17,900")],
                  "Other": [cand("https://other.in/9", headline="TN steps up dengue control as cases cross 17900"),
                            cand("https://other.in/8", headline="Kerala dengue cases cross 900")]}
    pages = {"https://site.in/1": ok("https://site.in/1", text(1)), "https://other.in/9": ok("https://other.in/9", text(2)),
             "https://other.in/8": ok("https://other.in/8", text(3))}
    result, factory, _ = run(candidates, pages, sources=(SITE, other))
    assert result["new"] == 3 and result["shared_story"] == 1
    with factory() as s:
        story = {a.link_url: a.story_id for a in s.scalars(select(Article))}
    assert story["https://site.in/1"] == story["https://other.in/9"] != story["https://other.in/8"]


def test_canonical_pointing_at_section_page_is_ignored():
    source = Source("Site", domain="site.in", article_pattern=r"-\d+$")
    page = ok("https://site.in/news", text(5))  # bad canonical: the section page
    result, factory, _ = run([cand("https://site.in/pune/dengue-rise-55")], {"https://site.in/pune/dengue-rise-55": page},
                             sources=(source,))
    with factory() as s:
        assert s.scalars(select(Article.article_url)).one() == "https://site.in/pune/dengue-rise-55"


def test_store_error_does_not_abort_run():
    candidates = [cand("https://site.in/a", headline="Dengue boom"), cand("https://site.in/b")]
    pages = {c["url"]: ok(c["url"], text(n)) for n, c in enumerate(candidates)}
    real = collector.tag_content

    def flaky(matcher, headline, body):
        if "boom" in headline:
            raise RuntimeError("boom")
        return real(matcher, headline, body)

    with patch.object(collector, "tag_content", flaky):
        result, factory, _ = run(candidates, pages)
    assert result["store_errors"] == 1 and result["new"] == 1 and result["status"] == "partial"


def test_failed_listing_marks_run_partial_and_is_recorded():
    listings = [{"source": "Site", "url": "https://site.in/rss", "kind": "rss", "deep": False, "ok": False,
                 "error": "ConnectionError: down"},
                {"source": "Site", "url": "https://site.in/news.xml", "kind": "sitemap", "deep": False, "ok": True,
                 "error": None}]
    result, factory, _ = run([cand("https://site.in/a")], {"https://site.in/a": ok("https://site.in/a", text(1))},
                             listings=listings)
    assert result["status"] == "partial" and result["new"] == 1
    with factory() as s:
        [r] = s.scalars(select(CollectionRun)).all()
        assert r.errors == [{"source": "Site", "url": "https://site.in/rss", "error": "ConnectionError: down"}]
        assert last_successful_run(s).run_id == r.run_id
        assert s.get(ListingStatus, "https://site.in/rss").last_error == "ConnectionError: down"
        assert s.get(ListingStatus, "https://site.in/news.xml").last_ok_at is not None


def test_listing_counts_as_read_only_after_its_articles_are_stored():
    source = Source("Site", domain="site.in", archive_sitemaps=["https://site.in/index.xml"])
    listings = [{"source": "Site", "url": "https://site.in/index.xml", "kind": "archive", "deep": True,
                 "ok": True, "error": None}]
    factory = get_session_factory("sqlite://")

    def crash(url):
        raise KeyboardInterrupt  # the run is stopped while articles are being downloaded

    with patch.object(collector, "discover", lambda s, d, **kw: Discovery(candidates=[cand("https://site.in/a")],
                                                                        listings=list(listings))), \
         patch.object(collector, "extract_article", crash):
        try:
            collector.collect(factory, [source], CFG)
        except KeyboardInterrupt:
            pass
    with factory() as s:
        assert s.get(ListingStatus, "https://site.in/index.xml") is None  # not marked as read
        assert collector.plan_deep_reads(s, [source], DISEASES, utcnow(), force=False)[0] == "backfill"


def test_plan_deep_reads_backfill_then_incremental_then_catch_up():
    source = Source("Site", domain="site.in", archive_sitemaps=["https://site.in/index.xml"],
                    keyword_pages=["https://site.in/topic/{slug}"], article_pattern=r"\d+$")
    listings = ["https://site.in/index.xml", "https://site.in/topic/dengue", "https://site.in/topic/hepatitis-a"]
    factory = get_session_factory("sqlite://")
    cutoff = utcnow() - timedelta(days=30)
    with factory() as s:
        mode, deep = collector.plan_deep_reads(s, [source], DISEASES, cutoff, force=False)
        assert mode == "backfill" and sorted(deep) == sorted(listings)  # never read in depth
        for url in listings:
            s.add(ListingStatus(url=url, source_name="Site", kind="x", deep_read_at=utcnow()))
        s.add(CollectionRun(status="success", started_at=utcnow(), finished_at=utcnow()))
        s.commit()
        assert collector.plan_deep_reads(s, [source], DISEASES, cutoff, force=False) == ("incremental", {})
        assert collector.plan_deep_reads(s, [source], DISEASES, cutoff, force=True)[0] == "backfill"

        s.add(CollectionRun(status="success", started_at=utcnow() - timedelta(days=3),
                            finished_at=utcnow() - timedelta(days=3)))
        s.query(CollectionRun).filter(CollectionRun.started_at > utcnow() - timedelta(hours=1)).delete()
        s.commit()
        mode, deep = collector.plan_deep_reads(s, [source], DISEASES, cutoff, force=False)
        assert mode == "catch-up" and set(deep) == set(listings)
        assert all(utcnow() - timedelta(days=4) < c < utcnow() - timedelta(days=3) for c in deep.values())


def test_config_change_rechecks_rejected_links():
    candidates = [cand("https://site.in/k", method="keyword_page", headline="Ward update")]
    pages = {"https://site.in/k": ok("https://site.in/k", "Officials reviewed jaundice outbreak cases. " * 20)}
    factory = get_session_factory("sqlite://")
    narrow = DiseaseConfig([Disease("Dengue", ["dengue"])], Settings(lookback_days=30))
    with patch.object(collector, "config_hash", return_value="v1"):
        with patch.object(collector, "discover", lambda s, d, **kw: Discovery(candidates=[dict(c) for c in candidates])), \
             patch.object(collector, "extract_article", lambda url: pages[url]):
            first = collector.collect(factory, [SITE], narrow)
    assert first["no_keyword"] == 1
    with patch.object(collector, "config_hash", return_value="v2"):  # hepatitis keywords added
        second, _, fetched = run(candidates, pages, factory)
    assert fetched == ["https://site.in/k"] and second["new"] == 1
