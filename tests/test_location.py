from unittest.mock import MagicMock

import pytest

from outbreak_monitor.db import GeocodeCache, get_session_factory
from outbreak_monitor.gazetteer import display_name, normalise_name
from outbreak_monitor.location import LocationResolver, NominatimGeocoder

KEYWORDS = {"Dengue": ["dengue"], "Malaria": ["malaria"], "Measles": ["measles"],
            "Bird flu": ["bird flu", "avian influenza", "H5N1"]}


def locate(resolver, headline, text="", diseases=("Dengue",)):
    return resolver.locate_article(headline, text, list(diseases), KEYWORDS)


def test_reference_names_are_cleaned():
    assert display_name("State of Kerala") == "Kerala"
    assert display_name("Gurugram district") == "Gurugram"
    assert display_name("National Capital Territory of Delhi") == "Delhi"
    assert normalise_name("G’nagar") == normalise_name("G'nagar") == "gnagar"
    assert normalise_name("Tiruchirāppalli") == "tiruchirappalli"


def test_district_from_headline(resolver):
    r = locate(resolver, "Dengue cases rise to 58, malaria count reaches 82 in Ludhiana",
               "The district health department said 58 dengue cases were confirmed this year.")
    assert (r.country, r.state, r.district, r.level, r.confidence) == ("India", "Punjab", "Ludhiana", "district", "high")
    assert r.location_text == "Ludhiana" and r.latitude and r.longitude


def test_spelling_variants_and_abbreviations(resolver):
    assert locate(resolver, "Gurgaon tops dengue chart as tally rises to 60").district == "Gurugram"
    assert locate(resolver, "Dengue surveillance stepped up in Trichy").district == "Tiruchirappalli"
    r = locate(resolver, "Dengue cases cross 16,000 in TN", "Dengue cases in the state crossed 16,000.")
    assert (r.state, r.level) == ("Tamil Nadu", "state")


def test_dateline_is_not_the_outbreak_location(resolver):
    r = locate(resolver, "Civic body destroys mosquito breeding spots",
               "PUNE: Health officials in Nashik reported 40 new dengue cases this week. "
               "Dengue cases in Nashik have doubled.")
    assert (r.state, r.district) == ("Maharashtra", "Nashik")
    assert "Pune" not in [c["name"] for c in r.candidates if c["weight"] > 0]


def test_lab_location_is_not_the_outbreak_location(resolver):
    r = locate(resolver, "Vandalur zoo closes after bird flu deaths",
               "Seven waterbirds at Vandalur Zoo died of H5N1 avian influenza. "
               "Samples were sent to the National Institute of High Security Animal Diseases in Bhopal, "
               "which confirmed bird flu.", diseases=("Bird flu",))
    assert (r.state, r.locality) == ("Tamil Nadu", "Vandalur")


def test_ambiguous_name_resolved_by_state_in_article_or_flagged(resolver):
    r = locate(resolver, "Dengue cases rise in Aurangabad", "Bihar's Aurangabad district reported 30 dengue cases.")
    assert (r.state, r.district) == ("Bihar", "Aurangabad")
    r = locate(resolver, "Dengue cases rise in Aurangabad", "The district reported 30 dengue cases.")
    assert r.review and r.district is None


def test_dateline_used_when_outbreak_sentences_refer_to_it(resolver):
    r = locate(resolver, "Dengue cases reach 236 as administration intensifies fogging",
               "Noida: Dengue cases in the city crossed the 200-mark, with 236 dengue cases till Thursday.")
    assert (r.state, r.district) == ("Uttar Pradesh", "Gautam Buddha Nagar")
    assert any("dateline place" in reason for reason in r.reasons)


def test_dateline_used_when_only_its_own_state_is_named(resolver):
    r = locate(resolver, "Dengue situation remains under watch",
               "Bhopal: The dengue situation remains under watch. Dengue cases in Madhya Pradesh are stable. "
               "Health officials in Bhopal reported 3 dengue cases.")
    assert (r.state, r.district) == ("Madhya Pradesh", "Bhopal")


def test_headline_first_place_decides_between_state_and_district(resolver):
    r = locate(resolver, "Haryana dengue cases jump 58% in 20 days; Gurgaon sees highest tally",
               "Haryana's dengue tally rose to 503 cases.")
    assert (r.state, r.level) == ("Haryana", "state")
    r = locate(resolver, "Gurugram dengue cases rise to 66 as Haryana cases cross 500",
               "Gurugram has recorded 66 dengue cases so far this year.")
    assert (r.state, r.district) == ("Haryana", "Gurugram")
    r = locate(resolver, "Gujarat: Dengue cases hit BSF personnel in Gandhinagar",
               "Dengue cases were reported among BSF personnel in Gandhinagar.")
    assert r.district == "Gandhinagar"  # leading "Gujarat:" is a label


def test_hospital_town_is_not_the_outbreak_location(resolver):
    r = locate(resolver, "Boy dies of dengue in Coimbatore Medical College Hospital",
               "A four-year-old boy from Tiruppur died of dengue. The boy from Tiruppur had dengue fever for days.")
    assert r.district == "Tiruppur"


def test_state_level_report_when_headline_names_state_only(resolver):
    r = locate(resolver, "Kerala reports 300 dengue cases in a week",
               "Dengue cases rose in Thiruvananthapuram. Dengue cases also rose in Kozhikode and Ernakulam.")
    assert (r.state, r.district, r.level) == ("Kerala", None, "state")


def test_locality_within_district(resolver):
    r = locate(resolver, "Dengue cases reported in Kadayanallur",
               "Health officials reported 12 dengue cases in Kadayanallur in Tenkasi district.")
    assert (r.state, r.district, r.locality, r.level) == ("Tamil Nadu", "Tenkasi", "Kadayanallur", "locality")


def test_common_words_and_people_are_not_places(resolver):
    r = locate(resolver, "Bed scarcity hits city hospitals as dengue surge delays surgeries",
               "Hospitals in Pune reported a dengue surge.")
    assert r.district == "Pune"
    r = locate(resolver, "East zone reports dengue cases", "Officials said 20 dengue cases were reported.")
    assert r.country is None


def test_outbreak_abroad(resolver):
    r = locate(resolver, "Pennsylvania measles cases rise to 1,317, state health department says",
               "Pennsylvania reported 1,317 measles cases.", diseases=("Measles",))
    assert (r.country, r.level, r.latitude) == ("United States", "country", None)
    r = locate(resolver, "Dhaka hospitals struggle to find space for dengue patients",
               "Hospitals in Dhaka admitted 747 dengue patients across Bangladesh.")
    assert r.country == "Bangladesh"


class FakeGeocoder:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def locate(self, name, district, state):
        self.calls.append((name, district, state))
        return self.answer if self.answer and self.answer["state"] == state else None


def test_village_geocoded_inside_district(resolver):
    geo = FakeGeocoder({"lat": 26.7, "lon": 94.9, "state": "Nagaland"})
    village_resolver = LocationResolver(places=resolver.places, foreign=resolver.foreign, geocoder=geo)
    r = locate(village_resolver, "Measles outbreak in Mon's Tizit", "The measles outbreak in Tizit village of Mon district "
               "has affected 40 children.", diseases=("Measles",))
    assert (r.state, r.district, r.locality, r.source) == ("Nagaland", "Mon", "Tizit", "nominatim")
    assert ("Tizit", "Mon", "Nagaland") in geo.calls


def test_geocoder_caches_answers_but_not_failures():
    factory = get_session_factory("sqlite://")
    geocoder = NominatimGeocoder(factory, "test-agent", min_delay_seconds=0)
    hit = MagicMock(latitude=26.7, longitude=94.9, raw={"display_name": "Tizit", "class": "place", "type": "village",
                                                         "address": {"state": "Nagaland"}})
    geocoder.client = MagicMock(geocode=MagicMock(side_effect=[TimeoutError("slow"), hit]))
    assert geocoder.locate("Tizit", "Mon", "Nagaland") is None       # failure: not cached
    assert geocoder.locate("Tizit", "Mon", "Nagaland")["lat"] == 26.7  # second call reaches the service
    assert geocoder.locate("Tizit", "Mon", "Nagaland")["lat"] == 26.7  # third call is served from the cache
    assert geocoder.client.geocode.call_count == 2
    with factory() as s:
        assert s.get(GeocodeCache, "Tizit, Mon, Nagaland, India").result["state"] == "Nagaland"


def test_geocoded_result_must_be_a_place_in_the_same_state():
    factory = get_session_factory("sqlite://")
    geocoder = NominatimGeocoder(factory, "test-agent", min_delay_seconds=0)
    other_state = MagicMock(latitude=25.0, longitude=85.0, raw={"class": "place", "address": {"state": "Bihar"}})
    clinic = MagicMock(latitude=32.7, longitude=74.8, raw={"class": "amenity", "type": "clinic",
                                                          "address": {"state": "Jammu and Kashmir"}})
    geocoder.client = MagicMock(geocode=MagicMock(side_effect=[other_state, clinic]))
    assert geocoder.locate("Badi", "Adilabad", "Telangana") is None
    assert geocoder.locate("Sharma", "Jammu", "Jammu and Kashmir") is None


def test_only_place_like_names_are_geocoded(resolver):
    geo = FakeGeocoder(None)
    r = LocationResolver(places=resolver.places, foreign=resolver.foreign, geocoder=geo)
    locate(r, "Dengue cases rise in Pune", "Dr Sharma said 40 dengue cases were found in Sept. "
           "IgM tests confirmed dengue in Kothrud.")
    assert [c[0] for c in geo.calls] == ["Kothrud"]
