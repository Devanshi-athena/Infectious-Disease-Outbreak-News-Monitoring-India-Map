"""Collect disease-related articles from the configured sources into the database.

    python -m outbreak_monitor.collector              # manual refresh (backfills automatically if never done)
    python -m outbreak_monitor.collector --backfill   # force a full backfill of the lookback window
    python -m outbreak_monitor.scheduler              # run on a schedule

One run:
 1. Discovery (per source, in parallel): RSS feeds, news sitemaps and keyword pages; in backfill
    mode also archive sitemaps and further keyword-page pages (see discovery.py).
 2. Pre-filter: skip links already stored or rejected and items older than lookback_days;
    RSS/sitemap items must contain a disease keyword in headline/summary/sitemap keywords/URL.
    Keyword-page links always go on (the publisher tagged them with the disease).
 3. Extraction (per source, in parallel): download the article page (robots.txt permitting) and
    extract text, canonical URL, title and date with Trafilatura.
 4. Final check and store: keyword match including article text, lookback window, duplicate
    check (canonical URL, content fingerprint, near-identical headline), then save.
Config files are re-read on every run, so edits apply without restarting the scheduler.
"""

import argparse
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import load_disease_config, load_sources
from .db import Article, CollectionRun, SeenLink, get_session_factory, last_successful_run, utcnow
from .discovery import discover
from .extractor import content_fingerprint, extract_article
from .logs import setup_logging
from .matcher import KeywordMatcher

log = logging.getLogger(__name__)

METADATA_FIELDS = ("headline", "published_at", "excerpt", "meta_keywords")
HEADLINE_SIMILARITY = 95  # rapidfuzz ratio (0-100) above which two headlines of one source are the same story


def normalise_headline(headline: str) -> str:
    return " ".join(re.findall(r"\w+", headline.lower()))


def merge_candidates(candidates: list[dict]) -> list[dict]:
    """Combine repeated sightings of one URL (e.g. in RSS and on a topic page) into one candidate."""
    merged: dict[str, dict] = {}
    for c in candidates:
        m = merged.get(c["url"])
        if m is None:
            merged[c["url"]] = {**c, "methods": [c["method"]]}
            continue
        if c["method"] not in m["methods"]:
            m["methods"].append(c["method"])
        for key in METADATA_FIELDS:
            if not m.get(key) and c.get(key):
                m[key] = c[key]
    return list(merged.values())


def _extract_all(candidates: list[dict]) -> list[tuple[dict, dict]]:
    """Download and extract each candidate (runs inside one source's worker thread)."""
    return [(c, extract_article(c["url"])) for c in candidates]


def needs_backfill(session) -> bool:
    """True until one backfill run has completed."""
    return session.scalar(select(CollectionRun.run_id).where(
        CollectionRun.mode == "backfill", CollectionRun.status.in_(["success", "partial"])).limit(1)) is None


class _Run:
    """State and statistics for one collection run (used from the main thread only)."""

    def __init__(self, session, matcher: KeywordMatcher, cutoff: datetime):
        self.session = session
        self.matcher = matcher
        self.cutoff = cutoff
        self.known_urls = (set(session.scalars(select(Article.article_url)))
                           | set(session.scalars(select(Article.link_url)))
                           | set(session.scalars(select(SeenLink.url))))
        self.known_hashes = set(session.scalars(select(Article.content_hash).where(Article.content_hash.is_not(None))))
        self.known_headlines: dict[str, list[str]] = {}
        for source_name, headline in session.execute(select(Article.source_name, Article.headline)):
            self.known_headlines.setdefault(source_name, []).append(normalise_headline(headline))
        self.stats = dict.fromkeys(["listings_ok", "listings_failed", "candidates", "already_known", "no_keyword",
                                    "too_old", "duplicates", "new", "text_ok", "text_missing"], 0)
        self.new_by_method: dict[str, int] = {}
        self.errors: list[dict] = []

    def _tags(self, c: dict, *extra: str | None, url: str | None = None) -> list[str]:
        return self.matcher.match(c.get("headline"), c.get("excerpt"), c.get("meta_keywords"),
                                  urlsplit(url or c["url"]).path, *extra)

    def _is_near_duplicate(self, source_name: str, headline: str) -> bool:
        """Same story republished by the same source under another URL (e.g. city + national section)."""
        return process.extractOne(normalise_headline(headline), self.known_headlines.get(source_name, []),
                                  scorer=fuzz.ratio, score_cutoff=HEADLINE_SIMILARITY) is not None

    def prefilter(self, candidates: list[dict]) -> list[dict]:
        """Drop known links, old items, and listing items without a keyword (except keyword-page links)."""
        keep = []
        for c in merge_candidates(candidates):
            self.stats["candidates"] += 1
            if c["url"] in self.known_urls:
                self.stats["already_known"] += 1
            elif c.get("published_at") and c["published_at"] < self.cutoff:
                self.stats["too_old"] += 1
            elif "keyword_page" not in c["methods"] and not self._tags(c):
                self.stats["no_keyword"] += 1
            else:
                keep.append(c)
        return keep

    def _remember(self, url: str, outcome: str) -> None:
        self.session.merge(SeenLink(url=url, outcome=outcome))
        self.session.commit()
        self.known_urls.add(url)
        self.stats[outcome if outcome != "duplicate" else "duplicates"] += 1

    def store(self, source_name: str, c: dict, info: dict) -> None:
        if info.get("title") and (set(c["methods"]) & {"keyword_page", "archive"} or not c.get("headline")):
            c["headline"] = info["title"]  # topic-page anchor text is noisy; archive entries have no title
        text = info.get("article_text")
        canonical = info.get("canonical_url") or c["url"]
        fingerprint = content_fingerprint(text)
        published = c.get("published_at") or info.get("published_at")
        tags = self._tags(c, text, url=canonical)

        if not tags or not c.get("headline"):
            return self._remember(c["url"], "no_keyword")
        if published and published < self.cutoff:
            return self._remember(c["url"], "too_old")
        if (canonical in self.known_urls or (fingerprint and fingerprint in self.known_hashes)
                or self._is_near_duplicate(source_name, c["headline"])):
            return self._remember(c["url"], "duplicate")

        self.session.add(Article(
            headline=c["headline"], source_name=source_name, article_url=canonical, link_url=c["url"],
            collection_method=",".join(c["methods"]), listing_url=c.get("listing_url"),
            published_at=published, excerpt=c.get("excerpt"), article_text=text,
            extraction_status=info["extraction_status"], content_hash=fingerprint, disease_tags=tags))
        try:
            self.session.commit()
        except IntegrityError:  # stored meanwhile by a concurrent run
            self.session.rollback()
            self.stats["duplicates"] += 1
            return

        self.known_urls |= {canonical, c["url"]}
        if fingerprint:
            self.known_hashes.add(fingerprint)
        self.known_headlines.setdefault(source_name, []).append(normalise_headline(c["headline"]))
        self.stats["new"] += 1
        self.stats["text_ok" if info["extraction_status"] == "ok" else "text_missing"] += 1
        for method in c["methods"]:
            self.new_by_method[method] = self.new_by_method.get(method, 0) + 1
        if info["extraction_status"] != "ok":
            log.info("Stored without text (%s): %s %s", info["extraction_status"], c["url"], info.get("error", ""))


def collect(session_factory=None, sources=None, disease_config=None, backfill: bool | None = None) -> dict:
    """Run discovery, extraction and storage for all enabled sources. Returns run statistics.

    backfill=None decides automatically: backfill until one backfill run has completed.
    """
    session_factory = session_factory or get_session_factory()
    sources = [s for s in (sources if sources is not None else load_sources()) if s.enabled]
    disease_config = disease_config or load_disease_config()
    diseases, settings = disease_config.diseases, disease_config.settings

    with session_factory() as session:
        if backfill is None:
            backfill = needs_backfill(session)
        run_record = CollectionRun(mode="backfill" if backfill else "incremental")
        session.add(run_record)
        session.commit()
        cutoff = utcnow() - timedelta(days=settings.lookback_days)
        run = _Run(session, KeywordMatcher(diseases), cutoff)
        log.info("Starting %s run (articles since %s UTC)", run_record.mode, cutoff.strftime("%Y-%m-%d %H:%M"))
        try:
            # One worker per source; each worker only talks to its own site, so the
            # per-domain rate limit in fetch.py still applies.
            with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
                discoveries = list(pool.map(
                    lambda s: discover(s, diseases, backfill=backfill, cutoff=cutoff,
                                       max_pages=settings.backfill_max_pages, known_urls=run.known_urls),
                    sources))
                to_fetch = {}
                for source, found in zip(sources, discoveries):
                    run.stats["listings_ok"] += found.listings_ok
                    run.stats["listings_failed"] += len(found.errors)
                    run.errors.extend(found.errors)
                    to_fetch[source.name] = run.prefilter(found.candidates)
                    log.info("%-20s %5d links found, %4d to fetch", source.name,
                             len(found.candidates), len(to_fetch[source.name]))

                futures = {name: pool.submit(_extract_all, cands) for name, cands in to_fetch.items()}
                for name, future in futures.items():
                    before = run.stats["new"]
                    for candidate, info in future.result():
                        run.store(name, candidate, info)
                    log.info("%-20s %4d new articles", name, run.stats["new"] - before)

            if run.stats["listings_failed"] == 0:
                run_record.status = "success"
            else:
                run_record.status = "partial" if run.stats["listings_ok"] else "failed"
        except Exception as exc:
            log.exception("Collection run aborted")
            session.rollback()
            run_record.status = "failed"
            run.errors.append({"source": None, "url": None, "error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            run_record.finished_at = utcnow()
            run_record.stats = {**run.stats, "new_by_method": run.new_by_method}
            run_record.errors = run.errors
            session.commit()
        return {"mode": run_record.mode, "status": run_record.status, **run.stats, "new_by_method": run.new_by_method}


def run_and_report(session_factory=None, backfill: bool | None = None) -> dict:
    """One collection run with a summary in the log."""
    session_factory = session_factory or get_session_factory()
    result = collect(session_factory, backfill=backfill)
    with session_factory() as session:
        last = last_successful_run(session)
    log.info("Run finished: %s", result)
    log.info("Last successful refresh: %s UTC", last.finished_at if last else "never")
    return result


def main():
    parser = argparse.ArgumentParser(description="Collect outbreak news articles once.")
    parser.add_argument("--backfill", action="store_true",
                        help="collect the whole lookback window (archive sitemaps, all keyword-page pages)")
    args = parser.parse_args()
    setup_logging()
    run_and_report(backfill=True if args.backfill else None)


if __name__ == "__main__":
    main()
