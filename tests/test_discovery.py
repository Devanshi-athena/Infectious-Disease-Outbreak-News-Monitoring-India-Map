from datetime import datetime
from unittest.mock import MagicMock, patch

import requests

from outbreak_monitor import discovery
from outbreak_monitor.config import Disease, Source, load_disease_config, load_sources
from outbreak_monitor.urls import canonicalize_url

SOURCE = Source("Example", domain="www.example.in", article_pattern=r"-\d+\.html$",
                keyword_pages=["https://www.example.in/topic/{slug}/page-{page}", "https://www.example.in/?s={query}"])
CUTOFF = datetime(2026, 9, 8)


def _resp(body: str, url="https://www.example.in/page"):
    return MagicMock(content=body.encode(), url=url)


def _http_error(status):
    return requests.HTTPError(response=MagicMock(status_code=status))


def test_canonicalize_url_strips_tracking_and_fragment():
    a = canonicalize_url("http://WWW.Example.com/news/story-1/?utm_source=rss&id=5#top")
    b = canonicalize_url("https://www.example.com/news/story-1?id=5")
    assert a == b == "https://www.example.com/news/story-1?id=5"


def test_clean_text_removes_html():
    assert discovery.clean_text("<p>Dengue &amp; malaria</p>  <br/>cases") == "Dengue & malaria cases"


def test_source_owns_domain_with_or_without_www():
    assert SOURCE.owns("www.example.in") and SOURCE.owns("example.in")
    assert not SOURCE.owns("tamil.example.in") and not SOURCE.owns("msn.com")


def test_keyword_page_templates_fill_terms_and_keep_page():
    diseases = [Disease("Flu", ["swine flu"], search_terms=["Swine flu", "h1n1"]), Disease("Dengue", ["dengue"])]
    assert discovery.keyword_page_templates(SOURCE, diseases) == [
        "https://www.example.in/topic/swine-flu/page-{page}", "https://www.example.in/topic/h1n1/page-{page}",
        "https://www.example.in/topic/dengue/page-{page}",
        "https://www.example.in/?s=swine+flu", "https://www.example.in/?s=h1n1", "https://www.example.in/?s=dengue",
    ]


def test_read_rss():
    feed = """<rss><channel><item><title>Dengue cases rise</title>
      <link>https://www.example.in/news/dengue-1.html?utm_source=rss</link>
      <pubDate>Mon, 19 Oct 2026 10:00:00 +0530</pubDate><description>&lt;p&gt;40 cases&lt;/p&gt;</description>
    </item></channel></rss>"""
    with patch.object(discovery, "_fetch_allowed", return_value=_resp(feed)):
        [item] = discovery.read_rss("https://www.example.in/rss")
    assert item["url"] == "https://www.example.in/news/dengue-1.html"
    assert item["headline"] == "Dengue cases rise" and item["excerpt"] == "40 cases"
    assert item["published_at"].isoformat() == "2026-10-19T04:30:00" and item["method"] == "rss"


NEWS_SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url><loc>https://www.example.in/india/kozhikode-measles-5.html</loc>
    <news:news><news:publication_date>2026-10-17T11:40:28+05:30</news:publication_date>
      <news:title>161 confirmed cases, one death in Kozhikode</news:title>
      <news:keywords>measles, Kerala</news:keywords></news:news></url>
  <url><loc>https://www.example.in/india/old-story-6.html</loc><lastmod>2026-08-01T10:00:00+05:30</lastmod></url>
  <url><loc>https://www.example.in/india/dengue-in-pune-7.html</loc><lastmod>2026-10-01T10:00:00+05:30</lastmod></url>
</urlset>"""


def test_read_sitemap_with_and_without_titles():
    with patch.object(discovery, "_fetch_allowed", return_value=_resp(NEWS_SITEMAP)):
        items = discovery.read_sitemap("https://www.example.in/news-sitemap.xml", CUTOFF)
    assert [i["url"].rsplit("/", 1)[1] for i in items] == ["kozhikode-measles-5.html", "dengue-in-pune-7.html"]
    assert items[0]["headline"] == "161 confirmed cases, one death in Kozhikode"
    assert items[0]["meta_keywords"] == "measles, Kerala"
    assert items[0]["published_at"].isoformat() == "2026-10-17T06:10:28"
    assert items[1]["headline"] is None  # archive-style entry: only URL and lastmod


def test_read_sitemap_index_reads_only_children_in_window():
    index = """<sitemapindex>
      <sitemap><loc>https://www.example.in/sitemap/october-2026.xml</loc></sitemap>
      <sitemap><loc>https://www.example.in/sitemap/august-2026.xml</loc></sitemap>
      <sitemap><loc>https://www.example.in/sitemap.xml?yyyy=2026&amp;mm=09&amp;dd=20</loc></sitemap>
      <sitemap><loc>https://www.example.in/post-sitemap9.xml</loc><lastmod>2026-09-01T00:00:00Z</lastmod></sitemap>
      <sitemap><loc>https://www.example.in/sitemap/topic-1.xml</loc></sitemap>
    </sitemapindex>"""
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return _resp(index if url.endswith("index.xml") else "<urlset></urlset>")

    with patch.object(discovery, "_fetch_allowed", side_effect=fake_fetch):
        discovery.read_sitemap("https://www.example.in/index.xml", CUTOFF, child_pattern=r"(-\d{4}\.xml|yyyy=|post-)")
    assert fetched[1:] == ["https://www.example.in/sitemap/october-2026.xml",
                           "https://www.example.in/sitemap.xml?yyyy=2026&mm=09&dd=20"]


def test_period_end_from_archive_names():
    assert discovery._period_end("https://x.in/sitemap/october-2026.xml") == datetime(2026, 10, 31, 23, 59)
    assert discovery._period_end("https://x.in/news/2026-February-1.xml") == datetime(2026, 2, 28, 23, 59)
    assert discovery._period_end("https://x.in/sitemap.xml?yyyy=2026&mm=10&dd=21") == datetime(2026, 10, 21, 23, 59)
    assert discovery._period_end("https://x.in/post-sitemap3.xml") is None


def test_read_keyword_page_keeps_only_own_article_links():
    page = """<html><body>
      <a href="/topic/dengue">Dengue</a>
      <a href="/city/pune/dengue-surge-12.html"><img src="x.jpg"></a>
      <a href="/city/pune/dengue-surge-12.html">Dengue surge delays surgeries in Pune hospitals</a>
      <a href="example.in/india/measles-kerala-15.html">Measles cases in Kerala rise to 161</a>
      <a href="https://tamil.example.in/news/dengue-13.html">Tamil edition story</a>
      <a href="https://other.com/story-14.html">Other site</a>
    </body></html>"""
    with patch.object(discovery, "_fetch_allowed", return_value=_resp(page, "https://www.example.in/topic/dengue")):
        items = discovery.read_keyword_page("https://www.example.in/topic/dengue", SOURCE)
    assert [(i["url"], i["headline"]) for i in items] == [
        ("https://www.example.in/city/pune/dengue-surge-12.html", "Dengue surge delays surgeries in Pune hospitals"),
        ("https://example.in/india/measles-kerala-15.html", "Measles cases in Kerala rise to 161"),  # scheme-less href
    ]


def _page(*ids):
    return [{"url": f"https://www.example.in/a-{i}.html"} for i in ids]


def test_keyword_pagination_stops_on_repeat_known_or_missing_page():
    tpl = "https://www.example.in/topic/dengue/page-{page}"
    pages = {1: _page(1, 2), 2: _page(3, 4), 3: _page(3, 4), 4: _page(5)}
    with patch.object(discovery, "read_keyword_page", side_effect=lambda url, s: pages[int(url.rsplit("-", 1)[1])]):
        # page 3 repeats page 2 (site ignores the page number) -> stop
        assert len(discovery.read_keyword_pages(tpl, SOURCE, 10, set())) == 4
        # incremental: one page only
        assert len(discovery.read_keyword_pages(tpl, SOURCE, 1, set())) == 2
        # page 1 already fully known -> stop
        assert len(discovery.read_keyword_pages(tpl, SOURCE, 10, {"https://www.example.in/a-1.html",
                                                                  "https://www.example.in/a-2.html"})) == 2
    with patch.object(discovery, "read_keyword_page", side_effect=[_page(1), _http_error(404)]):
        assert len(discovery.read_keyword_pages(tpl, SOURCE, 10, set())) == 1  # ran past last page


def test_discover_modes_failures_and_filters():
    source = Source("Example", domain="www.example.in", rss=["https://www.example.in/rss"],
                    sitemaps=["https://www.example.in/news.xml"], archive_sitemaps=["https://www.example.in/index.xml"],
                    keyword_pages=["https://www.example.in/t/{slug}"], article_pattern=r"\.html$")
    sitemap_items = [{"url": "https://www.example.in/a.html"}, {"url": "https://other.com/b.html"},
                     {"url": "https://www.example.in/topics/dengue"}]

    def run(backfill):
        with patch.object(discovery, "read_rss", side_effect=ConnectionError("down")), \
             patch.object(discovery, "read_sitemap", return_value=[dict(i) for i in sitemap_items]) as sitemap, \
             patch.object(discovery, "read_keyword_page", side_effect=_http_error(404)):
            result = discovery.discover(source, [Disease("Dengue", ["dengue"])], backfill=backfill,
                                        cutoff=CUTOFF, max_pages=5, known_urls=set())
        return result, sitemap.call_count

    result, sitemap_calls = run(backfill=False)
    assert sitemap_calls == 1 and result.listings_ok == 1 and len(result.errors) == 1  # 404 keyword page: no error
    assert [(c["url"], c["listing_url"]) for c in result.candidates] == [
        ("https://www.example.in/a.html", "https://www.example.in/news.xml")]  # other domain + topic page dropped
    _, sitemap_calls = run(backfill=True)
    assert sitemap_calls == 2  # archive read too


def test_config_files_load():
    sources = load_sources()
    assert len(sources) >= 5
    for s in sources:
        assert s.domain and (s.rss or s.sitemaps or s.keyword_pages)
        assert not s.keyword_pages or s.article_pattern
    cfg = load_disease_config()
    assert cfg.diseases and all(d.keywords and d.search_terms for d in cfg.diseases)
    assert cfg.settings.lookback_days > 0 and cfg.settings.backfill_max_pages >= 1
