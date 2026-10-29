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


def test_jaundice_and_plague_need_outbreak_phrasing():
    from outbreak_monitor.config import load_disease_config
    from outbreak_monitor.matcher import KeywordMatcher
    m = KeywordMatcher(load_disease_config().diseases)
    assert m.match("Newborn jaundice: when to see a doctor") == []
    assert m.match("Town plagued by potholes") == []
    assert m.match("3 jaundice deaths in Nainital") == ["Hepatitis A / E"]
    assert m.match("Bubonic plague case confirmed") == ["Other zoonotic outbreaks"]
