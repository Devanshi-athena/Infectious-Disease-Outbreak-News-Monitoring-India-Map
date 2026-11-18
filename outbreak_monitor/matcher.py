"""Match text against the configured disease keywords (English and native-language).

Uses the `regex` module: unlike `re`, its \\w treats the vowel signs and other combining marks of Indian
scripts as part of a word, so word boundaries work in Hindi, Tamil, Bengali etc. as they do in English."""

import unicodedata

import regex as re

from .config import Disease

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))  # joiners vary between publishers


def normalise_text(text: str) -> str:
    """NFC form without zero-width joiners, so differently encoded spellings of one word match."""
    return unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)


def phrase_pattern(phrase: str) -> str:
    """Regex for a phrase as whole words (use case-insensitively); spaces also match hyphens/newlines
    and quote marks ("'Mystery' fever" matches "mystery fever"). A trailing * matches any word ending
    (e.g. "hospitali*")."""
    wildcard = phrase.endswith("*")
    words = normalise_text(phrase.rstrip("*")).split()
    body = r"""[\s\-'‘’"“”]*[\s-][\s\-'‘’"“”]*""".join(map(re.escape, words))
    return r"(?<!\w)" + body + (r"\w*" if wildcard else r"(?!\w)")


def compile_phrases(phrases: list[str]) -> re.Pattern | None:
    return re.compile("|".join(map(phrase_pattern, phrases)), re.IGNORECASE) if phrases else None


class KeywordMatcher:
    """Finds disease keywords. All keywords are matched together and, where two overlap, the earliest and
    then the longest wins: "avian influenza" is bird flu only (not also seasonal influenza), and
    "bird flu outbreak" does not also count as "flu outbreak"."""

    def __init__(self, diseases: list[Disease]):
        # English keywords and native-language names (config.native_pattern) are matched alike.
        # Phrases of diseases that are not monitored are matched too (so they take precedence over shorter
        # keywords inside them) but never reported.
        self.order = [d.name for d in diseases if d.monitored and (d.keywords or d.native_keywords)]
        self.ignored = {d.name for d in diseases if not d.monitored}
        self.superseded_by = {d.name: set(d.superseded_by) for d in diseases if d.superseded_by}
        entries = sorted(((kw, d.name) for d in diseases for kw in d.keywords + d.native_patterns),
                         key=lambda e: -len(e[0]))
        self._entries = entries
        self.pattern = re.compile("|".join(f"(?P<k{i}>{phrase_pattern(kw)})" for i, (kw, _) in enumerate(entries)),
                                  re.IGNORECASE) if entries else None

    def mentions(self, text: str | None) -> list[tuple[str, str]]:
        """(disease, keyword) for every keyword mention in the text, without overlaps."""
        if not text or self.pattern is None:
            return []
        found = []
        for m in self.pattern.finditer(normalise_text(text)):
            keyword, disease = self._entries[int(m.lastgroup[1:])]
            if disease not in self.ignored:
                found.append((disease, keyword))
        return found

    def resolve(self, names: list[str]) -> list[str]:
        """Drop a disease when one that supersedes it is also present: "Jaundice (cause not confirmed)"
        is dropped when hepatitis A or E is named."""
        present = set(names)
        return [n for n in names if not self.superseded_by.get(n, set()) & present]

    def match(self, *texts: str | None) -> list[str]:
        """Names of diseases whose keywords appear in any of the texts."""
        found = {d for d, _ in self.mentions(" ".join(t for t in texts if t))}
        return self.resolve([name for name in self.order if name in found])

    def matched_keywords(self, **fields: str | None) -> dict[str, dict[str, list[str]]]:
        """Exactly which configured keywords were found in which field, per disease, e.g.
        matched_keywords(headline=..., text=...) -> {"Dengue": {"headline": ["dengue"], "text": ["dengue fever"]}}"""
        found: dict[str, dict[str, list[str]]] = {}
        for field, value in fields.items():
            for disease, keyword in self.mentions(value):
                hits = found.setdefault(disease, {}).setdefault(field, [])
                if keyword not in hits:
                    hits.append(keyword)
        return {name: found[name] for name in self.order if name in found}
