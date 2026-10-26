from datetime import datetime, timedelta

import pytest

from outbreak_monitor.processing import StoryIndex, clean_headline, headline_numbers

T = datetime(2026, 10, 20)


@pytest.mark.parametrize("title,source,expected", [
    ("Dengue cases rise | Pune News - The Times of India", "Times of India", "Dengue cases rise"),
    ("Dengue cases soar to 72 in Oct, swine flu declines | Nagpur News", "Times of India",
     "Dengue cases soar to 72 in Oct, swine flu declines"),
    ("Dengue persists - The Hindu", "The Hindu", "Dengue persists"),
    ("Opinion | Not Measles, Bangladesh Is Dealing With A Crisis", "NDTV",
     "Opinion | Not Measles, Bangladesh Is Dealing With A Crisis"),   # leading section label is kept
    ("Dengue: Pune | Ward 6 hotspot", "Times of India", "Dengue: Pune | Ward 6 hotspot"),  # no site/news part
])
def test_clean_headline(title, source, expected):
    assert clean_headline(title, source) == expected


def test_headline_numbers_ignore_thousands_separator():
    assert headline_numbers("Cases cross 17,900; 5 deaths") == ("17900", "5")


def test_story_index_rules():
    idx = StoryIndex()
    idx.add("A", "Dengue cases rise to 40 in Pune", T, 1)
    assert idx.is_duplicate("A", "Dengue cases rise to 40 in Pune!", T)                       # republished
    assert not idx.is_duplicate("A", "Dengue cases rise to 48 in Pune", T)                    # new figure
    assert not idx.is_duplicate("A", "Dengue cases rise to 40 in Pune", T + timedelta(days=10))  # weeks later
    assert not idx.is_duplicate("B", "Dengue cases rise to 40 in Pune", T)                    # other source
    assert idx.find_story("B", "Dengue cases rise to 40 in Pune city", T) == 1                # same story
    assert idx.find_story("B", "Dengue cases rise to 41 in Pune", T) is None
    assert idx.find_story("A", "Dengue cases rise to 40 in Pune", T) is None                  # same source
