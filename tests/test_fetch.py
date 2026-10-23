from unittest.mock import MagicMock, patch

import requests

from outbreak_monitor import extractor, fetch


def _http_error(status):
    resp = MagicMock(status_code=status)
    return requests.HTTPError(response=resp)


def test_is_transient():
    assert fetch.is_transient(requests.ConnectionError())
    assert fetch.is_transient(requests.Timeout())
    assert fetch.is_transient(_http_error(503))
    assert fetch.is_transient(_http_error(429))
    assert not fetch.is_transient(_http_error(404))
    assert not fetch.is_transient(_http_error(403))


def test_robots_txt_respected():
    resp = MagicMock(status_code=200, text="User-agent: *\nDisallow: /private/\n")
    with patch.object(fetch, "_session", return_value=MagicMock(get=MagicMock(return_value=resp))), \
         patch.object(fetch, "_throttle"):
        fetch._robots.clear()
        assert fetch.allowed_by_robots("https://news.example/public/story")
        assert not fetch.allowed_by_robots("https://news.example/private/story")
    fetch._robots.clear()


def test_extract_article_blocked_by_robots():
    with patch.object(extractor, "allowed_by_robots", return_value=False):
        assert extractor.extract_article("https://x.in/a")["extraction_status"] == "blocked"


def test_extract_article_uses_canonical_and_text():
    html = f"""<html><head><title>Dengue cases rise</title>
    <link rel="canonical" href="https://news.example/health/dengue-cases-rise">
    <meta property="article:published_time" content="2026-10-18T10:00:00+05:30"></head>
    <body><article><h1>Dengue cases rise</h1>
    {''.join(f'<p>Officials reported {n} new dengue cases in ward {n} of Pune district this week.</p>' for n in range(40, 50))}
    </article></body></html>"""
    resp = MagicMock(content=html.encode(), url="https://news.example/health/dengue-cases-rise?utm_source=rss")
    with patch.object(extractor, "allowed_by_robots", return_value=True), patch.object(extractor, "get", return_value=resp):
        info = extractor.extract_article("https://news.example/health/dengue-cases-rise?utm_source=rss")
    assert info["extraction_status"] == "ok"
    assert info["canonical_url"] == "https://news.example/health/dengue-cases-rise"
    assert "40 new dengue cases" in info["article_text"]
    assert info["published_at"].date().isoformat() == "2026-10-18"


def test_fingerprint_ignores_case_punctuation_and_short_text():
    text = "Officials reported new dengue cases. " * 20
    assert extractor.content_fingerprint(text) == extractor.content_fingerprint(text.upper().replace(".", ","))
    assert extractor.content_fingerprint("short") is None
