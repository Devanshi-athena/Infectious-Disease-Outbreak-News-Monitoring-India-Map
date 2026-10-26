"""Article processing shared by collection and re-processing: headline cleanup, keyword tagging,
and recognising the same story (republished by one source, or carried by several sources)."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from .matcher import KeywordMatcher

SAME_SOURCE_SIMILARITY = 95   # one source, same numbers, >= 95% similar headline -> duplicate (republished)
CROSS_SOURCE_SIMILARITY = 90  # other source, same numbers, >= 90% similar headline -> same story
STORY_WINDOW = timedelta(days=3)


def clean_headline(title: str, source_name: str) -> str:
    """Remove site/section suffixes: "Dengue cases rise | Pune News - The Times of India" -> "Dengue cases rise"."""
    site = re.escape(re.sub(r"^the\s+", "", source_name.strip(), flags=re.I))
    site_suffix = rf"\s+[-–—|:]\s+(?:the\s+)?{site}\s*$"
    headline = re.sub(r"\s+", " ", title).strip()
    while True:
        stripped = re.sub(site_suffix, "", headline, flags=re.I)
        parts = re.split(r"\s+\|\s+", stripped)
        if len(parts) > 1 and (re.search(r"\bnews\b", parts[-1], re.I) or re.search(site, parts[-1], re.I)):
            stripped = " | ".join(parts[:-1])
        if stripped == headline:
            return headline
        headline = stripped


def normalise_headline(headline: str) -> str:
    return " ".join(re.findall(r"\w+", headline.lower()))


def headline_numbers(headline: str) -> tuple[str, ...]:
    """Numbers in a headline ("17,900" -> "17900"); two reports with different figures are different news."""
    return tuple(n.replace(",", "") for n in re.findall(r"\d[\d,]*\d|\d", headline))


def tag_content(matcher: KeywordMatcher, headline: str, body: str | None) -> tuple[list[str], dict]:
    """Disease tags and the exact keywords behind them, from the headline and article text only."""
    found = matcher.matched_keywords(headline=headline, text=body)
    return list(found), found


@dataclass
class _Story:
    source: str
    norm: str
    numbers: tuple[str, ...]
    published: datetime | None
    story_id: int | None


class StoryIndex:
    """Recent headlines, to recognise duplicates (same source) and shared stories (other sources)."""

    def __init__(self):
        self._entries: list[_Story] = []

    def add(self, source: str, headline: str, published: datetime | None, story_id: int | None) -> None:
        self._entries.append(_Story(source, normalise_headline(headline), headline_numbers(headline),
                                    published, story_id))

    def _similar(self, headline: str, published: datetime | None, threshold: int):
        """Entries with the same figures, published within STORY_WINDOW, and a similar headline."""
        norm, numbers = normalise_headline(headline), headline_numbers(headline)
        for e in self._entries:
            if e.numbers != numbers:
                continue
            if published and e.published and abs(published - e.published) > STORY_WINDOW:
                continue
            if fuzz.ratio(norm, e.norm) >= threshold:
                yield e

    def is_duplicate(self, source: str, headline: str, published: datetime | None) -> bool:
        """Same source already has this headline (same figures, within 3 days)."""
        return any(e.source == source for e in self._similar(headline, published, SAME_SOURCE_SIMILARITY))

    def find_story(self, source: str, headline: str, published: datetime | None) -> int | None:
        """story_id of the same story from another source (same figures, within 3 days), if any."""
        return next((e.story_id for e in self._similar(headline, published, CROSS_SOURCE_SIMILARITY)
                     if e.source != source and e.story_id is not None), None)
