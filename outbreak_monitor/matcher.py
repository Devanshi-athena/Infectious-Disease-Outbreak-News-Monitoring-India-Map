"""Match text against the configured disease keywords."""

import re

from .config import Disease


class KeywordMatcher:
    def __init__(self, diseases: list[Disease]):
        # Whole-word, case-insensitive; spaces in a phrase also match hyphens/newlines.
        self._patterns = [
            (d.name, re.compile(
                "|".join(r"(?<!\w)" + r"[\s-]+".join(map(re.escape, kw.split())) + r"(?!\w)" for kw in d.keywords),
                re.IGNORECASE,
            ))
            for d in diseases if d.keywords
        ]

    def match(self, *texts: str | None) -> list[str]:
        """Return the names of diseases whose keywords appear in any of the texts."""
        text = " ".join(t for t in texts if t)
        return [name for name, pattern in self._patterns if pattern.search(text)]
