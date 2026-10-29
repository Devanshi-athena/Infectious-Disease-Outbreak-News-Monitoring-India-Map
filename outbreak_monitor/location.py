"""Outbreak location: state/UT, district and locality, with coordinates, confidence and review flag.

Steps for one article:
 1. Find place mentions. Named entities (spaCy GPE/LOC) are looked up first, so multi-word names
    such as "North Carolina" stay whole; then the remaining text is matched against the India
    reference data (reference/india_places.csv) and config/locations.yaml. Lower-case words, generic
    words ("East", "City") and names inside a person's name are ignored. A small town (not a
    district or state) found only by the dictionary must also look like a place: tagged as a place by
    spaCy, a large town, or written "in X" / "X village".
 2. Weigh each mention by where it is: headline 3, a sentence that mentions a disease of the article 2,
    other lead sentences 1, elsewhere 0.5. The dateline ("PUNE:", "Mumbai, Oct 20 (PTI) -") and
    bylines are the publisher's/author's location and weigh 0: they count only if the place is also
    named elsewhere.
 3. Resolve names shared by several places: a state the article names, else the higher
    administrative level (a district/state over same-named towns elsewhere), else a clearly larger
    population (flagged), else unresolved (flagged). A foreign country/capital/region wins over a
    same-named small Indian town unless the article names that town's state.
 4. Country: India vs a foreign country named in the headline or outbreak sentences. Foreign
    reports are not mapped.
 5. Most specific *supported* level. State: the best-supported state. District: the headline's
    district, or the single district the outbreak sentences point to; if the headline names only the
    state, or the outbreak sentences cover 3+ districts, the report is state-level. Locality: a town
    in that district named in the headline/outbreak sentences, or a place missing from the reference
    data (often a village) geocoded with Nominatim inside the district. Coordinates are those of the
    chosen level, so a district-level report gets the district's coordinates.
"""

import logging
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import spacy
from rapidfuzz import fuzz, process
from spacy.matcher import PhraseMatcher

from .config import CONFIG_DIR, _read_yaml
from .gazetteer import Place, load_foreign, load_india, normalise_name
from .matcher import compile_phrases
from .processing import split_paragraphs

log = logging.getLogger(__name__)

W_HEADLINE, W_EVENT, W_LEAD, W_OTHER = 3.0, 2.0, 1.0, 0.5
LEAD_SENTENCES = 3
MAX_TEXT_CHARS = 20_000
STATE_REPORT_DISTRICTS = 3      # outbreak sentences naming this many districts -> a state-level report
MULTI_STATE_RATIO = 0.75        # a second state this close to the first -> flag for review
POPULATION_DOMINANCE = 10       # resolve an ambiguous name to a place this many times larger (flagged)
LARGE_TOWN = 100_000            # towns this large are accepted without further evidence
FUZZY_MIN_SCORE = 92            # spelling-variant match threshold (rapidfuzz ratio)
KIND_RANK = {"state": 0, "district": 1, "town": 2}
GENERIC_WORDS = {"east", "west", "north", "south", "central", "new", "old", "upper", "lower", "city", "town",
                 "district", "state", "nagar", "road", "station", "market", "bazar", "bazaar", "camp", "colony",
                 "island", "islands", "valley", "hill", "hills", "port", "fort", "cantonment", "rural", "urban",
                 "pradesh"}
PLACE_BEFORE = re.compile(r"\b(in|at|from|near|of|across|around|to)\s*$", re.I)
PLACE_AFTER = re.compile(r"^\s*(village|town|taluk|tehsil|block|mandal|district|city|area|panchayat)\b", re.I)
# Names worth sending to the geocoder: capitalised words only (no digits or codes like "H1N1", "IgM").
GEOCODABLE = re.compile(r"^[A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}$")
MONTHS = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*$")
NOT_A_LOCALITY = re.compile(r"\b(Street|Road|Lane|Avenue|Marg|Hospital|College|School|University|Park|Market|"
                            r"Station|Bridge|Zoo|Office|Corporation)$")
# Nominatim results accepted as a locality: settlements, neighbourhoods and administrative areas.
PLACE_CLASSES = {"place", "boundary"}
VILLAGE_PHRASE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,})?)\s+(?:village|taluk|tehsil|block|mandal|panchayat)\b")
# A place named shortly after a laboratory/institute is where samples were tested, not the outbreak.
LAB_CUE = re.compile(r"\b(samples?\s+(were\s+)?(sent|collected|tested)|sent\s+to|laborator(y|ies)|lab|"
                     r"institute|NIV|NIHSAD|ICMR|AIIMS)\b", re.I)
LAB_REACH_CHARS = 80
W_LAB = 0.5
LEAD_EVENT_BONUS = 1.0   # an outbreak sentence in the lead weighs more
INSTITUTION = re.compile(r"\b(Hospital|College|Institute|University|AIIMS|Medical|Laborator)", re.I)
# Outbreak sentences that point at the dateline place: "cases in the city", "reported here".
DEICTIC = re.compile(r"\b(the city|city's|city hospitals|here|the district|civic body|municipal corporation|"
                     r"the corporation|local)\b", re.I)

# "PUNE:", "New Delhi, Oct 20 (PTI) -", "Kochi, October 20:", "Hyderabad (IANS):", "New Delhi [India], Oct 18 (ANI):"
DATELINE = re.compile(
    r"^\s*(?P<place>[A-Z][A-Za-z.' -]{1,40}?)\s*(?:\[India\])?\s*"
    r"(?:,\s*(?:[A-Z][a-z]{2,9}\.?\s+\d{1,2}|\d{1,2}\s+[A-Z][a-z]{2,9})(?:,?\s*\d{4})?)?\s*"
    r"(?:\((?:PTI|IANS|ANI|UNI|Reuters|AFP|AP|IANSlife|HT|TNN)\))?\s*[:\-–—]\s+")
BYLINE = re.compile(r"^\s*(By\s|Written by|Express News Service|TNN\b|HT Correspondent|Our Correspondent|"
                    r"Staff Reporter|Special Correspondent|FPJ News Service|DC Correspondent)", re.I)

_nlp = None
_nlp_lock = threading.Lock()


def _get_nlp():
    global _nlp
    with _nlp_lock:
        if _nlp is None:
            _nlp = spacy.load("en_core_web_sm", exclude=["lemmatizer", "tagger", "attribute_ruler"])
    return _nlp


def load_location_config(path: Path = CONFIG_DIR / "locations.yaml") -> dict:
    return _read_yaml(path)


@dataclass
class Mention:
    text: str                   # as written in the article
    sentence: int               # 0 = headline
    weight: float
    places: list[Place]         # candidate reference places (empty: not in reference data)
    role: str                   # headline / event / lead / body / dateline / byline / country
    foreign: str | None = None  # country, if the name is a foreign country, capital or region
    exact: bool = True          # False if matched by spelling similarity or population
    ambiguous: bool = False     # several places share the name; settled once the state is known
    locative: bool = False      # written like a place: "in X", "at X", "X village"
    start: int = 0              # character position in its sentence (headline order matters)


@dataclass
class LocationResult:
    country: str | None = None            # "India", a foreign country, or None if not determinable
    state: str | None = None
    district: str | None = None
    locality: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    level: str | None = None              # state / district / locality / country
    confidence: str = "none"              # high / medium / low / none
    review: bool = False
    location_text: str | None = None      # the article's own words for the chosen place
    source: str | None = None             # reference / nominatim
    reasons: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # every Indian place considered, with weight


class LocationResolver:
    def __init__(self, places: list[Place] | None = None, foreign: dict[str, str] | None = None,
                 config: dict | None = None, geocoder=None):
        self.places = places if places is not None else load_india()
        self.foreign = foreign if foreign is not None else load_foreign()
        config = config if config is not None else load_location_config()
        self.geocoder = geocoder
        self.nlp = _get_nlp()

        self.states = {p.name: p for p in self.places if p.kind == "state"}
        self.districts = {(p.state, p.name): p for p in self.places if p.kind == "district"}
        self.display = dict(config.get("display_names") or {})
        self.by_name: dict[str, list[tuple[Place, bool]]] = defaultdict(list)  # name -> (place, is_its_name)
        for p in self.places:
            self.by_name[normalise_name(p.name)].append((p, True))
            for alias in p.aliases:
                self.by_name[alias].append((p, False))
        extra_patterns = []
        for alias, target in (config.get("aliases") or {}).items():
            self.by_name[normalise_name(alias)].extend((p, True) for p in self._lookup_target(target))
            extra_patterns += [alias, alias.replace("'", "’")]  # as written, e.g. "G'nagar" / "G’nagar"
        for word in GENERIC_WORDS:
            self.by_name.pop(word, None)
        self.abbreviations = {abbr: self.states[name] for abbr, name in (config.get("abbreviations") or {}).items()
                              if name in self.states}

        self.matcher = PhraseMatcher(self.nlp.vocab, attr="LOWER")
        names = [n for n in self.by_name if len(n) >= 3] + extra_patterns
        for i in range(0, len(names), 1000):
            self.matcher.add("PLACE", [self.nlp.make_doc(n) for n in names[i:i + 1000]])

    def show(self, name: str | None) -> str | None:
        """Display name (config display_names override outdated reference names)."""
        return self.display.get(name, name) if name else name

    def _lookup_target(self, target: str) -> list[Place]:
        """'Visakhapatnam, Andhra Pradesh' -> that place (state/district preferred over town)."""
        name, _, state = (part.strip() for part in target.partition(","))
        found = [p for p in self.places if normalise_name(p.name) == normalise_name(name) and (not state or p.state == state)]
        return sorted(found, key=lambda p: KIND_RANK[p.kind])[:1]

    def _candidates(self, text: str) -> list[Place]:
        """Reference places for a name; places whose own name it is win over places it is an alias of."""
        entries = self.by_name.get(normalise_name(text), [])
        own = [p for p, is_name in entries if is_name]
        return list({p.geonameid: p for p in (own or [p for p, _ in entries])}.values())

    # ------------------------------------------------------------------ mentions

    def _sentence_mentions(self, doc, sentence: int, weight: float, role: str, skip_chars: int = 0) -> list[Mention]:
        mentions, taken = [], set()
        place_ents = [e for e in doc.ents if e.label_ in ("GPE", "LOC", "FAC", "ORG")]
        person = [(e.start_char, e.end_char) for e in doc.ents if e.label_ == "PERSON"]
        lab_cues = [m.end() for m in LAB_CUE.finditer(doc.text)]
        institutions = [(e.start_char, e.end_char) for e in doc.ents
                        if e.label_ in ("ORG", "FAC") and INSTITUTION.search(e.text)]

        def add(text, start_char, places, foreign=None, mention_role=None, locative=None):
            r = mention_role or ("dateline" if start_char < skip_chars else role)
            w = 0.0 if r in ("dateline", "byline") else weight
            end_char = start_char + len(text)
            if any(0 <= start_char - end <= LAB_REACH_CHARS for end in lab_cues) or \
                    any(s <= start_char and end_char <= e for s, e in institutions):
                w = min(w, W_LAB)  # where samples were tested / the hospital's town, not the outbreak's
            if locative is None:
                locative = bool(PLACE_BEFORE.search(doc.text[:start_char]) or PLACE_AFTER.match(doc.text[end_char:]))
            mentions.append(Mention(text, sentence, w, places, r, foreign, locative=locative, start=start_char))

        def foreign_name(text):
            norm = normalise_name(text)
            return None if norm in GENERIC_WORDS else self.foreign.get(norm)

        # 1. Named entities first, so multi-word names stay whole ("North Carolina", "East Singhbhum").
        for ent in place_ents:
            if ent.label_ in ("FAC", "ORG"):  # used only as evidence for dictionary matches below
                continue
            name = re.sub(r"^(the)\s+", "", ent.text, flags=re.I)
            name = re.sub(r"[’']s$", "", name)
            norm = normalise_name(name)
            if norm in ("india", "indian", "the country", "country"):
                add(ent.text, ent.start_char, [], mention_role="country")
            else:
                stripped = re.sub(r"\s+(district|city|town|village|state)$", "", name, flags=re.I)
                add(ent.text, ent.start_char, self._candidates(name) or self._candidates(stripped),
                    foreign=foreign_name(name))
            taken.update(range(ent.start, ent.end))

        # 2. Reference names in the remaining text (longest match first, no overlaps).
        for start, end in sorted(((s, e) for _, s, e in self.matcher(doc)), key=lambda x: (x[0], -(x[1] - x[0]))):
            span = doc[start:end]
            if any(i in taken for i in range(start, end)) or not span.text[:1].isupper():
                continue
            # part of a longer person's name ("Krishna Kumar"); a lone mislabelled place name is kept
            if any(ps <= span.start_char and span.end_char <= pe and (pe - ps) > len(span.text) for ps, pe in person):
                continue
            places = self._candidates(span.text)
            if not places:
                continue
            if all(p.kind == "town" for p in places) and not self._looks_like_place(span, doc, place_ents, places):
                continue
            taken.update(range(start, end))
            add(span.text, span.start_char, places, foreign=foreign_name(span.text))

        # 3. Case-sensitive abbreviations ("UP", "J&K").
        for token in doc:
            if token.text in self.abbreviations and token.i not in taken:
                add(token.text, token.idx, [self.abbreviations[token.text]])

        # 4. "Tizit village", "Bela mandal": a local place the model and reference data may not know.
        for match in VILLAGE_PHRASE.finditer(doc.text):
            span = doc.char_span(match.start(1), match.end(1), alignment_mode="expand")
            if span is not None and not any(t.i in taken for t in span):
                taken.update(t.i for t in span)
                add(match.group(1), match.start(1), [], locative=True)
        return mentions

    @staticmethod
    def _looks_like_place(span, doc, place_ents, places) -> bool:
        """Evidence that a dictionary match on a small-town name is really a place."""
        if any(e.start <= span.start and span.end <= e.end for e in place_ents):
            return True
        if max(p.population for p in places) >= LARGE_TOWN:
            return True
        before, after = doc.text[:span.start_char], doc.text[span.end_char:]
        return bool(PLACE_BEFORE.search(before) or PLACE_AFTER.match(after))

    def find_mentions(self, headline: str, text: str | None,
                      disease_pattern: re.Pattern | None) -> tuple[list[Mention], bool]:
        """All place mentions, and whether outbreak sentences refer to the dateline place ("the city")."""
        mentions = self._sentence_mentions(self.nlp(headline), 0, W_HEADLINE, "headline")
        paragraphs = split_paragraphs(text, MAX_TEXT_CHARS)
        sentence_no, deictic, seen_dateline = 0, False, False
        for p_index, doc in enumerate(self.nlp.pipe(paragraphs)):
            byline = bool(BYLINE.match(doc.text))
            dateline = DATELINE.match(doc.text) if p_index <= 2 and not seen_dateline else None
            seen_dateline = seen_dateline or bool(dateline)
            skip = dateline.end() if dateline else 0
            for sent in doc.sents:
                sentence_no += 1
                if byline:
                    role, weight = "byline", 0.0
                elif disease_pattern and disease_pattern.search(sent.text):
                    role, weight = "event", W_EVENT + (LEAD_EVENT_BONUS if sentence_no <= LEAD_SENTENCES else 0)
                    deictic = deictic or bool(DEICTIC.search(sent.text))
                elif sentence_no <= LEAD_SENTENCES:
                    role, weight = "lead", W_LEAD
                else:
                    role, weight = "body", W_OTHER
                local_skip = skip - sent.start_char if sent.start_char < skip else 0
                mentions += self._sentence_mentions(sent.as_doc(), sentence_no, weight, role, local_skip)
        return mentions, deictic

    # ------------------------------------------------------------------ resolution

    @staticmethod
    def _state_weights(mentions: list[Mention]) -> dict[str, float]:
        """Weight per state; a state named twice in one sentence ("Yavatmal, Maharashtra") counts once."""
        best = {}
        for m in mentions:
            key = (m.sentence, m.places[0].state)
            best[key] = max(best.get(key, 0.0), m.weight)
        weights = defaultdict(float)
        for (_, state), w in best.items():
            weights[state] += w
        return weights

    def _resolve_names(self, mentions: list[Mention], result: LocationResult) -> list[Mention]:
        """Settle each mention on one Indian place where possible. Returns the settled Indian mentions;
        names still shared by places in several states are marked ambiguous."""
        # A small-town match in a sentence that names a district/state elsewhere is most likely a local
        # village of that name ("Badi village of Bela mandal in Adilabad district"): set it aside.
        anchors = defaultdict(set)
        for m in mentions:
            if m.places and len({p.state for p in m.places}) == 1 and m.places[0].kind != "town" and not m.foreign:
                anchors[m.sentence].add(m.places[0].state)
        for m in mentions:
            if m.places and all(p.kind == "town" for p in m.places) and anchors[m.sentence] \
                    and not any(p.state in anchors[m.sentence] for p in m.places):
                m.places = []

        unambiguous = [m for m in mentions if m.places and m.weight and not m.foreign
                       and len({p.state for p in m.places}) == 1]
        state_weight = self._state_weights(unambiguous)

        for m in mentions:
            # A foreign country/capital/region vs same-named small Indian towns.
            if m.foreign and m.places and all(p.kind == "town" for p in m.places) \
                    and not any(p.state in state_weight for p in m.places):
                m.places = []
            # A named place missing from the reference data: a close spelling in the article's states.
            if not m.places and not m.foreign and m.role not in ("country", "dateline", "byline"):
                self._fuzzy(m, set(state_weight))
                if m.places:
                    result.reasons.append(f"spelling variant: '{m.text}' matched to {m.places[0].name}")
            if len({p.state for p in m.places}) <= 1:
                continue
            supported = [p for p in m.places if p.state in state_weight]
            top_rank = min(KIND_RANK[p.kind] for p in m.places)
            highest = [p for p in m.places if KIND_RANK[p.kind] == top_rank]
            by_pop = sorted(m.places, key=lambda p: -p.population)
            if supported:
                best = max(supported, key=lambda p: (state_weight[p.state], -KIND_RANK[p.kind]))
                m.places = [p for p in m.places if p.state == best.state]
            elif len({p.state for p in highest}) == 1:
                m.places = highest
            elif by_pop[0].population >= POPULATION_DOMINANCE * max(by_pop[1].population, 1):
                m.places, m.exact = [by_pop[0]], False
                result.reasons.append(f"'{m.text}' taken as {by_pop[0].state} by population (please check)")
            else:
                m.ambiguous = True
        for m in mentions:  # one place per mention: the highest administrative level
            if m.places and not m.ambiguous:
                m.places = [min(m.places, key=lambda p: KIND_RANK[p.kind])]
        return [m for m in mentions if m.places and not m.ambiguous]

    def _settle_ambiguous(self, mentions: list[Mention], state: str, result: LocationResult) -> list[Mention]:
        """With the state known, a shared name with a candidate in that state is that place. Names that
        still cannot be placed are noted; they need review only if they are in the headline."""
        settled, noted = [], set()
        for m in mentions:
            if not m.ambiguous:
                continue
            here = [p for p in m.places if p.state == state]
            if here:
                m.places, m.ambiguous = [min(here, key=lambda p: KIND_RANK[p.kind])], False
                settled.append(m)
            elif m.text not in noted:
                noted.add(m.text)
                states = ", ".join(sorted({p.state for p in m.places}))
                if m.role == "headline":
                    result.review = True
                    result.reasons.append(f"'{m.text}' in the headline is ambiguous ({states})")
                else:
                    result.reasons.append(f"'{m.text}' not placed (also in {states})")
        return settled

    def _fuzzy(self, m: Mention, states: set[str]) -> None:
        pool = {normalise_name(p.name): p for p in self.places
                if (p.kind == "district" or p.population >= LARGE_TOWN) and (not states or p.state in states)}
        name = normalise_name(m.text)
        if len(name) < 6:
            return
        best = process.extractOne(name, list(pool), scorer=fuzz.ratio, score_cutoff=FUZZY_MIN_SCORE)
        if best:
            m.places, m.exact = [pool[best[0]]], False

    def locate_article(self, headline: str, text: str | None, diseases: list[str], keywords: dict[str, list[str]],
                       geocode: bool = True) -> LocationResult:
        """Location for an article; outbreak sentences are those mentioning one of its diseases."""
        pattern = compile_phrases([k for name in diseases for k in keywords.get(name, [])])
        return self.resolve(headline, text, pattern, geocode)

    def resolve(self, headline: str, text: str | None, disease_pattern: re.Pattern | None = None,
                geocode: bool = True) -> LocationResult:
        mentions, deictic = self.find_mentions(headline, text, disease_pattern)
        result = LocationResult()
        # The dateline is the publisher's location, except when the article reports the outbreak there:
        # the outbreak sentences name no other place and either refer to it ("cases in the city",
        # "here") or the article keeps naming it.
        datelines = [m for m in mentions if m.role == "dateline" and m.places]
        dateline_states = {p.state for m in datelines for p in m.places}
        # Another place counts only if it is not simply the dateline's own state ("Bhopal: ... in Madhya Pradesh").
        named = any(m.role in ("headline", "event") and m.weight > W_LAB and
                    (m.foreign or any(p.kind != "state" or p.state not in dateline_states for p in m.places))
                    for m in mentions)
        if datelines and not named:
            dateline_name = normalise_name(datelines[0].text)
            repeated = any(normalise_name(m.text) == dateline_name for m in mentions if m.role != "dateline")
            if deictic or repeated:
                for m in datelines:
                    m.role, m.weight = "event", W_EVENT
                why = "the outbreak sentences refer to it ('the city' / 'here')" if deictic else "the article reports there"
                result.reasons.append(f"dateline place '{datelines[0].text}' used: {why}")
        indian = self._resolve_names(mentions, result)

        # Country: foreign only if named in the headline/outbreak sentences and outweighing India.
        india_weight = sum(m.weight for m in indian) + sum(m.weight for m in mentions if m.role == "country")
        foreign_weight = defaultdict(float)
        for m in mentions:
            if m.foreign and not m.places:
                foreign_weight[m.foreign] += m.weight
        country, f_weight = max(foreign_weight.items(), key=lambda kv: kv[1], default=(None, 0.0))
        ambiguous_weight = sum(m.weight for m in mentions if m.ambiguous)
        if f_weight >= W_EVENT and f_weight > india_weight + ambiguous_weight:
            result.country, result.level, result.confidence = country, "country", "medium"
            result.reasons.append(f"outside India: {country}")
            return result
        if not india_weight and ambiguous_weight:  # only names shared by places in several states
            result.country, result.level, result.confidence, result.review = "India", "country", "low", True
            for m in mentions:
                if m.ambiguous and m.weight:
                    states = ", ".join(sorted({p.state for p in m.places}))
                    result.reasons.append(f"'{m.text}' is ambiguous ({states}); no state named to decide")
            return result
        if not india_weight:
            result.review = True  # a person can add the location
            result.reasons.append("no location found")
            return result
        result.country = "India"
        result.candidates = self._candidate_summary(indian)

        ranked = sorted(self._state_weights(indian).items(), key=lambda kv: -kv[1])
        if not ranked or ranked[0][1] == 0:
            result.level, result.confidence, result.review = "country", "low", True
            result.reasons.append("only national-level, dateline or byline locations")
            return result
        state, top = ranked[0]
        if len(ranked) > 1 and ranked[1][1] >= MULTI_STATE_RATIO * top:
            result.review = True
            result.reasons.append(f"several states reported: {', '.join(s for s, _ in ranked[:3])}")
        indian += self._settle_ambiguous(mentions, state, result)
        result.candidates = self._candidate_summary(indian)
        in_state = [m for m in indian if m.places[0].state == state and m.weight]
        # headline/outbreak-sentence mentions, except labs' and hospitals' towns (down-weighted)
        strong = [m for m in in_state if m.role in ("headline", "event") and m.weight > W_LAB]

        district = self._choose_district(strong, result, headline)
        locality_mention = None
        if district:
            towns = [m for m in strong if m.places[0].kind == "town" and self._district_of(m.places[0]) == district
                     and normalise_name(m.places[0].name) != normalise_name(district)]
            locality_mention = max(towns, key=lambda m: m.weight, default=None)

        if locality_mention:
            chosen = [m for m in in_state if m.places[0] == locality_mention.places[0]]
        elif district:
            chosen = [m for m in in_state if self._district_of(m.places[0]) == district]
        else:
            chosen = [m for m in in_state if m.places[0].kind == "state"] or in_state
        result.state, result.source = state, "reference"
        result.location_text = max(chosen, key=lambda m: m.weight).text
        if locality_mention:
            town = locality_mention.places[0]
            result.district, result.locality, result.level = district, town.name, "locality"
            result.latitude, result.longitude = town.latitude, town.longitude
        elif district:
            d = self.districts[(state, district)]
            result.district, result.level = district, "district"
            result.latitude, result.longitude = d.latitude, d.longitude
            if geocode:
                self._geocode_village(result, mentions, state, district, disease_pattern)
        else:
            s = self.states[state]
            result.level, result.latitude, result.longitude = "state", s.latitude, s.longitude

        support = [m for m in chosen if m.role in ("headline", "event")]
        result.confidence = ("high" if any(m.role == "headline" for m in support) or len(support) >= 2
                             else "medium" if support else "low")
        if any(not m.exact for m in chosen) and result.confidence != "low":
            result.confidence = "medium" if result.confidence == "high" else "low"
        if not support:
            result.reasons.append("place not named in the headline or outbreak sentences")
        if any("please check" in r or "spelling variant" in r for r in result.reasons):
            result.review = True
        if result.review:
            result.confidence = "low"
        result.review = result.review or result.confidence == "low"
        result.state, result.district = self.show(result.state), self.show(result.district)
        result.locality = self.show(result.locality)
        return result

    def _choose_district(self, strong: list[Mention], result: LocationResult, headline: str) -> str | None:
        weight, has_town = defaultdict(float), set()
        for m in strong:
            d = self._district_of(m.places[0])
            if d:
                weight[d] += m.weight
                if m.places[0].kind == "town" and normalise_name(m.places[0].name) != normalise_name(d):
                    has_town.add(d)
        head = sorted((m for m in strong if m.role == "headline"), key=lambda m: m.start)
        head_districts = {self._district_of(m.places[0]) for m in head} - {None}
        head_state = any(m.places[0].kind == "state" for m in head)
        if head_state and head_districts:
            # Both named. A district written as the place of the event ("outbreak in Mon's Tizit") wins;
            # otherwise the first place is the focus ("Haryana dengue cases jump 58%; Gurgaon sees highest"
            # vs "Gurugram dengue cases rise to 66 as Haryana cases cross 500"). A leading "Gujarat:" is a label.
            located = [m for m in head if m.places[0].kind != "state" and m.locative]
            label = head[0].start == 0 and headline[len(head[0].text):].lstrip().startswith(":")
            first = located[0] if located else (head[1] if label and len(head) > 1 else head[0])
            if first.places[0].kind == "state":
                result.reasons.append("headline leads with the state: state-level report")
                return None
            return self._district_of(first.places[0])
        if len(weight) >= STATE_REPORT_DISTRICTS and not head_districts:
            result.reasons.append(f"state-level report: outbreak sentences cover {len(weight)} districts")
            return None
        if not head_districts and head_state:
            if weight:
                result.reasons.append("headline names the state only: state-level report")
            return None
        options = head_districts or set(weight)
        ranked = sorted(options, key=lambda d: (-weight[d], d not in has_town))
        if not ranked:
            return None
        if len(ranked) > 1 and weight[ranked[0]] == weight[ranked[1]] and \
                (ranked[0] in has_town) == (ranked[1] in has_town):
            result.reasons.append(f"districts equally reported ({', '.join(ranked)}): state-level")
            return None
        return ranked[0]

    def _district_of(self, place: Place) -> str | None:
        if place.kind == "district":
            return place.name
        if place.kind == "state":
            return None
        if place.district:
            return place.district
        same = self.districts.get((place.state, place.name))  # a town listed without its district
        return same.name if same else None

    def _geocode_village(self, result: LocationResult, mentions: list[Mention], state: str, district: str,
                         disease_pattern: re.Pattern | None) -> None:
        """A place named in the headline/outbreak sentences but missing from the reference data (often a
        village): look it up with Nominatim inside the district. Only names written like a place
        ("in X", "X village") are looked up; the answer must be a place in the same state."""
        if not self.geocoder:
            return
        for m in mentions:
            if m.places or m.foreign or m.role not in ("headline", "event") or not m.locative:
                continue
            if not GEOCODABLE.match(m.text) or MONTHS.match(m.text) or NOT_A_LOCALITY.search(m.text) \
                    or normalise_name(m.text) in GENERIC_WORDS \
                    or (disease_pattern and disease_pattern.search(m.text)):
                continue
            found = self.geocoder.locate(m.text, district, state)
            if found:
                result.locality, result.level, result.source = m.text, "locality", "nominatim"
                result.latitude, result.longitude = found["lat"], found["lon"]
                result.location_text = m.text
                result.reasons.append(f"locality '{m.text}' geocoded with Nominatim in {district}, {state}")
                return

    @staticmethod
    def _candidate_summary(mentions: list[Mention]) -> list[dict]:
        summary: dict[str, dict] = {}
        for m in mentions:
            p = m.places[0]
            entry = summary.setdefault(p.geonameid, {"name": p.name, "kind": p.kind, "state": p.state,
                                                     "district": p.district, "weight": 0.0, "roles": []})
            entry["weight"] += m.weight
            if m.role not in entry["roles"]:
                entry["roles"].append(m.role)
        return sorted(summary.values(), key=lambda e: -e["weight"])


def apply(article, result: LocationResult) -> None:
    """Write a location onto an Article."""
    article.country, article.state, article.district = result.country, result.state, result.district
    article.locality, article.latitude, article.longitude = result.locality, result.latitude, result.longitude
    article.location_level, article.location_text = result.level, result.location_text
    article.location_confidence, article.location_review = result.confidence, result.review
    article.location_source = result.source
    article.location_reasons, article.location_candidates = list(result.reasons), list(result.candidates)


def build_resolver(session_factory, config: dict | None = None) -> LocationResolver:
    """Resolver with the Nominatim geocoder configured in config/locations.yaml."""
    config = config if config is not None else load_location_config()
    geo = config.get("geocoding") or {}
    geocoder = (NominatimGeocoder(session_factory, geo["user_agent"], float(geo.get("min_delay_seconds", 1.1)))
                if geo.get("enabled") and geo.get("user_agent") else None)
    return LocationResolver(config=config, geocoder=geocoder)


# ---------------------------------------------------------------------- geocoding


class NominatimGeocoder:
    """geopy + OpenStreetMap Nominatim with a database cache and a global 1-request-per-second limit."""

    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, session_factory, user_agent: str, min_delay_seconds: float = 1.1):
        from geopy.geocoders import Nominatim
        self.client = Nominatim(user_agent=user_agent, timeout=20)
        self.session_factory = session_factory
        self.min_delay = min_delay_seconds

    def locate(self, name: str, district: str, state: str) -> dict | None:
        from .db import GeocodeCache
        query = f"{name}, {district}, {state}, India"
        with self.session_factory() as session:
            cached = session.get(GeocodeCache, query)
            if cached is None:
                try:
                    result = self._call(query)
                except Exception as exc:  # temporary problem: not cached, tried again next time
                    log.warning("Geocoding failed for %s: %s", query, exc)
                    return None
                cached = GeocodeCache(query=query, result=result)  # {} = looked up, not found
                session.merge(cached)
                session.commit()
            found = cached.result
        if not found:
            return None
        if found.get("class") not in PLACE_CLASSES:  # a street, clinic, bridge... is not a locality
            log.info("Geocoded '%s' is a %s/%s, not a place; ignored", query, found.get("class"), found.get("type"))
            return None
        if normalise_name(found.get("state") or "") != normalise_name(state):
            log.info("Geocoded '%s' outside %s (%s); ignored", query, state, found.get("state"))
            return None
        return found

    def _call(self, query: str) -> dict:
        with NominatimGeocoder._lock:
            wait = NominatimGeocoder._last_call + self.min_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                loc = self.client.geocode(query, country_codes="in", addressdetails=True, exactly_one=True)
            finally:
                NominatimGeocoder._last_call = time.monotonic()
        if not loc:
            return {}
        address = loc.raw.get("address", {})
        return {"lat": loc.latitude, "lon": loc.longitude, "display_name": loc.raw.get("display_name"),
                "class": loc.raw.get("class"), "type": loc.raw.get("type"),
                "state": address.get("state"), "district": address.get("state_district") or address.get("county")}
