"""Database model and session setup (SQLite now, PostgreSQL-compatible later).

All datetimes are stored as naive UTC.
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, DateTime, Float, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "outbreaks.db"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    # Collection
    article_id: Mapped[int] = mapped_column(primary_key=True)
    headline: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(200), index=True)
    article_url: Mapped[str] = mapped_column(String(2000), unique=True)  # canonical URL (from the page if available)
    link_url: Mapped[str] = mapped_column(String(2000), index=True)      # link as found in the listing (normalised)
    collection_method: Mapped[str] = mapped_column(String(60))           # e.g. "rss,keyword_page" (all that found it)
    listing_url: Mapped[str | None] = mapped_column(String(2000))        # feed, sitemap or keyword page it came from
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    excerpt: Mapped[str | None] = mapped_column(Text)  # RSS summary/description

    # Full-text extraction
    article_text: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str | None] = mapped_column(String(20))  # ok/no_text/blocked/failed
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # fingerprint of article_text

    # Phase 3: relevance
    disease_tags: Mapped[list | None] = mapped_column(JSON)
    relevance_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/relevant/irrelevant
    event_status: Mapped[str | None] = mapped_column(String(20))  # new/suspected/historical/unclear

    # Phase 4: location
    location_text: Mapped[str | None] = mapped_column(Text)  # original location phrase
    state: Mapped[str | None] = mapped_column(String(100), index=True)
    district: Mapped[str | None] = mapped_column(String(100))
    locality: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location_confidence: Mapped[str | None] = mapped_column(String(20))


class SeenLink(Base):
    """Links that were downloaded but not stored, so later runs don't download them again."""

    __tablename__ = "seen_links"

    url: Mapped[str] = mapped_column(String(2000), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(20))  # no_keyword/too_old/duplicate
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CollectionRun(Base):
    """One collection run (scheduled or manual), for monitoring and 'last refresh' display."""

    __tablename__ = "collection_runs"

    run_id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="incremental")  # backfill/incremental
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/partial/failed
    stats: Mapped[dict | None] = mapped_column(JSON)
    errors: Mapped[list | None] = mapped_column(JSON)  # [{"url": ..., "error": ...}]


def last_successful_run(session) -> CollectionRun | None:
    """Most recent run that completed (fully or with some failed feeds)."""
    return session.scalars(
        select(CollectionRun).where(CollectionRun.status.in_(["success", "partial"]))
        .order_by(CollectionRun.finished_at.desc()).limit(1)
    ).first()


def get_session_factory(db_url: str | None = None) -> sessionmaker:
    if db_url is None:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{DEFAULT_DB_PATH}"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine)
