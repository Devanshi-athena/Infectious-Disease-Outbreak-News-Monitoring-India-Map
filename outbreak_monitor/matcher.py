"""Match text against the configured disease keywords."""

import re

from .config import Disease


def phrase_pattern(phrase: str) -> str:
    """Regex for a phrase as whole words (use case-insensitively); spaces also match hyphens/newlines.
    A trailing * matches any word ending (e.g. "hospitali*")."""
    wildcard = phrase.endswith("*")
    words = phrase.rstrip("*").split()
    body = r"[\s-]+".join(map(re.escape, words))
    return r"(?<!\w)" + body + (r"\w*" if wildcard else r"(?!\w)")


def compile_phrases(phrases: list[str]) -> re.Pattern | None:
    return re.compile("|".join(map(phrase_pattern, phrases)), re.IGNORECASE) if phrases else None


class KeywordMatcher:
    def __init__(self, diseases: list[Disease]):
        self.patterns = {d.name: compile_phrases(d.keywords) for d in diseases if d.keywords}
        self._keywords = {d.name: [(kw, re.compile(phrase_pattern(kw), re.IGNORECASE)) for kw in d.keywords]
                          for d in diseases if d.keywords}

    def match(self, *texts: str | None) -> list[str]:
        """Names of diseases whose keywords appear in any of the texts."""
        text = " ".join(t for t in texts if t)
        return [name for name, pattern in self.patterns.items() if pattern.search(text)]

    def matched_keywords(self, **fields: str | None) -> dict[str, dict[str, list[str]]]:
        """Exactly which configured keywords were found in which field, per disease, e.g.
        matched_keywords(headline=..., text=...) -> {"Dengue": {"headline": ["dengue"], "text": ["dengue cases"]}}"""
        found: dict[str, dict[str, list[str]]] = {}
        for name, keywords in self._keywords.items():
            for field, value in fields.items():
                hits = [kw for kw, pattern in keywords if value and pattern.search(value)]
                if hits:
                    found.setdefault(name, {})[field] = hits
        return found
