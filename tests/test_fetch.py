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


def _robots_session(*responses):
    return patch.object(fetch, "_session", return_value=MagicMock(get=MagicMock(side_effect=list(responses))))


def test_robots_txt_respected():
    resp = MagicMock(status_code=200, text="User-agent: *\nDisallow: /private/\n")
    with _robots_session(resp), patch.object(fetch, "_throttle"):
        fetch._robots.clear()
        assert fetch.allowed_by_robots("https://news.example/public/story")
        assert not fetch.allowed_by_robots("https://news.example/private/story")
    fetch._robots.clear()


def test_robots_unavailable_is_retried_after_short_ttl_not_cached_forever():
    ok = MagicMock(status_code=200, text="User-agent: *\nAllow: /\n")
    clock = [1000.0]
    with _robots_session(MagicMock(status_code=503), ok), patch.object(fetch, "_throttle"), \
         patch.object(fetch.time, "monotonic", side_effect=lambda: clock[0]):
        fetch._robots.clear()
        try:
            fetch.allowed_by_robots("https://news.example/a")
            assert False, "expected RobotsUnavailable"
        except fetch.RobotsUnavailable:
            pass
        clock[0] += fetch.ROBOTS_ERROR_TTL_SECONDS + 1  # 10 minutes later: read again, now fine
        assert fetch.allowed_by_robots("https://news.example/a")
    fetch._robots.clear()


def test_extract_article_statuses():
    with patch.object(extractor, "allowed_by_robots", return_value=False):
        assert extractor.extract_article("https://x.in/a")["extraction_status"] == "blocked"
    with patch.object(extractor, "allowed_by_robots", side_effect=fetch.RobotsUnavailable("x")):
        assert extractor.extract_article("https://x.in/a")["extraction_status"] == "failed"  # temporary
    with patch.object(extractor, "allowed_by_robots", return_value=True):
        for error, status in [(_http_error(403), "unavailable"), (_http_error(404), "unavailable"),
                              (_http_error(503), "failed"), (requests.ReadTimeout(), "failed")]:
            with patch.object(extractor, "get", side_effect=error):
                assert extractor.extract_article("https://x.in/a")["extraction_status"] == status


def test_get_stops_trickling_download():
    resp = MagicMock(raise_for_status=MagicMock(), iter_content=MagicMock(return_value=iter([b"x"] * 10)))
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    clock = iter(range(0, 1000, 30))  # every read takes 30 s
    with patch.object(fetch, "_session", return_value=MagicMock(get=MagicMock(return_value=resp))), \
         patch.object(fetch, "_throttle"), patch.object(fetch.time, "monotonic", side_effect=lambda: next(clock)):
        try:
            fetch.get.__wrapped__("https://news.example/slow")
            assert False, "expected DownloadTooLarge"
        except fetch.DownloadTooLarge:
            pass


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
