"""Load the YAML configuration files (news sources, diseases and settings)."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class Source:
    name: str
    domain: str                                              # e.g. www.thehindu.com
    enabled: bool = True
    rss: list[str] = field(default_factory=list)             # RSS/Atom feed URLs
    sitemaps: list[str] = field(default_factory=list)        # news sitemaps (latest ~48 h), read every run
    archive_sitemaps: list[str] = field(default_factory=list)  # sitemap indexes, read during backfill
    archive_child_pattern: str | None = None                 # regex selecting which index children to read
    keyword_pages: list[str] = field(default_factory=list)   # URL templates with {slug}/{query}/{page}
    article_pattern: str | None = None                       # regex on URL path identifying article links

    def owns(self, host: str) -> bool:
        """True if a link's host belongs to this source (with or without 'www.')."""
        bare = self.domain.removeprefix("www.")
        return host.removeprefix("www.") == bare


@dataclass
class Disease:
    name: str
    keywords: list[str]
    search_terms: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.search_terms and self.keywords:
            self.search_terms = [self.keywords[0]]


@dataclass
class Settings:
    lookback_days: int = 45            # backfill reaches this far back; older articles are ignored
    refresh_interval_minutes: int = 30
    backfill_max_pages: int = 10       # keyword-page pages read per term during backfill
    country: str = "India"


@dataclass
class DiseaseConfig:
    diseases: list[Disease] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sources(path: Path = CONFIG_DIR / "sources.yaml") -> list[Source]:
    """Return all sources in the file, including disabled ones."""
    return [Source(**s) for s in _read_yaml(path).get("sources") or []]


def load_disease_config(path: Path = CONFIG_DIR / "diseases.yaml") -> DiseaseConfig:
    data = _read_yaml(path)
    diseases = [
        Disease(name=d["name"], keywords=list(d.get("keywords") or []),
                search_terms=list(d.get("search_terms") or []))
        for d in data.get("diseases") or []
    ]
    return DiseaseConfig(diseases=diseases, settings=Settings(**(data.get("settings") or {})))
