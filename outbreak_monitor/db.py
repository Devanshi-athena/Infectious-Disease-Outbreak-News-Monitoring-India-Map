"""Database model and session setup (SQLite now, PostgreSQL-compatible later).

All datetimes are stored as naive UTC.
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "outbreaks.db"
SQLITE_LOCK_TIMEOUT_SECONDS = 30  # wait this long for another process's write instead of failing

_default_factory: sessionmaker | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    # Collection
    article_id: Mapped[int] = mapped_column(primary_key=True)
    headline: Mapped[str] = mapped_column(Text)                          # cleaned (no "| City News - Site" suffix)
    source_name: Mapped[str] = mapped_column(String(200), index=True)
    article_url: Mapped[str] = mapped_column(String(2000), unique=True)  # canonical URL (from the page if valid)
    link_url: Mapped[str] = mapped_column(String(2000), index=True)      # link as found in the listing (normalised)
    collection_method: Mapped[str] = mapped_column(String(60))           # e.g. "rss,keyword_page" (all that found it)
    listing_url: Mapped[str | None] = mapped_column(String(2000))        # feed, sitemap or keyword page it came from
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # UTC
    date_source: Mapped[str | None] = mapped_column(String(20))          # listing (feed/sitemap) or page
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    excerpt: Mapped[str | None] = mapped_column(Text)  # RSS summary/description

    # Full-text extraction
    article_text: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str | None] = mapped_column(String(20))  # ok/no_text/blocked/unavailable/failed
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # fingerprint of article_text

    # Keyword matching (headline and article text only)
    disease_tags: Mapped[list | None] = mapped_column(JSON)      # every disease whose keywords matched
    matched_keywords: Mapped[dict | None] = mapped_column(JSON)  # {"Dengue": {"headline": [...], "text": [...]}}

    # Automatic relevance and event classification (relevance.py)
    relevance: Mapped[str | None] = mapped_column(String(20), index=True)  # relevant/irrelevant
    content_type: Mapped[str | None] = mapped_column(String(30))  # outbreak_report/public_health_response/research/...
    event_status: Mapped[str | None] = mapped_column(String(20))  # new/suspected/historical/unclear
    relevant_diseases: Mapped[list | None] = mapped_column(JSON)  # diseases the reported event is about
    relevance_reasons: Mapped[list | None] = mapped_column(JSON)  # why the classifier decided this

    # Same story from several publishers shares a story_id (= article_id of its first article)
    story_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Manual review by a user; never changed by the classifier
    relevance_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/relevant/irrelevant

    # Outbreak location (location.py)
    country: Mapped[str | None] = mapped_column(String(100), index=True)  # "India", a foreign country, or empty
    state: Mapped[str | None] = mapped_column(String(100), index=True)
    district: Mapped[str | None] = mapped_column(String(100))
    locality: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)    # of the most specific supported level
    longitude: Mapped[float | None] = mapped_column(Float)
    location_level: Mapped[str | None] = mapped_column(String(20))       # state/district/locality/country
    location_text: Mapped[str | None] = mapped_column(Text)              # the article's own words for the place
    location_confidence: Mapped[str | None] = mapped_column(String(20))  # high/medium/low/none
    location_review: Mapped[bool | None] = mapped_column(Boolean)        # needs a person to check
    location_source: Mapped[str | None] = mapped_column(String(20))      # reference / nominatim
    location_reasons: Mapped[list | None] = mapped_column(JSON)
    location_candidates: Mapped[list | None] = mapped_column(JSON)       # every Indian place considered


class SeenLink(Base):
    """Links that were checked but not stored, so later runs don't download them again.
    fetch_failed links are retried until `attempts` reaches the limit."""

    __tablename__ = "seen_links"

    url: Mapped[str] = mapped_column(String(2000), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(20))  # no_keyword/too_old/duplicate/fetch_failed
    attempts: Mapped[int | None] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GeocodeCache(Base):
    """Nominatim answers, so each place is looked up once ({} = looked up, nothing found)."""

    __tablename__ = "geocode_cache"

    query: Mapped[str] = mapped_column(String(500), primary_key=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ListingStatus(Base):
    """Last result of each listing (feed, sitemap, archive, keyword-page template).
    A listing that has never been read in depth (deep_read_at is empty) is read in depth on the
    next run: archives over the lookback window, keyword pages over several pages."""

    __tablename__ = "listing_status"

    url: Mapped[str] = mapped_column(String(2000), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # rss/sitemap/archive/keyword_page
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    deep_read_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class CollectionRun(Base):
    """One collection run (scheduled or manual), for monitoring and 'last refresh' display."""

    __tablename__ = "collection_runs"

    run_id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="incremental")  # backfill/catch-up/incremental
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/partial/failed
    config_hash: Mapped[str | None] = mapped_column(String(64))  # of diseases.yaml + relevance.yaml
    stats: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)  # [{"source": ..., "url": ..., "error": ...}]


def last_successful_run(session) -> CollectionRun | None:
    """Most recent run that completed (fully or with some failed listings)."""
    return session.scalars(
        select(CollectionRun).where(CollectionRun.status.in_(["success", "partial"]))
        .order_by(CollectionRun.finished_at.desc()).limit(1)
    ).first()


def get_session_factory(db_url: str | None = None) -> sessionmaker:
    """Session factory for db_url, or for data/outbreaks.db (created once per process)."""
    global _default_factory
    if db_url is None and _default_factory is not None:
        return _default_factory
    url = db_url or f"sqlite:///{DEFAULT_DB_PATH}"
    if db_url is None:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"timeout": SQLITE_LOCK_TIMEOUT_SECONDS} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    factory = sessionmaker(engine)
    if db_url is None:
        _default_factory = factory
    return factory


def _add_missing_columns(engine) -> None:
    """Add columns that were added to the models after the database was created (nullable only)."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    col_type = column.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'))
