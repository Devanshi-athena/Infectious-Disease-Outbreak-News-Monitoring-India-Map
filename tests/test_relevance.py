from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from outbreak_monitor import db
from outbreak_monitor.config import Disease
from outbreak_monitor.relevance import CASE_COUNT, RelevanceClassifier, load_relevance_config

DISEASES = [Disease("Dengue", ["dengue"]), Disease("Malaria", ["malaria"]),
            Disease("Measles", ["measles", "rubella"]), Disease("Typhoid", ["typhoid"])]
PUBLISHED = datetime(2026, 10, 20)


@pytest.fixture(scope="module")
def classify():
    clf = RelevanceClassifier(DISEASES, load_relevance_config())
    return lambda headline, body="", url="https://news.example/india/story-1": clf.classify(
        headline, body, url, PUBLISHED)


@pytest.mark.parametrize("sentence", [
    "Gurugram dengue tally rises to 70 with 2 fresh cases",
    "2,500 country chickens die as bird flu hits the farm",
    "Cases of high-risk malaria strain in Mumbai jump 535% in 10 years",
    "87 jawans hospitalised with dengue in 10 days",
    "138 fall ill after consuming prasad",
    "At least 138 people have taken ill after the feast",
    "Since a large number of people fell ill after consuming the prasad, food poisoning is suspected",
])
def test_case_count_patterns(sentence):
    assert CASE_COUNT.search(sentence)


@pytest.mark.parametrize("sentence", [
    "COVID-19 cases in the city were discussed",
    "Since 2019, people have avoided the area",
    "H1N1 patients need rest",
    "The 1.5 children per family figure",
])
def test_not_case_counts(sentence):
    assert not CASE_COUNT.search(sentence)


def test_question_headline_with_response_cue_is_not_awareness(classify):
    r = classify("Nipah scare in Kozhikode? Health dept issues dengue alert",
                 "The health department issued an alert after dengue cases were found. Dengue fogging began.")
    assert r.relevance == "relevant"


def test_outbreak_report_new(classify):
    r = classify("Dengue cases reach 236 as administration intensifies fogging",
                 "The district reported 12 new dengue cases on Monday. Fogging was carried out in 40 wards.")
    assert (r.relevance, r.content_type, r.event_status, r.relevant_diseases) == \
        ("relevant", "outbreak_report", "new", ["Dengue"])


def test_suspected_in_headline(classify):
    r = classify("8-year-old girl dies in first suspected dengue death in Pune this year",
                 "Her samples were sent for testing. Dengue cases have been rising in the city.")
    assert r.event_status == "suspected"


def test_historical_needs_headline_evidence(classify):
    r = classify("Malaria outbreak that killed 40 recalled years ago in Odisha village",
                 "The malaria outbreak killed 40 people in the village.")
    assert r.event_status == "historical"
    # comparison with an earlier year in the body does not make a report historical
    r = classify("Lucknow logs 144 dengue cases this month",
                 "Lucknow logged 144 dengue cases this month, up from 120 in 2025.")
    assert r.event_status == "new"


def test_response_without_cases_is_unclear(classify):
    r = classify("Collector reviews dengue preparedness and fogging drive",
                 "The collector asked officials to intensify the dengue drive and anti-larval work.")
    assert (r.relevance, r.content_type, r.event_status) == ("relevant", "public_health_response", "unclear")


@pytest.mark.parametrize("headline,content_type", [
    ("ICMR study detects insecticide resistance in dengue mosquitoes", "research"),
    ("India may get own dengue vaccine by next year", "research"),
    ("Can adults get measles too? Doctors explain", "awareness"),
    ("Why mosquitoes bite some people more: what dengue experts say", "awareness"),
])
def test_research_and_awareness_are_irrelevant(classify, headline, content_type):
    r = classify(headline, "Dengue and measles are covered in this piece. Dengue spreads through mosquitoes.")
    assert (r.relevance, r.content_type) == ("irrelevant", content_type)


def test_secondary_disease_needs_its_own_outbreak_sentences(classify):
    r = classify("Dengue cases rise to 72 in Nagpur",
                 "Dengue cases rose to 72 this month. Doctors said malaria was also seen in 3 cases. "
                 "Dengue wards are full. Malaria testing continues. Malaria awareness drives are planned.")
    assert r.relevant_diseases == ["Dengue"]
    r = classify("Dengue and malaria cases surge in Delhi",
                 "Dengue cases rose to 300. Malaria cases rose to 120.")
    assert r.relevant_diseases == ["Dengue", "Malaria"]
    r = classify("Dengue cases rise to 72 in Nagpur",
                 "Dengue cases rose to 72. Malaria cases also rose to 40. Malaria deaths reached 2.")
    assert r.relevant_diseases == ["Dengue", "Malaria"]   # two outbreak sentences of its own


def test_passing_mention(classify):
    r = classify("Athlete wins gold at Asian Games after tough year",
                 "She trained for months. Earlier she had typhoid. She then won the final.")
    assert (r.relevance, r.content_type) == ("irrelevant", "passing_mention")


def test_excluded_url_section(classify):
    r = classify("Jay Meena battled typhoid to win historic medal", "He had typhoid. Typhoid kept him out. 3 cases.",
                 url="https://news.example/sports/other/jay-meena-1")
    assert (r.relevance, r.content_type) == ("irrelevant", "other")


def test_new_columns_added_to_existing_database(tmp_path):
    path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:  # an articles table from before the relevance columns existed
        conn.execute(text("CREATE TABLE articles (article_id INTEGER PRIMARY KEY, headline TEXT)"))
    db.get_session_factory(f"sqlite:///{path}")
    columns = {c["name"] for c in inspect(engine).get_columns("articles")}
    assert {"relevance", "content_type", "relevant_diseases", "relevance_reasons"} <= columns


def test_crime_headline_is_not_an_outbreak():
    clf = RelevanceClassifier([Disease("Acute Diarrhoeal Disease", ["food poisoning"])], load_relevance_config())
    body = "Five members of a family died. Food poisoning was suspected. Police said food poisoning was ruled out."
    r = clf.classify("'Not food poisoning; it is murder': twist in family deaths", body, "https://a.in/x", PUBLISHED)
    assert (r.relevance, r.content_type) == ("irrelevant", "other")
    r = clf.classify("75 BSF personnel hospitalised over suspected food poisoning",
                     "75 personnel were hospitalised after food poisoning at the camp.", "https://a.in/y", PUBLISHED)
    assert r.relevance == "relevant"


def test_disease_inference_is_strict():
    from outbreak_monitor.config import load_disease_config
    from outbreak_monitor.matcher import KeywordMatcher
    m = KeywordMatcher(load_disease_config().diseases)
    assert m.match("Newborn jaundice: when to see a doctor") == []
    assert m.match("Town plagued by potholes") == []
    # Jaundice alone is not hepatitis A/E: it is the jaundice syndrome (S form) until a cause is named
    assert m.match("3 jaundice deaths in Nainital") == ["Jaundice (< 4 weeks)"]
    assert m.match("Jaundice cases rise; hepatitis A confirmed in samples") == ["Hepatitis A"]
    assert m.match("Newborn jaundice: when to see a doctor") == []
    assert m.match("Hepatitis A outbreak in hostel") == ["Hepatitis A"]
    assert m.match("Viral hepatitis cases rise; hepatitis E found in samples") == ["Hepatitis E"]
    assert m.match("Dysentery cases rise in slum") == ["Dysentery"]   # its own condition, not ADD in general
    # Overlapping keywords: the more specific one wins
    assert m.match("Japanese encephalitis cases in Gorakhpur") == ["Japanese Encephalitis"]   # not also AES
    assert m.match("Measles-rubella cases reported") == ["Measles", "Rubella"]
    assert m.match("Bubonic plague case confirmed") == ["Plague"]
    assert m.match("Stray dog bites 12; one dies of rabies") == ["Human Rabies"]           # bite superseded
    assert m.match("Dog bite cases rise in the city") == ["Animal bite"]
    assert m.match("Mystery fever kills 3; dengue confirmed") == ["Dengue"]                # syndrome superseded
    # Bird flu in animals is recognised but not monitored, and is not human influenza
    assert m.match("Avian influenza confirmed in water birds") == []
    assert m.match("Bird flu outbreak triggers alert") == []
    assert m.match("Seasonal influenza and bird flu cases rise") == ["Influenza"]
    assert m.match("2,500 chickens died of bird flu in Nellore") == []
    # Bird flu in people is monitored, only when a person is named
    assert m.match("Two-year-old girl dies of bird flu in Andhra Pradesh") == ["Bird flu in humans"]
    assert m.match("India reports first human case of H5N1") == ["Bird flu in humans"]
    assert m.match("40,000 chickens culled; no human cases of bird flu reported") == []
    assert m.match("Man tested positive for bird flu, says health department") == ["Bird flu in humans"]
    # Cough syndromes (S form 2.6 / 2.7): never plain "cough"
    assert m.match("Cough syrup deaths: firm's licence cancelled") == []
    assert m.match("Cough and cold cases rise in the city") == ["Cough (up to 2 weeks)"]
    assert m.match("Whooping cough cases in school") == ["Pertussis"]
    assert m.match("H1N1 cases rise; symptoms include fever and persistent cough") == ["Influenza"]


def test_comparison_and_past_events_are_not_outbreak_reports():
    clf = RelevanceClassifier([Disease("COVID-19", ["covid", "covid-19"])], load_relevance_config())
    r = clf.classify("'Ebola is like Covid pandemic': African CDC chief says no country is safe",
                     "Ebola has killed 40 people in Congo. Officials said the response must be fast.",
                     "https://a.in/x", PUBLISHED)
    assert r.relevance == "irrelevant"
    r = clf.classify("HC orders Rs 50L Covid ex gratia despite absence of RT-PCR report",
                     "The man died of Covid in 2021. Covid deaths were 40 that month. His Covid report was missing.",
                     "https://a.in/y", PUBLISHED)
    assert (r.relevance, r.content_type) == ("irrelevant", "other")
    r = clf.classify("Covid cases rise to 40 in Pune", "Pune reported 40 Covid cases this week.", "https://a.in/z",
                     PUBLISHED)
    assert r.relevance == "relevant"


def test_keywords_match_across_quote_marks():
    from outbreak_monitor.matcher import compile_phrases
    pattern = compile_phrases(["mystery fever", "bird flu"])
    assert pattern.search("'Mystery' fever kills 2 sisters in Baran")
    assert pattern.search("“Bird” flu scare") and pattern.search("bird-flu cases")
    assert not pattern.search("mysteryfever") and not pattern.search("birds flu")


def test_native_keyword_excluded_endings():
    from outbreak_monitor.config import load_disease_config
    from outbreak_monitor.matcher import KeywordMatcher
    m = KeywordMatcher(load_disease_config().diseases)
    assert m.match("ഇൻഫ്ലുവൻസ കേസുകൾ കൂടുന്നു") == ["Influenza"]              # influenza cases rise
    assert m.match("ഇൻഫ്ലുവൻസയുടെ വ്യാപനം") == ["Influenza"]                # spread of influenza
    assert m.match("ഇൻഫ്ലുവൻസറുടെ വീട്ടിലെത്തി") == []                      # influencer's house
    assert m.match("വ്ലോഗർമാർക്കും ഇൻഫ്ലുവൻസർമാർക്കും എതിരെ നടപടി") == []   # influencers
    assert m.match("ഏവിയൻ ഇൻഫ്ലുവൻസയുടെ സ്ഥിരീകരണം") == []                    # avian influenza: animals


def test_quick_gate_gives_the_same_matches():
    from outbreak_monitor.config import load_disease_config
    from outbreak_monitor.matcher import KeywordMatcher
    m = KeywordMatcher(load_disease_config().diseases)
    texts = ["News: dengue cases rise in Pune", "New human case of H5N1 in Andhra", "newsletter about cricket",
             "Hospitalised after food poisoning", "डेंगूने दोघांचा मृत्यू", "Girl dies of bird flu", "Stock markets rally"]
    gated = [m.match(t) for t in texts]
    gate, m.gate = m.gate, None
    assert gated == [m.match(t) for t in texts]
    assert gate.search("Cricket news today") is None   # "new" is a whole word in its keyword, not "news"
