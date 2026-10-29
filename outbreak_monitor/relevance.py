"""Relevance (does the article report an outbreak/event?) and event classification.

Rule-based and explainable; cue words live in config/relevance.yaml.

    content_type   outbreak_report         cases, deaths or an outbreak are reported
                   public_health_response  a response to disease in an area (fogging, alert, drive...)
                   research                study, trial, vaccine or drug news
                   awareness               explainer, tips, symptoms
                   passing_mention         the disease is not central to the article
                   other                   central but neither event nor response, or excluded URL section
                   no_keyword              (set by reprocess.py) no configured keyword in headline or text
    relevance      relevant for outbreak_report / public_health_response, else irrelevant
    event_status   new / suspected / historical / unclear (relevant articles only)

A disease is "central" if it is in the headline, or mentioned in 3+ sentences, or in the lead
(first 3 sentences) and 2+ sentences. Cues are only looked for in sentences that mention a
central disease, so a passing mention elsewhere in the article does not count.

Stored articles are re-classified automatically when config/diseases.yaml or
config/relevance.yaml changes, or manually with:  python -m outbreak_monitor.reprocess
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import spacy

from .config import CONFIG_DIR, Disease, _read_yaml
from .matcher import compile_phrases
from .processing import split_paragraphs

CUE_KINDS = ("event", "response", "suspected", "confirmed", "research", "awareness", "historical", "current",
             "crime")
LEAD_SENTENCES = 3
MAX_TEXT_CHARS = 20_000
MIN_SECONDARY_EVENTS = 2   # outbreak sentences needed for a disease not named in the headline

# A count: "40", "2,500", "1,00,000" or a number word. Not part of a name or code ("COVID-19",
# "H1N1"), not a decimal, and not a year ("in 2019,").
_DIGITS = r"(?<![\w.-])(?!(?:19|20)\d\d(?!\d|,\d{3}))\d+(?:,\d{2,3})*"
_NUMBER = (rf"(?:{_DIGITS}|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
           rf"dozens?|hundreds?|thousands?)\b)")
_COUNTED = (r"(?:cases?|patients?|deaths?|infections?|infected|people|persons|children|kids|students|residents|"
            r"villagers|\w*birds|chickens|hens|ducks|crows|pigs|animals)")
_TREND = r"(?:jump|rise|rising|climb|cross|reach|touch|surge|spike|soar|spiral|dip|drop|decline|fall|doubl|tripl)\w*"
_ILL = r"(?:hospitali[sz]ed|admitted|infected|tested positive|test positive|fell ill|took ill|died|dead|affected)"
# "40 new dengue cases", "2,500 chickens", "87 jawans hospitalised", "tally rises to 70",
# "count crossed 500", "cases of X in Y jump 31%"
CASE_COUNT = re.compile(
    rf"{_NUMBER}\s+(?:[\w-]+\s+){{0,3}}?{_COUNTED}\b"
    rf"|{_NUMBER}\s+(?:[\w-]+\s+){{0,2}}?{_ILL}\b"
    rf"|\b(?:tally|toll|count|cases)\s+(?:[\w-]+\s+){{0,3}}?(?:to|at|of|crosse?d?|reache?d?|touche?d?)\s+\d"
    rf"|\bcases\s+(?:[\w,'’-]+\s+){{0,6}}?{_TREND}",
    re.IGNORECASE)
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")

_nlp = None


def _sentences(text: str) -> list[str]:
    """Split article text into sentences (paragraphs first, then spaCy's sentence segmenter)."""
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm", exclude=["ner", "lemmatizer", "attribute_ruler", "tagger"])
    return [s.text.strip() for doc in _nlp.pipe(split_paragraphs(text, MAX_TEXT_CHARS))
            for s in doc.sents if s.text.strip()]


def load_relevance_config(path=CONFIG_DIR / "relevance.yaml") -> dict:
    return _read_yaml(path)


@dataclass
class Classification:
    relevance: str
    content_type: str
    event_status: str | None = None
    relevant_diseases: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class RelevanceClassifier:
    def __init__(self, diseases: list[Disease], cues: dict):
        self.diseases = {d.name: compile_phrases(d.keywords) for d in diseases if d.keywords}
        self.cues = {kind: compile_phrases(cues.get(kind) or []) for kind in CUE_KINDS}
        self.excluded_sections = {s.lower() for s in cues.get("excluded_url_sections") or []}

    def _cue(self, kind: str, text: str) -> str | None:
        pattern = self.cues[kind]
        m = pattern.search(text) if pattern else None
        return m.group(0) if m else None

    def _event(self, text: str) -> str | None:
        m = CASE_COUNT.search(text)
        return m.group(0) if m else self._cue("event", text)

    def classify(self, headline: str, text: str | None, url: str = "",
                 published_at: datetime | None = None) -> Classification:
        sents = [headline] + (_sentences(text) if text else [])
        reasons = []

        # Per disease: where it is mentioned and what the mentioning sentences say.
        central, event_d, response_d, event_sents = [], [], [], set()
        in_headline, event_count = set(), {}
        for name, pattern in self.diseases.items():
            idx = [i for i, s in enumerate(sents) if pattern.search(s)]
            if not idx:
                continue
            in_head = idx[0] == 0
            body = [i for i in idx if i > 0]
            in_lead = any(i <= LEAD_SENTENCES for i in body)
            if not (in_head or len(body) >= 3 or (in_lead and len(body) >= 2)):
                continue
            central.append(name)
            if in_head:
                in_headline.add(name)
            reasons.append(f"{name}: {'in headline, ' if in_head else ''}{len(body)} sentence(s)")
            events = [(i, e) for i in idx if (e := self._event(sents[i]))]
            event_count[name] = len(events)
            if events:
                event_d.append(name)
                event_sents.update(i for i, _ in events)
                reasons.append(f"{name} event: '{events[0][1]}'")
            if response := next((r for i in idx if (r := self._cue("response", sents[i]))), None):
                response_d.append(name)
                reasons.append(f"{name} response: '{response}'")

        section = next((p for p in urlsplit(url).path.lower().split("/")[:3] if p in self.excluded_sections), None)
        if section:
            return Classification("irrelevant", "other", reasons=reasons + [f"URL section '{section}'"])
        if not central:
            return Classification("irrelevant", "passing_mention", reasons=["no disease is central to the article"])

        if cue := self._cue("crime", headline):
            return Classification("irrelevant", "other", reasons=reasons + [f"crime headline: '{cue}'"])
        head_event = self._event(headline)
        if not head_event and (cue := self._cue("research", headline)):
            return Classification("irrelevant", "research", reasons=reasons + [f"research headline: '{cue}'"])
        question = "?" in headline and not self._cue("response", headline) and "question headline"
        if not head_event and (cue := self._cue("awareness", headline) or question):
            return Classification("irrelevant", "awareness", reasons=reasons + [f"awareness headline: '{cue}'"])

        # The headline decides between report and response; the body is the fallback.
        head_response = self._cue("response", headline)
        if event_d and (head_event or not head_response):
            content_type, relevant = "outbreak_report", event_d
        elif response_d:
            content_type, relevant = "public_health_response", response_d
        else:
            return Classification("irrelevant", "other", reasons=reasons + ["no event or response cue"])

        # When the headline names a disease, another disease counts as reported only with at least two
        # outbreak sentences of its own ("Dengue cases soar…" mentioning Covid-like symptoms is not a
        # Covid report). If that leaves nothing, the earlier list stands.
        if in_headline & set(relevant):
            focused = [d for d in relevant if d in in_headline or event_count.get(d, 0) >= MIN_SECONDARY_EVENTS]
            if dropped := [d for d in relevant if d not in focused]:
                reasons.append(f"secondary mention(s) not counted: {', '.join(dropped)}")
            relevant = focused

        status, why = self._event_status(sents, event_sents | {0}, content_type, published_at)
        return Classification("relevant", content_type, status, relevant, reasons + [why])

    def _event_status(self, sents: list[str], idx: set[int], content_type: str,
                      published_at: datetime | None) -> tuple[str, str]:
        """new / suspected / historical / unclear, from the headline, lead and event sentences."""
        headline = sents[0]
        focus = " ".join(sents[i] for i in sorted(idx))
        lead = " ".join(sents[: LEAD_SENTENCES + 1])

        # Historical only on explicit evidence in the headline (a cue such as "years ago", or a past
        # year) and nothing in the lead marking the report as current. Body text often compares
        # with earlier years ("up from last year"), so it is not used.
        year = (published_at or datetime.now()).year
        head_years = [int(y) for y in YEAR.findall(headline)]
        current = self._cue("current", lead) or (year in head_years and str(year))
        historical = self._cue("historical", headline) or (head_years and max(head_years) < year and str(max(head_years)))
        if historical and not current:
            return "historical", f"historical: '{historical}'"

        # Suspected: said in the headline, or in the event sentences when no case counts are given.
        if (cue := self._cue("suspected", headline)) and not self._cue("confirmed", headline):
            return "suspected", f"suspected (headline): '{cue}'"
        if ((cue := self._cue("suspected", focus)) and not self._cue("confirmed", focus)
                and not CASE_COUNT.search(focus)):
            return "suspected", f"suspected: '{cue}'"
        if content_type == "outbreak_report" or (count := CASE_COUNT.search(focus)):
            evidence = current or (content_type != "outbreak_report" and count.group(0))
            return "new", f"new: current report{f' (' + repr(evidence) + ')' if evidence else ''}"
        return "unclear", "unclear: response without case report"


NO_KEYWORD = Classification("irrelevant", "no_keyword", reasons=["no configured keyword in headline or text"])


def apply(article, result: Classification) -> None:
    """Write a classification onto an Article (manual relevance_status is never touched)."""
    article.relevance = result.relevance
    article.content_type = result.content_type
    article.event_status = result.event_status
    article.relevant_diseases = list(result.relevant_diseases)
    article.relevance_reasons = list(result.reasons)
