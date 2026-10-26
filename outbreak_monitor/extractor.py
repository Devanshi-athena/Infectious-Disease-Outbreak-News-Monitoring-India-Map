"""Download an article page and extract its main text and metadata with Trafilatura."""

import hashlib
import logging
import re
from datetime import datetime

import requests
import trafilatura

from .fetch import RobotsUnavailable, allowed_by_robots, get
from .urls import canonicalize_url

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 300          # shorter extractions are usually cookie banners/teasers
MIN_FINGERPRINT_CHARS = 500   # only fingerprint texts long enough to be distinctive


def content_fingerprint(text: str | None) -> str | None:
    """Hash of the normalised words, so identical stories on different URLs collide."""
    if not text or len(text) < MIN_FINGERPRINT_CHARS:
        return None
    words = re.findall(r"\w+", text.lower())
    return hashlib.sha256(" ".join(words).encode()).hexdigest()


def extract_article(url: str) -> dict:
    """Download and extract one article.

    extraction_status:
        ok           text extracted
        no_text      page downloaded but no usable article text
        blocked      robots.txt disallows the page (permanent)
        unavailable  the site refused or does not have the page (HTTP 4xx, permanent)
        failed       temporary problem (network, timeout, 429/5xx, robots.txt unreadable); retry later
    Also returned when found: article_text, canonical_url, title, published_at, error.
    """
    try:
        if not allowed_by_robots(url):
            return {"extraction_status": "blocked", "error": "disallowed by robots.txt"}
    except RobotsUnavailable as exc:
        return {"extraction_status": "failed", "error": str(exc)}
    try:
        resp = get(url)
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        permanent = code is not None and 400 <= code < 500 and code != 429
        return {"extraction_status": "unavailable" if permanent else "failed", "error": f"HTTP {code}"}
    except Exception as exc:
        return {"extraction_status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}

    result = {"extraction_status": "no_text", "canonical_url": canonicalize_url(resp.url)}
    try:
        doc = trafilatura.bare_extraction(resp.content, url=resp.url, with_metadata=True,
                                          include_comments=False, include_tables=False)
    except Exception as exc:  # malformed page
        return {**result, "error": f"extraction error: {type(exc).__name__}"}
    if doc is None:
        return result
    if doc.url:
        canonical = canonicalize_url(doc.url)
        if canonical.rstrip("/").count("/") > 2:  # ignore canonicals pointing at a site's home page
            result["canonical_url"] = canonical
    if doc.title:
        result["title"] = doc.title
    if doc.date:
        try:
            result["published_at"] = datetime.strptime(doc.date[:10], "%Y-%m-%d")
        except ValueError:
            pass
    if doc.text and len(doc.text) >= MIN_TEXT_CHARS:
        result["extraction_status"] = "ok"
        result["article_text"] = doc.text
    return result
