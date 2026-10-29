"""Collect disease-related articles from the configured sources into the database.

    python -m outbreak_monitor.collector              # one run (manual refresh)
    python -m outbreak_monitor.collector --backfill   # force an in-depth read of every listing
    python -m outbreak_monitor.scheduler              # run on a schedule

One run:
 0. Plan: listings never read in depth (first run, a new source or search term, an archive that
    failed before) are read in depth over lookback_days ("backfill"); after a gap of more than
    CATCH_UP_AFTER, all archives and keyword pages are read back to the last run ("catch-up").
    If config/diseases.yaml or config/relevance.yaml changed, stored articles are re-processed
    and links rejected for lacking keywords are checked again.
 1. Discovery (per source, in parallel): RSS feeds, news sitemaps, keyword pages, and archive
    sitemaps when planned (see discovery.py). Only links on the source's own domain are kept.
 2. Pre-filter: skip links already stored or rejected and items older than lookback_days;
    RSS/sitemap items must contain a disease keyword in headline/summary/sitemap keywords/URL.
    Keyword-page links always go on (the publisher tagged them with the disease).
 3. Extraction (per source, in parallel): download the article page (robots.txt permitting) and
    extract text, canonical URL, title and date with Trafilatura.
 4. Store (as each source finishes): clean the headline; tag diseases from headline and article
    text only, recording the exact keywords; reject old items and duplicates (canonical URL,
    content fingerprint, same-source headline with the same figures); link the same story from
    other sources (story_id); classify relevance (relevance.py); find the outbreak location
    (location.py; Nominatim geocoding only for relevant articles); save. A temporarily failed
    download is retried in later runs (up to MAX_FETCH_ATTEMPTS) instead of being rejected.
"""

import argparse
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from .config import CONFIG_DIR, Source, load_disease_config, load_sources
from .db import (Article, CollectionRun, ListingStatus, SeenLink, get_session_factory, last_successful_run,
                 utcnow)
from .discovery import discover, keyword_page_templates
from .extractor import content_fingerprint, extract_article
from .gazetteer import FOREIGN_FILE, INDIA_FILE
from .location import LocationResolver, build_resolver
from .location import apply as apply_location
from .logs import setup_logging
from .matcher import KeywordMatcher
from .official import collect_official
from .processing import STORY_WINDOW, StoryIndex, clean_headline, tag_content
from .relevance import RelevanceClassifier, apply, load_relevance_config
from .reprocess import reprocess

log = logging.getLogger(__name__)

METADATA_FIELDS = ("headline", "published_at", "excerpt", "meta_keywords")
MAX_FETCH_ATTEMPTS = 3                 # runs in which a temporarily failing article download is tried
CATCH_UP_AFTER = timedelta(hours=36)   # news sitemaps cover ~48 h; after a longer gap, read archives
CATCH_UP_MARGIN = timedelta(hours=12)  # overlap with the last successful run
KEYWORD_FILES = (CONFIG_DIR / "diseases.yaml",)
CONFIG_FILES = KEYWORD_FILES + (CONFIG_DIR / "relevance.yaml", CONFIG_DIR / "locations.yaml", INDIA_FILE, FOREIGN_FILE)


def _digest(paths) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:32]


def config_hash() -> str:
    """Fingerprint of the configuration: first half keywords, second half everything that decides tags,
    classification and location. A keyword change re-checks rejected links; any change re-processes."""
    return _digest(KEYWORD_FILES) + _digest(CONFIG_FILES)


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


def plan_deep_reads(session, sources: list[Source], diseases, cutoff: datetime,
                    force: bool) -> tuple[str, dict[str, datetime]]:
    """Which archive sitemaps / keyword-page templates to read in depth, and back to when."""
    done = set(session.scalars(select(ListingStatus.url).where(ListingStatus.deep_read_at.is_not(None))))
    last = last_successful_run(session)
    gap_cutoff = None
    if last and utcnow() - last.started_at > CATCH_UP_AFTER:
        gap_cutoff = max(cutoff, last.started_at - CATCH_UP_MARGIN)
    mode, deep = "incremental", {}
    for source in sources:
        for url in source.archive_sitemaps + keyword_page_templates(source, diseases):
            if force or url not in done:
                deep[url], mode = cutoff, "backfill"
            elif gap_cutoff:
                deep[url] = gap_cutoff
                mode = "catch-up" if mode == "incremental" else mode
    return mode, deep


class _Run:
    """State and statistics for one collection run (used from the main thread only)."""

    def __init__(self, session, matcher: KeywordMatcher, classifier: RelevanceClassifier,
                 locator: LocationResolver, keywords: dict[str, list[str]], cutoff: datetime):
        self.session = session
        self.matcher = matcher
        self.classifier = classifier
        self.locator = locator
        self.keywords = keywords
        self.cutoff = cutoff
        retry = (SeenLink.outcome == "fetch_failed") & (SeenLink.attempts < MAX_FETCH_ATTEMPTS)
        self.failed_attempts = {u: n or 1 for u, n in session.execute(select(SeenLink.url, SeenLink.attempts).where(retry))}
        self.known_urls = (set(session.scalars(select(Article.article_url)))
                           | set(session.scalars(select(Article.link_url)))
                           | set(session.scalars(select(SeenLink.url).where(~retry))))
        self.known_hashes = set(session.scalars(select(Article.content_hash).where(Article.content_hash.is_not(None))))
        self.stories = StoryIndex()
        recent = select(Article.source_name, Article.headline, Article.published_at, Article.story_id).where(
            or_(Article.published_at.is_(None), Article.published_at >= cutoff - STORY_WINDOW))
        for source_name, headline, published, story_id in session.execute(recent):
            self.stories.add(source_name, headline, published, story_id)
        self.stats = dict.fromkeys(["listings_ok", "listings_failed", "candidates", "already_known", "no_keyword",
                                    "too_old", "duplicates", "retry_later", "gave_up", "store_errors", "new",
                                    "text_ok", "text_missing", "shared_story"], 0)
        self.new_by_method: dict[str, int] = {}
        self.errors: list[dict] = []

    # ---------------------------------------------------------------- listings

    def record_listings(self, listings: list[dict]) -> None:
        now = utcnow()
        for listing in listings:
            row = self.session.get(ListingStatus, listing["url"]) or ListingStatus(
                url=listing["url"], source_name=listing["source"], kind=listing["kind"])
            if listing["ok"]:
                row.last_ok_at = now
                if listing["deep"]:
                    row.deep_read_at = now
            else:
                row.last_error_at, row.last_error = now, listing["error"]
                self.errors.append({"source": listing["source"], "url": listing["url"], "error": listing["error"]})
            self.stats["listings_ok" if listing["ok"] else "listings_failed"] += 1
            self.session.merge(row)
        self.session.commit()

    def prefilter(self, candidates: list[dict]) -> list[dict]:
        """Drop known links, old items, and listing items without a keyword (except keyword-page links)."""
        keep = []
        for c in merge_candidates(candidates):
            self.stats["candidates"] += 1
            if c["url"] in self.known_urls:
                self.stats["already_known"] += 1
            elif c.get("published_at") and c["published_at"] < self.cutoff:
                self.stats["too_old"] += 1
            elif "keyword_page" not in c["methods"] and not self.matcher.match(
                    c.get("headline"), c.get("excerpt"), c.get("meta_keywords"), urlsplit(c["url"]).path):
                self.stats["no_keyword"] += 1
            else:
                keep.append(c)
        return keep

    # ---------------------------------------------------------------- articles

    def _remember(self, url: str, outcome: str, attempts: int = 1) -> None:
        self.session.merge(SeenLink(url=url, outcome=outcome, attempts=attempts))
        try:
            self.session.commit()
        except IntegrityError:  # recorded meanwhile by a concurrent run
            self.session.rollback()
        if outcome != "fetch_failed" or attempts >= MAX_FETCH_ATTEMPTS:
            self.known_urls.add(url)

    @staticmethod
    def _canonical(source: Source, link: str, canonical: str | None) -> str:
        """The page's canonical URL, unless it points off-site or at a non-article page."""
        if not canonical or not source.owns(urlsplit(canonical).netloc):
            return link
        if source.article_pattern:
            pattern = re.compile(source.article_pattern)
            if not pattern.search(urlsplit(canonical).path) and pattern.search(urlsplit(link).path):
                return link
        return canonical

    def store(self, source: Source, c: dict, info: dict) -> None:
        link, status = c["url"], info["extraction_status"]
        gave_up = False
        if status == "failed":  # temporary problem: try again in a later run
            attempts = self.failed_attempts.get(link, 0) + 1
            if attempts < MAX_FETCH_ATTEMPTS:
                self.stats["retry_later"] += 1
                return self._remember(link, "fetch_failed", attempts)
            gave_up = True
            self.stats["gave_up"] += 1

        page_title = info.get("title")
        use_title = page_title and (set(c["methods"]) & {"keyword_page", "archive"} or not c.get("headline"))
        headline = clean_headline((page_title if use_title else c.get("headline")) or "", source.name)
        text = info.get("article_text")
        tags, keywords = tag_content(self.matcher, headline, text or c.get("excerpt"))
        canonical = self._canonical(source, link, info.get("canonical_url"))
        if c.get("published_at"):
            published, date_source = c["published_at"], "listing"
        else:
            published = info.get("published_at")
            date_source = "page" if published else None
        fingerprint = content_fingerprint(text)

        if not headline or not tags:
            self.stats["no_keyword"] += 1
            return self._remember(link, "fetch_failed" if gave_up else "no_keyword", MAX_FETCH_ATTEMPTS)
        if published and published < self.cutoff:
            self.stats["too_old"] += 1
            return self._remember(link, "too_old")
        if (canonical in self.known_urls or (fingerprint and fingerprint in self.known_hashes)
                or self.stories.is_duplicate(source.name, headline, published)):
            self.stats["duplicates"] += 1
            return self._remember(link, "duplicate")

        story_id = self.stories.find_story(source.name, headline, published)
        article = Article(
            headline=headline, source_name=source.name, article_url=canonical, link_url=link,
            collection_method=",".join(c["methods"]), listing_url=c.get("listing_url"),
            published_at=published, date_source=date_source, excerpt=c.get("excerpt"), article_text=text,
            extraction_status=status, content_hash=fingerprint, disease_tags=tags, matched_keywords=keywords,
            story_id=story_id)
        apply(article, self.classifier.classify(headline, text or c.get("excerpt"), canonical, published))
        apply_location(article, self.locator.locate_article(
            headline, text or c.get("excerpt"), article.relevant_diseases or tags, self.keywords,
            geocode=article.relevance == "relevant"))  # external geocoding only for relevant articles
        self.session.add(article)
        self.session.execute(delete(SeenLink).where(SeenLink.url == link))  # e.g. an earlier failed attempt
        try:
            self.session.flush()
            if article.story_id is None:
                article.story_id = article.article_id
            self.session.commit()
        except IntegrityError:  # stored meanwhile by a concurrent run
            self.session.rollback()
            self.stats["duplicates"] += 1
            return

        self.known_urls |= {canonical, link}
        if fingerprint:
            self.known_hashes.add(fingerprint)
        self.stories.add(source.name, headline, published, article.story_id)
        self.stats["new"] += 1
        self.stats["shared_story"] += story_id is not None
        self.stats["text_ok" if status == "ok" else "text_missing"] += 1
        for method in c["methods"]:
            self.new_by_method[method] = self.new_by_method.get(method, 0) + 1
        if status != "ok":
            log.info("Stored without text (%s): %s %s", status, link, info.get("error", ""))


def collect(session_factory=None, sources=None, disease_config=None, backfill: bool = False) -> dict:
    """One collection run over all enabled sources. Returns the run statistics."""
    session_factory = session_factory or get_session_factory()
    sources = [s for s in (sources if sources is not None else load_sources()) if s.enabled]
    disease_config = disease_config or load_disease_config()
    diseases, settings = disease_config.diseases, disease_config.settings
    matcher = KeywordMatcher(diseases)
    classifier = RelevanceClassifier(diseases, load_relevance_config())
    locator = build_resolver(session_factory)
    keywords = {d.name: d.keywords for d in diseases}
    current_hash = config_hash()

    with session_factory() as session:
        cutoff = utcnow() - timedelta(days=settings.lookback_days)
        mode, deep = plan_deep_reads(session, sources, diseases, cutoff, backfill)
        previous_hash = session.scalar(select(CollectionRun.config_hash).where(CollectionRun.config_hash.is_not(None))
                                       .order_by(CollectionRun.run_id.desc()).limit(1))
        run_record = CollectionRun(mode=mode, config_hash=current_hash)
        session.add(run_record)
        session.commit()
        log.info("Starting %s run (articles since %s UTC, %d listings in depth)",
                 mode, cutoff.strftime("%Y-%m-%d %H:%M"), len(deep))

        has_articles = session.scalar(select(Article.article_id).limit(1)) is not None
        if previous_hash != current_hash and (previous_hash is not None or has_articles):
            if (previous_hash or "")[:32] != current_hash[:32]:
                log.info("Keywords changed: re-checking links rejected for having no keyword")
                session.execute(delete(SeenLink).where(SeenLink.outcome == "no_keyword"))
                session.commit()
            if has_articles:
                log.info("Configuration changed: re-processing stored articles")
                reprocess(session, matcher, classifier, locator, keywords)

        run = _Run(session, matcher, classifier, locator, keywords, cutoff)
        try:
            # Each source goes through discovery -> extraction -> storage on its own, so a slow site
            # never holds up the others. Workers only talk to their own site (per-domain rate limit
            # in fetch.py); all database work happens here in the main thread.
            with ThreadPoolExecutor(max_workers=2 * max(1, len(sources))) as pool:
                pending = {pool.submit(discover, s, diseases, cutoff=cutoff, deep=deep,
                                       max_pages=settings.backfill_max_pages, known_urls=set(run.known_urls)):
                           ("discovered", s) for s in sources}
                listings: dict[str, list[dict]] = {}
                while pending:
                    future = next(as_completed(pending))
                    stage, source = pending.pop(future)
                    if stage == "discovered":
                        found = future.result()
                        listings[source.name] = found.listings
                        candidates = run.prefilter(found.candidates)
                        log.info("%-20s %5d links found, %4d to fetch", source.name,
                                 len(found.candidates), len(candidates))
                        pending[pool.submit(_extract_all, candidates)] = ("extracted", source)
                        continue
                    before = run.stats["new"]
                    for candidate, info in future.result():
                        try:
                            run.store(source, candidate, info)
                        except Exception as exc:  # one bad article must not stop the run
                            session.rollback()
                            run.stats["store_errors"] += 1
                            run.errors.append({"source": source.name, "url": candidate["url"],
                                               "error": f"store: {type(exc).__name__}: {exc}"[:500]})
                            log.exception("Could not store %s", candidate["url"])
                    # Only now (everything found has been stored) does a listing count as read; if
                    # the run stops earlier, the next run reads it again.
                    run.record_listings(listings.pop(source.name))
                    log.info("%-20s %4d new articles", source.name, run.stats["new"] - before)

            failed = run.stats["listings_failed"] + run.stats["store_errors"]
            run_record.status = "success" if not failed else ("partial" if run.stats["listings_ok"] else "failed")
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


def run_and_report(session_factory=None, backfill: bool = False) -> dict:
    """One collection run with a summary in the log."""
    session_factory = session_factory or get_session_factory()
    result = collect(session_factory, backfill=backfill)
    try:  # official reports are a separate stage: a slow or failing NCDC site never blocks news collection
        result["official"] = collect_official(session_factory)
    except Exception as exc:
        log.exception("Official reports failed")
        result["official"] = {"errors": [{"source": "official", "error": f"{type(exc).__name__}: {exc}"[:500]}]}
    with session_factory() as session:
        last = last_successful_run(session)
    log.info("Run finished: %s", result)
    log.info("Last successful refresh: %s UTC", last.finished_at if last else "never")
    return result


def main():
    parser = argparse.ArgumentParser(description="Collect outbreak news articles once.")
    parser.add_argument("--backfill", action="store_true",
                        help="read every archive sitemap and keyword page in depth over the lookback window")
    args = parser.parse_args()
    setup_logging()
    run_and_report(backfill=args.backfill)


if __name__ == "__main__":
    main()
