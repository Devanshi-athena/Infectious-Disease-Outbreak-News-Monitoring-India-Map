"""LGD codes (Local Government Directory) for the reference places."""

from types import SimpleNamespace

import pytest

from outbreak_monitor.gazetteer import load_india
from outbreak_monitor.lgd import lgd_codes
from outbreak_monitor.location import set_lgd_codes

LGD = lgd_codes()
pytestmark = pytest.mark.skipif(LGD is None, reason="LGD lists not downloaded")


def test_every_reference_state_and_district_has_an_lgd_code():
    places = load_india()
    assert {p.name for p in places if p.kind == "state"} <= set(LGD.states)
    assert all((p.state, p.name) in LGD.districts for p in places if p.kind == "district")


@pytest.mark.parametrize("place, expected", [
    (("Maharashtra", "Aurangabad"), (27, 469, None)),           # renamed: Chhatrapati Sambhajinagar
    (("Odisha", "Cuttack"), (21, 350, None)),                   # LGD spelling: Kataka
    (("Haryana", "Charki Dadri"), (6, 701, None)),              # close spelling: Charkhi Dadri
    (("Delhi", "Shahdara"), (7, 81, 437)),                      # a sub-district of North East in LGD
    (("Puducherry", "Mahe"), (34, 600, 5913)),
    (("Uttar Pradesh", "Budaun", "Bisauli"), (9, 133, 779)),    # locality = one sub-district of the district
    (("Kerala",), (32, None, None)),                            # state level only
])
def test_codes(place, expected):
    codes = LGD.codes(*place)
    assert (codes["state_lgd_code"], codes["district_lgd_code"], codes["subdistrict_lgd_code"]) == expected


def test_locality_that_is_not_a_subdistrict_gets_no_subdistrict_code():
    assert LGD.codes("Uttar Pradesh", "Budaun", "Jagat Pipari")["subdistrict_lgd_code"] is None


def test_codes_are_set_for_india_only():
    record = SimpleNamespace()
    set_lgd_codes(record, "India", "Uttar Pradesh", "Budaun")
    assert (record.state_lgd_code, record.district_lgd_code) == (9, 133)
    set_lgd_codes(record, "Bangladesh", "Dhaka")
    assert record.state_lgd_code is None and record.district_lgd_code is None


village_list = pytest.mark.skipif(LGD is None or not LGD._villages(), reason="LGD village list not downloaded")


@village_list
def test_village_code_needs_the_geocoded_subdistrict():
    codes = LGD.codes("Madhya Pradesh", "Balaghat", "Bondari", "Bondari, Birsa Tahsil, Balaghat, Madhya Pradesh, India")
    assert (codes["subdistrict_lgd_code"], codes["village_lgd_code"]) == (7033, 498052)
    assert LGD.codes("Madhya Pradesh", "Balaghat", "Bondari")["village_lgd_code"] is None   # no address


@village_list
def test_no_village_code_for_city_areas_towns_or_another_districts_village():
    # Hadapsar is a village in LGD but a part of Pune city
    assert LGD.codes("Maharashtra", "Pune", "Hadapsar",
                     "Hadapsar, Pune, Pune City Subdistrict, Pune, Maharashtra, 411001, India")["village_lgd_code"] is None
    # LGD's Rajpur in Kanpur Nagar is not the Rajpur of Kanpur Dehat that the article meant
    assert LGD.codes("Uttar Pradesh", "Kanpur", "Rajpur",
                     "Rajpur, Sikandra, Kanpur Dehat, Uttar Pradesh, 209115, India")["village_lgd_code"] is None
    assert LGD.codes("Maharashtra", "Kolhapur", "Ichalkaranji", "Ichalkaranji, Hatkanangle, Kolhapur")["village_lgd_code"] is None
