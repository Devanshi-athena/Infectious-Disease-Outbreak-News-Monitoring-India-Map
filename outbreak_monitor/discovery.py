"""Find candidate articles on a source: RSS feeds, sitemaps and keyword (topic/search) pages.

Each reader returns a list of candidate dicts:
    url, headline, published_at, excerpt, meta_keywords, method, listing_url
Only links on the source's own domain are returned. Article pages themselves are fetched
later by the extractor.

Every run reads RSS, news sitemaps and the first page of each keyword page. Listings that the
collector plans to read "in depth" (see collector.plan_deep_reads) are read further: archive
sitemaps back to a cutoff, and keyword pages up to `backfill_max_pages` pages.
"""

import calendar
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .config import Disease, Source
from .fetch import allowed_by_robots, get
from .urls import canonicalize_url

log = logging.getLogger(__name__)

SCHEMELESS_LINK = re.compile(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+/", re.IGNORECASE)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


class FeedParseError(Exception):
    pass


class DisallowedByRobots(Exception):
    pass


class PartialListing(Exception):
    """Some files of a sitemap index failed; the items from the other files are kept."""

    def __init__(self, items: list[dict], failures: list[str]):
        super().__init__(f"{len(failures)} sitemap file(s) failed: " + "; ".join(failures[:3]))
        self.items = items


@dataclass
class Discovery:
    """Everything found on one source in one run.
    listings: one entry per listing read: {url, kind, deep, ok, error}."""
    candidates: list[dict] = field(default_factory=list)
    listings: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[dict]:
        return [{"source": l["source"], "url": l["url"], "error": l["error"]} for l in self.listings if not l["ok"]]


def clean_text(raw: str | None) -> str | None:
    """Strip HTML tags/entities and collapse whitespace."""
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text or None


def _parse_iso(value: str | None) -> datetime | None:
    """ISO 8601 string -> naive UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _candidate(url, headline, method, published_at=None, excerpt=None, meta_keywords=None) -> dict:
    return {"url": canonicalize_url(url), "headline": clean_text(headline), "published_at": published_at,
            "excerpt": clean_text(excerpt), "meta_keywords": meta_keywords, "method": method}


def _fetch_allowed(url: str) -> requests.Response:
    if not allowed_by_robots(url):
        raise DisallowedByRobots(url)
    return get(url)


# ---------------------------------------------------------------- RSS

# Feeds occasionally come back truncated/malformed; one more try usually works.
@retry(retry=retry_if_exception_type(FeedParseError), stop=stop_after_attempt(2), wait=wait_fixed(3), reraise=True)
def read_rss(feed_url: str) -> list[dict]:
    parsed = feedparser.parse(_fetch_allowed(feed_url).content)
    if parsed.bozo and not parsed.entries:
        raise FeedParseError(f"unparseable feed: {parsed.bozo_exception}")
    items = []
    for e in parsed.entries:
        if not e.get("link") or not e.get("title"):
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")  # UTC struct_time
        items.append(_candidate(e.link, e.title, "rss", published_at=datetime(*t[:6]) if t else None,
                                excerpt=e.get("summary")))
    return items


# ---------------------------------------------------------------- sitemaps

def _period_end(url: str) -> datetime | None:
    """Last day covered by an archive sitemap, from its URL (daily or monthly naming)."""
    if m := re.search(r"yyyy=(\d{4})&mm=(\d{1,2})&dd=(\d{1,2})", url):
        return datetime(int(m[1]), int(m[2]), int(m[3]), 23, 59)
    m = (re.search(r"(\d{4})-([a-z]+)", url, re.I) or re.search(r"([a-z]+)-(\d{4})", url, re.I))
    if m:
        year, month = (m[1], m[2]) if m[1].isdigit() else (m[2], m[1])
        if month.lower() in MONTHS:
            month_no = MONTHS[month.lower()]
            return datetime(int(year), month_no, calendar.monthrange(int(year), month_no)[1], 23, 59)
    return None


def _child_in_window(loc: str, lastmod: str | None, cutoff: datetime) -> bool:
    """Should this child of a sitemap index be read? Both the period in its URL (e.g. october-2026)
    and its <lastmod> must not lie entirely before the cutoff; unknown values don't exclude it."""
    return all(d is None or d >= cutoff for d in (_period_end(loc), _parse_iso(lastmod)))


def _parse_urlset(soup: BeautifulSoup, cutoff: datetime | None, method: str) -> list[dict]:
    items = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        if not loc:
            continue
        title, date, keywords, lastmod = (node.find(n) for n in ("title", "publication_date", "keywords", "lastmod"))
        published = _parse_iso(date.get_text(strip=True) if date else None)
        modified = _parse_iso(lastmod.get_text(strip=True) if lastmod else None)
        if cutoff and (published or modified) and (published or modified) < cutoff:
            continue
        items.append(_candidate(loc.get_text(strip=True), title.get_text(strip=True) if title else None, method,
                                published_at=published,
                                meta_keywords=keywords.get_text(strip=True) if keywords else None))
    return items


def read_sitemap(url: str, cutoff: datetime | None = None, child_pattern: str | None = None,
                 method: str = "sitemap") -> list[dict]:
    """Read a sitemap or sitemap index (Google-News or plain). For an index, children are read
    if they match child_pattern and may contain entries newer than cutoff. If some children fail,
    PartialListing is raised carrying the items of the others."""
    soup = BeautifulSoup(_fetch_allowed(url).content, "xml")
    if not soup.find("sitemapindex"):
        return _parse_urlset(soup, cutoff, method)
    items, failures = [], []
    for node in soup.find_all("sitemap"):
        loc_tag, lastmod = node.find("loc"), node.find("lastmod")
        if not loc_tag:
            continue
        loc = loc_tag.get_text(strip=True)
        if child_pattern and not re.search(child_pattern, loc):
            continue
        if cutoff and not _child_in_window(loc, lastmod.get_text(strip=True) if lastmod else None, cutoff):
            continue
        try:
            items.extend(_parse_urlset(BeautifulSoup(_fetch_allowed(loc).content, "xml"), cutoff, method))
        except Exception as exc:  # keep the other files
            failures.append(f"{loc}: {type(exc).__name__}")
            log.warning("Sitemap file failed %s: %s", loc, exc)
    if failures:
        raise PartialListing(items, failures)
    return items


# ---------------------------------------------------------------- keyword pages

def read_keyword_page(page_url: str, source: Source) -> list[dict]:
    """Collect article links (matching source.article_pattern) from a topic/tag/search page."""
    resp = _fetch_allowed(page_url)
    soup = BeautifulSoup(resp.content, "lxml")
    pattern = re.compile(source.article_pattern) if source.article_pattern else None
    found: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if SCHEMELESS_LINK.match(href):  # e.g. "hindustantimes.com/india-news/..." (no https://)
            href = "https://" + href
        url = canonicalize_url(urljoin(resp.url, href))
        parts = urlsplit(url)
        if not source.owns(parts.netloc) or (pattern and not pattern.search(parts.path)):
            continue
        text = clean_text(a.get_text(" ")) or ""
        if len(text) > len(found.get(url, "")):  # the same link may appear as image + headline
            found[url] = text
    return [_candidate(url, text or None, "keyword_page") for url, text in found.items()]


def read_keyword_pages(template: str, source: Source, max_pages: int, known_urls: set[str]) -> list[dict]:
    """Read page 1, 2, ... of one keyword page (template may contain {page}).
    Stops at max_pages, when a page brings no new links, or when all its links are already known."""
    items, seen = [], set()
    for page in range(1, max_pages + 1):
        if page > 1 and "{page}" not in template:
            break
        try:
            page_items = read_keyword_page(template.format(page=page), source)
        except requests.HTTPError as exc:
            if page > 1 and exc.response is not None and exc.response.status_code in (404, 410):
                break  # ran past the last page
            raise
        new = [i for i in page_items if i["url"] not in seen]
        if not new:
            break
        items.extend(new)
        seen.update(i["url"] for i in new)
        if all(i["url"] in known_urls for i in new):
            break  # reached articles we already have
    return items


def keyword_page_templates(source: Source, diseases: list[Disease]) -> list[str]:
    """One URL template per (keyword page, search term); {page} is left in place for pagination."""
    terms = dict.fromkeys(t.strip().lower() for d in diseases for t in d.search_terms if t.strip())
    return [tpl.format(slug=re.sub(r"\s+", "-", t), query=quote_plus(t), page="{page}")
            for tpl in source.keyword_pages for t in terms]


# ---------------------------------------------------------------- per source

def discover(source: Source, diseases: list[Disease], *, cutoff: datetime, deep: dict[str, datetime],
             max_pages: int, known_urls: set[str]) -> Discovery:
    """Run all configured collection methods for one source. Failures are recorded, not raised.

    deep maps archive-sitemap URLs and keyword-page templates to read in depth to the cutoff for
    that read (archives are only read when listed; keyword pages get max_pages instead of 1).
    """
    result = Discovery()
    jobs = [(url, "rss", False, read_rss) for url in source.rss]
    jobs += [(url, "sitemap", False, lambda u: read_sitemap(u, cutoff)) for url in source.sitemaps]
    jobs += [(url, "archive", True,
              lambda u: read_sitemap(u, deep[u], source.archive_child_pattern, "archive"))
             for url in source.archive_sitemaps if url in deep]
    jobs += [(tpl, "keyword_page", tpl in deep,
              lambda t: read_keyword_pages(t, source, max_pages if t in deep else 1, known_urls))
             for tpl in keyword_page_templates(source, diseases)]
    pattern = re.compile(source.article_pattern) if source.article_pattern else None

    for url, kind, is_deep, reader in jobs:
        items, error = [], None
        try:
            items = reader(url)
        except PartialListing as exc:
            items, error = exc.items, str(exc)
        except requests.HTTPError as exc:
            if kind == "keyword_page" and exc.response is not None and exc.response.status_code in (404, 410):
                log.debug("No keyword page %s", url)  # the site simply has no page for this term
            else:
                error = f"HTTPError: {exc}"
        except Exception as exc:  # one bad listing must not stop the run
            error = f"{type(exc).__name__}: {exc}"
        if error:
            log.warning("FAILED %s %s: %s", source.name, url, error)
        result.listings.append({"source": source.name, "url": url, "kind": kind, "deep": is_deep,
                                "ok": error is None, "error": error[:500] if error else None})
        for c in items:
            parts = urlsplit(c["url"])
            # sitemaps also list section/topic pages; keep article URLs only
            if source.owns(parts.netloc) and (kind == "rss" or not pattern or pattern.search(parts.path)):
                c["listing_url"] = url
                result.candidates.append(c)
    return result
