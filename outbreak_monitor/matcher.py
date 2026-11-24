"""Match text against the configured disease keywords (English and native-language).

Uses the `regex` module: unlike `re`, its \\w treats the vowel signs and other combining marks of Indian
scripts as part of a word, so word boundaries work in Hindi, Tamil, Bengali etc. as they do in English."""

import unicodedata
from functools import lru_cache

import regex as re

from .config import Disease

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))  # joiners vary between publishers


def normalise_text(text: str) -> str:
    """NFC form without zero-width joiners, so differently encoded spellings of one word match."""
    return unicodedata.normalize("NFC", text).translate(_ZERO_WIDTH)


def phrase_pattern(phrase: str) -> str:
    """Regex for a phrase as whole words (use case-insensitively); spaces also match hyphens/newlines
    and quote marks ("'Mystery' fever" matches "mystery fever"). A trailing * matches any word ending
    (e.g. "hospitali*"); "*!xy" also requires that the ending does not start with x or y. Alternatives of one
    word are separated by | ("girl|boy dies of bird flu")."""
    phrase, _, not_followed_by = phrase.partition("!")
    wildcard = phrase.endswith("*")
    words = normalise_text(phrase.rstrip("*")).split()
    words = ["(?:" + "|".join(map(re.escape, w.split("|"))) + ")" if "|" in w else re.escape(w) for w in words]
    body = r"""[\s\-'‘’"“”]*[\s-][\s\-'‘’"“”]*""".join(words)
    if not_followed_by:
        body += "(?![" + re.escape(normalise_text(not_followed_by)) + "])"
    return r"(?<!\w)" + body + (r"\w*" if wildcard else r"(?!\w)")


def _first_words(phrase: str) -> tuple[list[str], bool]:
    """The alternatives of a phrase's first word ("girl|boy dies of ..." -> girl, boy), and whether
    the word may run on (a one-word keyword with a trailing *, "hospitali*"); otherwise it ends at a word
    boundary, as in phrase_pattern."""
    head = phrase.partition("!")[0]
    words = normalise_text(head.rstrip("*")).split()
    if not words:
        return [], False
    return words[0].split("|"), len(words) == 1 and head.endswith("*")


def trie_pattern(words) -> str:
    """One regex matching any of the words, with shared beginnings merged (a trie): far faster than a plain
    alternation of hundreds of words."""
    trie: dict = {}
    for word in words:
        node = trie
        for ch in word:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict) -> str:
        branches = [re.escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if "" in node else body

    return build(trie)


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
        self._phrases = [phrase_pattern(kw) for kw, _ in entries]
        self.pattern = re.compile("|".join(f"(?P<k{i}>{p})" for i, p in enumerate(self._phrases)),
                                  re.IGNORECASE) if entries else None
        # Speed (the collector checks tens of thousands of sitemap entries per run; one pass of the full pattern of
        # all keywords costs up to 50 ms on a long entry):
        # 1. the gate: any keyword begins with one of these words; most texts contain none and are passed over.
        # 2. for a text that passes, only the keywords whose first word occurs in it are tried, in the same order
        #    as the full pattern, so the result is the same (a keyword whose first word is absent cannot match).
        first: dict[tuple[str, bool], list[int]] = {}   # (first word, may run on) -> keywords beginning with it
        for i, (kw, _) in enumerate(entries):
            words, runs_on = _first_words(kw)
            for w in words:
                first.setdefault((w, runs_on), []).append(i)
        whole = sorted(w for w, runs_on in first if not runs_on)
        prefix = sorted(w for w, runs_on in first if runs_on)
        parts = ([f"(?:{trie_pattern(whole)})" + r"(?!\w)"] if whole else []) + \
                ([trie_pattern(prefix)] if prefix else [])
        self.gate = re.compile(r"(?<!\w)(?:" + "|".join(parts) + ")", re.IGNORECASE) if parts else None
        self._first_patterns = [(re.compile(r"(?<!\w)" + re.escape(w) + ("" if runs_on else r"(?!\w)"),
                                            re.IGNORECASE), ids) for (w, runs_on), ids in first.items()]
        self._subpattern = lru_cache(maxsize=2048)(self._compile_subset)

    def _compile_subset(self, ids: frozenset[int]) -> re.Pattern:
        return re.compile("|".join(f"(?P<k{i}>{self._phrases[i]})" for i in sorted(ids)), re.IGNORECASE)

    def _pattern_for(self, text: str):
        """The pattern to run on text: None if no keyword can occur in it, else the keywords that can."""
        if self.gate is None:
            return self.pattern
        if not self.gate.search(text):
            return None
        ids = frozenset(i for first, members in self._first_patterns if first.search(text) for i in members)
        return self._subpattern(ids) if ids else None

    def mentions(self, text: str | None) -> list[tuple[str, str]]:
        """(disease, keyword) for every keyword mention in the text, without overlaps."""
        if not text or self.pattern is None:
            return []
        text = normalise_text(text)
        pattern = self._pattern_for(text)
        if pattern is None:
            return []
        found = []
        for m in pattern.finditer(text):
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
