"""Load the YAML configuration files (news sources, diseases and settings)."""

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SOURCE_FILES = (CONFIG_DIR / "sources.yaml", CONFIG_DIR / "regional_sources.yaml")

# Languages of the news sources (ISO 639-1 code -> name). Every language except English is translated.
LANGUAGES = {"en": "English", "hi": "Hindi", "bn": "Bengali", "mr": "Marathi", "te": "Telugu", "ta": "Tamil",
             "gu": "Gujarati", "ur": "Urdu", "kn": "Kannada", "or": "Odia", "ml": "Malayalam", "pa": "Punjabi",
             "as": "Assamese"}
SHORT_NATIVE_KEYWORD = 3   # native keywords of up to 3 characters must be whole words (no suffixes)


@dataclass
class Source:
    name: str
    domain: str                                              # e.g. www.thehindu.com
    language: str = "en"                                     # ISO 639-1 code (config.LANGUAGES)
    enabled: bool = True
    rss: list[str] = field(default_factory=list)             # RSS/Atom feed URLs
    sitemaps: list[str] = field(default_factory=list)        # news sitemaps (latest ~48 h), read every run
    archive_sitemaps: list[str] = field(default_factory=list)  # sitemap indexes, read during backfill
    archive_child_pattern: str | None = None                 # regex selecting which index children to read
    keyword_pages: list[str] = field(default_factory=list)   # URL templates with {slug}/{query}/{page}
    article_pattern: str | None = None                       # regex on URL path identifying article links

    def __post_init__(self):
        if self.language not in LANGUAGES:
            raise ValueError(f"source {self.name!r}: unknown language {self.language!r}")

    def owns(self, host: str) -> bool:
        """True if a link's host belongs to this source (with or without 'www.')."""
        bare = self.domain.removeprefix("www.")
        return host.removeprefix("www.") == bare


@dataclass
class OfficialSource:
    """An official outbreak-report series (config/sources.yaml -> official_reports)."""
    name: str
    type: str          # idsp_weekly
    listing: str       # page listing the report files
    weeks: int = 12    # most recent weekly reports to keep
    enabled: bool = True


def native_pattern(word: str) -> str:
    """A native-language keyword as a matcher phrase: Indian languages attach case endings and postpositions
    to the word ("डेंगू" -> "डेंगूने", "டெங்கு" -> "டெங்குவால்"), so endings are allowed (trailing *), except for
    short words and words written with a trailing "=", which would otherwise match the start of unrelated
    words (Bengali "হাম" in "হামলা", "নিপা=" not in "নিপাত")."""
    exact = word.strip().endswith("=")
    word = " ".join(word.strip().rstrip("=").split())
    if exact or len(word.replace(" ", "")) <= SHORT_NATIVE_KEYWORD:
        return word
    # A final virama (vowel-killer sign) is dropped when an ending is attached: Tamil காய்ச்சல் + ஆல் ->
    # காய்ச்சலால், Malayalam കോവിഡ് -> കോവിഡിന്. Without it, the word is a prefix of all its forms.
    while word and unicodedata.name(word[-1], "").endswith("SIGN VIRAMA"):
        word = word[:-1]
    return word + "*"


@dataclass
class Disease:
    name: str
    keywords: list[str]
    search_terms: list[str] | None = None   # None: the first keyword; []: no topic-page search
    category: str = "Other"   # disease group, e.g. "Vector-borne"
    superseded_by: list[str] = field(default_factory=list)  # dropped when one of these is also found
    native_keywords: dict[str, list[str]] = field(default_factory=dict)  # language code -> names in that language
    code: str = ""            # stable identifier, e.g. "DENGUE" (names may be reworded, codes never change)
    idsp: str = ""            # where the condition is on the IDSP list, e.g. "P, L" or "S 2.9"
    # False: the phrases are recognised (so "influenza" inside "avian influenza" is not human flu) but never
    # tag an article, are not searched for and are not shown.
    monitored: bool = True

    def __post_init__(self):
        if not self.monitored:
            self.search_terms = []
        elif self.search_terms is None:
            self.search_terms = self.keywords[:1]
        if unknown := set(self.native_keywords) - set(LANGUAGES):
            raise ValueError(f"disease {self.name!r}: unknown language(s) {sorted(unknown)}")

    @property
    def native_patterns(self) -> list[str]:
        """Native-language keywords of every language, as matcher phrases."""
        return [native_pattern(w) for words in self.native_keywords.values() for w in words]


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
    # Native-language words that make a regional article worth translating although they name no single
    # disease ("fever", "epidemic", "jaundice"). They never tag a disease themselves, and apply only to
    # sources in their own language (Marathi "साथ" is an epidemic, Hindi "साथ" means "with").
    screening_words: dict[str, list[str]] = field(default_factory=dict)

    @property
    def monitored(self) -> list[Disease]:
        """The diseases that tag articles and are shown (the IDSP list), in configuration order."""
        return [d for d in self.diseases if d.monitored]

    @property
    def screening_patterns(self) -> dict[str, list[str]]:
        """Language code -> screening words as matcher phrases."""
        return {lang: [native_pattern(w) for w in words] for lang, words in self.screening_words.items()}


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_sources(paths=SOURCE_FILES) -> list[Source]:
    """Return all sources in the files (English and regional-language), including disabled ones."""
    paths = [paths] if isinstance(paths, (str, Path)) else paths
    return [Source(**s) for path in paths if Path(path).exists() for s in _read_yaml(path).get("sources") or []]


def load_official_sources(path: Path = CONFIG_DIR / "sources.yaml") -> list[OfficialSource]:
    return [OfficialSource(**s) for s in _read_yaml(path).get("official_reports") or []]


def load_disease_config(path: Path = CONFIG_DIR / "diseases.yaml") -> DiseaseConfig:
    data = _read_yaml(path)
    diseases = [
        Disease(name=d["name"], keywords=list(d.get("keywords") or []),
                search_terms=list(d["search_terms"] or []) if "search_terms" in d else None,
                category=d.get("category") or "Other",
                superseded_by=list(d.get("superseded_by") or []),
                native_keywords={lang: list(words or []) for lang, words in (d.get("native_keywords") or {}).items()},
                code=str(d.get("code") or ""), idsp=str(d.get("idsp") or ""), monitored=d.get("monitored", True))
        for d in data.get("diseases") or []
    ]
    names, codes = [d.name for d in diseases], [d.code for d in diseases if d.code]
    if len(set(names)) != len(names) or len(set(codes)) != len(codes):
        raise ValueError("diseases.yaml: disease names and codes must be unique")
    if unknown := {s for d in diseases for s in d.superseded_by} - set(names):
        raise ValueError(f"diseases.yaml: superseded_by names unknown diseases {sorted(unknown)}")
    screening = {lang: list(words or []) for lang, words in (data.get("screening_words") or {}).items()}
    if unknown := set(screening) - set(LANGUAGES):
        raise ValueError(f"screening_words: unknown language(s) {sorted(unknown)}")
    return DiseaseConfig(diseases=diseases, settings=Settings(**(data.get("settings") or {})),
                         screening_words=screening)
