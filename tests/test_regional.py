"""Regional-language sources: native keywords, screening, selective translation, storage and display."""

from unittest.mock import patch

import pytest
from sqlalchemy import select

from outbreak_monitor import collector
from outbreak_monitor.config import (Disease, DiseaseConfig, Settings, Source, load_disease_config, load_sources,
                                     native_pattern)
from outbreak_monitor.dashboard_data import (Filters, apply_filters, load_articles, not_on_map, options,
                                             popup_html)
from outbreak_monitor.db import Article, SeenLink, get_session_factory, utcnow
from outbreak_monitor.discovery import Discovery
from outbreak_monitor.matcher import KeywordMatcher
from outbreak_monitor.relevance import RelevanceClassifier, load_relevance_config
from outbreak_monitor.reprocess import reprocess
from outbreak_monitor.translate import mostly_latin, select_sentences, split_sentences

DENGUE = Disease("Dengue", ["dengue", "dengue fever"], native_keywords={"hi": ["डेंगू"]})
CFG = DiseaseConfig([DENGUE], Settings(lookback_days=30), screening_words={"hi": ["बुखार"]})
HINDI = Source("Hindi Daily", domain="hindi.example.in", language="hi")

HEADLINE_HI = "पटना में डेंगू के 25 नए मरीज मिले"
TEXT_HI = ("पटना में डेंगू के 25 नए मरीज मिले हैं। स्वास्थ्य विभाग ने अलर्ट जारी किया है। "
           "शहर में आज बारिश हुई। अस्पतालों में डेंगू के लिए अलग वार्ड बनाए गए हैं।")
ENGLISH = {
    HEADLINE_HI: "25 new dengue patients found in Patna",
    "पटना में डेंगू के 25 नए मरीज मिले हैं।": "25 new dengue patients have been found in Patna.",
    "स्वास्थ्य विभाग ने अलर्ट जारी किया है।": "The health department has issued an alert.",
    "अस्पतालों में डेंगू के लिए अलग वार्ड बनाए गए हैं।": "Separate wards have been set up for dengue in hospitals.",
}


class FakeTranslator:
    """Stands in for IndicTrans2: fixed translations, records every call."""

    def __init__(self, table=None):
        self.table = table if table is not None else ENGLISH
        self.calls = []

    def available(self):
        return True

    def translate(self, language, texts):
        self.calls.append((language, list(texts)))
        return [self.table.get(t, f"[{t}]") for t in texts]


def cand(url, headline=HEADLINE_HI, method="sitemap"):
    return {"url": url, "headline": headline, "published_at": utcnow(), "excerpt": None, "meta_keywords": None,
            "method": method, "listing_url": "https://hindi.example.in/sitemap.xml"}


def run(candidates, pages, translator, factory=None, sources=(HINDI,), cfg=CFG):
    factory = factory or get_session_factory("sqlite://")

    def fake_discover(source, diseases, *, cutoff, deep, max_pages, known_urls, **_):
        return Discovery(candidates=[dict(c) for c in candidates], listings=[
            {"source": source.name, "url": f"https://{source.domain}/sitemap.xml", "kind": "sitemap", "deep": False,
             "ok": True, "error": None}])

    with patch.object(collector, "discover", fake_discover), \
            patch.object(collector, "extract_article", lambda url: pages[url]):
        return collector.collect(factory, list(sources), cfg, translator=translator), factory


def page(text, title=None):
    return {"extraction_status": "ok", "article_text": text, "canonical_url": None, "title": title}


# ---------------------------------------------------------------- configuration and matching

def test_native_pattern_allows_endings_except_short_and_exact_words():
    assert native_pattern("डेंगू") == "डेंगू*"
    assert native_pattern("हाम") == "हाम"            # 3 letters: whole word only
    assert native_pattern("নিপা=") == "নিপা"          # "=": whole word only
    assert native_pattern(" बर्ड   फ्लू ") == "बर्ड फ्लू*"


def test_configuration_files_load_with_all_languages():
    cfg = load_disease_config()
    languages = {lang for d in cfg.diseases for lang in d.native_keywords}
    assert languages == {"hi", "mr", "gu", "bn", "as", "te", "ta", "ur", "kn", "or", "ml", "pa"}
    assert set(cfg.screening_words) == languages
    regional = [s for s in load_sources() if s.language != "en"]
    assert {s.language for s in regional} == languages
    assert len({s.name for s in regional}) == len(regional)   # no duplicate names


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError):
        Source("X", domain="x.in", language="xx")
    with pytest.raises(ValueError):
        Disease("Dengue", ["dengue"], native_keywords={"xx": ["d"]})


@pytest.mark.parametrize("text, expected", [
    ("डेंगू के 25 नए मरीज मिले", ["Dengue"]),
    ("डेंगूने दोघांचा मृत्यू", ["Dengue"]),                 # Marathi ending attached to the word
    ("মালদায় হামলা, আহত ৩", []),                          # "হামলা" (attack) is not measles
    ("হাম রোগে আক্রান্ত শিশু", ["Measles"]),
    ("নিপাত যাক", []),                                     # "নিপাত" (destruction) is not Nipah
    ("டெங்குவால் 3 பேர் உயிரிழப்பு", ["Dengue"]),
    ("കമ്പനി ലാഭം", []),                                   # "പനി" (fever) inside "കമ്പനി" (company)
    ("చికున్‌గున్యా కేసులు", ["Chikungunya"]),          # with and without zero-width non-joiner
    ("చికున్గున్యా కేసులు", ["Chikungunya"]),
    ("ਹੈਜ਼ਾ ਫੈਲਿਆ", ["Cholera"]),                           # nukta, composed or not
    ("மர்ம காய்ச்சலால் 2 பேர் பலி", ["Fever (no diagnosis)"]),  # final virama dropped before the ending
    ("കോവിഡിന് ശേഷം", ["COVID-19"]),
    ("हॉस्टल में फूड पॉइजनिंग, 40 छात्र बीमार", ["Food poisoning"]),
    ("कुत्ते के काटने के 120 मामले", ["Animal bite"]),
    ("ಹಕ್ಕಿ ಜ್ವರ ಭೀತಿ", []),                                  # bird flu: recognised, not monitored
    ("Dengue cases rise in Pune", ["Dengue"]),
    ("anti-dengues", []),
])
def test_native_keywords_of_the_configuration(text, expected):
    assert KeywordMatcher(load_disease_config().diseases).match(text) == expected


# ---------------------------------------------------------------- sentence selection

def test_sentences_split_on_danda_and_only_relevant_ones_are_selected():
    sentences = split_sentences(TEXT_HI)
    assert len(sentences) == 4 and sentences[1] == "स्वास्थ्य विभाग ने अलर्ट जारी किया है।"
    picked = select_sentences(sentences, lambda s: "डेंगू" in s)
    assert picked == [sentences[0], sentences[1], sentences[3]]   # the lead (2) + the dengue sentence, not the rain


def test_mostly_latin():
    assert mostly_latin("Dengue cases rise in Patna")
    assert not mostly_latin(HEADLINE_HI)
    assert not mostly_latin("")


# ---------------------------------------------------------------- collection

def test_regional_article_is_translated_tagged_classified_and_stored():
    translator = FakeTranslator()
    stats, factory = run([cand("https://hindi.example.in/a")], {"https://hindi.example.in/a": page(TEXT_HI)}, translator)
    assert stats["new"] == 1 and stats["translated"] == 1
    # one call: headline + lead + the other dengue sentence; the sentence about rain is not translated
    [(language, texts)] = translator.calls
    assert language == "hi" and len(texts) == 4 and "शहर में आज बारिश हुई।" not in texts
    with factory() as s:
        a = s.scalars(select(Article)).one()
        assert a.language == "hi"
        assert a.headline == "25 new dengue patients found in Patna" and a.headline_original == HEADLINE_HI
        assert a.article_text == TEXT_HI                       # original text kept as published
        assert [p["english"] for p in a.translation][1] == "The health department has issued an alert."
        assert a.disease_tags == ["Dengue"]
        assert a.matched_keywords["Dengue"]["headline_original"] == ["डेंगू*"]
        assert a.relevance == "relevant" and a.content_type == "outbreak_report"


def test_regional_article_without_disease_or_screening_word_is_not_translated():
    translator = FakeTranslator()
    text = "शहर में आज बारिश हुई। यातायात प्रभावित रहा।"
    stats, factory = run([cand("https://hindi.example.in/b", headline="शहर में बारिश", method="keyword_page")],
                         {"https://hindi.example.in/b": page(text, title="शहर में बारिश")}, translator)
    # topic-page links are always downloaded, but headline and text name no disease: nothing translated
    assert stats["new"] == 0 and stats["no_keyword"] == 1 and translator.calls == []
    with factory() as s:
        assert s.get(SeenLink, "https://hindi.example.in/b").outcome == "no_keyword"


def test_screening_word_lets_an_article_through_and_translation_decides():
    head = "पटना में बुखार का प्रकोप"
    text = "पटना में बुखार का प्रकोप फैला है। 40 लोग अस्पताल में भर्ती हैं।"
    translator = FakeTranslator({head: "Fever outbreak in Patna",
                                 "पटना में बुखार का प्रकोप फैला है।": "A fever outbreak has spread in Patna.",
                                 "40 लोग अस्पताल में भर्ती हैं।": "40 people with dengue are in hospital."})
    stats, factory = run([cand("https://hindi.example.in/c", headline=head)], {"https://hindi.example.in/c": page(text)},
                         translator)
    assert stats["new"] == 1    # "बुखार" (fever) screened it in; the English translation names dengue
    with factory() as s:
        assert s.scalars(select(Article)).one().disease_tags == ["Dengue"]


def test_disease_lost_in_translation_still_counts():
    # The translation drops the disease name; the original names it, so the article is still about dengue.
    table = {HEADLINE_HI: "25 new patients found in Patna",
             "पटना में डेंगू के 25 नए मरीज मिले हैं।": "25 new patients have been found in Patna.",
             "स्वास्थ्य विभाग ने अलर्ट जारी किया है।": "The health department has issued an alert.",
             "अस्पतालों में डेंगू के लिए अलग वार्ड बनाए गए हैं।": "Separate wards have been set up in hospitals."}
    stats, factory = run([cand("https://hindi.example.in/d")], {"https://hindi.example.in/d": page(TEXT_HI)},
                         FakeTranslator(table))
    with factory() as s:
        a = s.scalars(select(Article)).one()
        assert a.headline == "25 new patients found in Patna"           # stored translation is unchanged
        assert a.disease_tags == ["Dengue"] and a.relevance == "relevant"


def test_english_article_on_a_regional_site_is_not_translated():
    translator = FakeTranslator()
    text = "Patna reported 25 new dengue cases on Monday. The health department issued an alert. " * 5
    stats, factory = run([cand("https://hindi.example.in/e", headline="Dengue cases rise in Patna")],
                         {"https://hindi.example.in/e": page(text)}, translator)
    assert stats["new"] == 1 and translator.calls == []
    with factory() as s:
        a = s.scalars(select(Article)).one()
        assert a.language == "en" and a.headline_original is None and a.translation is None


def test_sitemap_title_is_the_news_title_not_an_image_caption():
    from outbreak_monitor.discovery import _parse_urlset, _xml
    xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9" '
           'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"><url><loc>https://ml.example.in/a</loc>'
           '<image:image><image:title>WhatsApp Image.jpeg</image:title></image:image>'
           '<news:news><news:title>ഡെങ്കിപ്പനി പടരുന്നു</news:title></news:news></url></urlset>')
    [item] = _parse_urlset(_xml(xml.encode()), None, "sitemap")
    assert item["headline"] == "ഡെങ്കിപ്പനി പടരുന്നു"


def test_english_listing_title_for_an_article_in_the_regional_language():
    # Some sites list an English title in the sitemap; the article (and its page title) are in Hindi.
    translator = FakeTranslator()
    stats, factory = run([cand("https://hindi.example.in/f", headline="Dengue patients found in Patna")],
                         {"https://hindi.example.in/f": page(TEXT_HI, title=HEADLINE_HI)}, translator)
    with factory() as s:
        a = s.scalars(select(Article)).one()
        assert a.language == "hi" and a.headline_original == HEADLINE_HI
        assert a.headline == "25 new dengue patients found in Patna"


def test_regional_sources_are_skipped_without_the_model():
    class Missing(FakeTranslator):
        def available(self):
            return False
    english = Source("English Daily", domain="english.example.in")
    # (the fake discovery hands the Hindi link to the English source too, which must not store it either)
    stats, factory = run([cand("https://hindi.example.in/a")], {"https://hindi.example.in/a": page(TEXT_HI)},
                         Missing(), sources=(HINDI, english))
    assert stats["translated"] == 0
    with factory() as s:
        assert s.scalars(select(Article)).all() == []   # never stored untranslated


def test_reprocess_keeps_the_translation_and_retags_from_both_texts(resolver):
    stats, factory = run([cand("https://hindi.example.in/a")], {"https://hindi.example.in/a": page(TEXT_HI)},
                         FakeTranslator())
    with factory() as s:
        reprocess(s, KeywordMatcher(CFG.diseases), RelevanceClassifier(CFG.diseases, load_relevance_config()),
                  resolver, {d.name: d.keywords for d in CFG.diseases})
        a = s.scalars(select(Article)).one()
        assert a.headline == "25 new dengue patients found in Patna" and a.headline_original == HEADLINE_HI
        assert a.disease_tags == ["Dengue"] and a.relevance == "relevant"


# ---------------------------------------------------------------- dashboard

def test_dashboard_language_filter_and_original_headline():
    _, factory = run([cand("https://hindi.example.in/a")], {"https://hindi.example.in/a": page(TEXT_HI)},
                     FakeTranslator())
    with factory() as s:
        s.add(Article(headline="Dengue cases rise in Pune", source_name="English Daily",
                      article_url="https://english.example.in/x", link_url="https://english.example.in/x",
                      collection_method="rss", published_at=utcnow(), disease_tags=["Dengue"], relevance="relevant"))
        s.commit()
        df = load_articles(s)
    assert options(df)["languages"] == ["English", "Hindi"]
    hindi = apply_filters(df, Filters(view="all", languages=["Hindi"]))
    assert list(hindi["headline"]) == ["25 new dengue patients found in Patna"]
    popup = popup_html(hindi.assign(place="Patna"))
    assert HEADLINE_HI in popup and "Hindi, translated" in popup
    english = apply_filters(df, Filters(view="all", languages=["English"]))
    assert "translated" not in popup_html(english.assign(place="Pune"))


def test_articles_from_india_without_a_place_are_listed_as_not_on_the_map():
    factory = get_session_factory("sqlite://")
    common = dict(source_name="Daily", collection_method="rss", published_at=utcnow(), disease_tags=["Dengue"],
                  relevance="relevant")
    with factory() as s:
        s.add_all([
            Article(headline="Dengue cases rise in Pune", article_url="https://d.in/1", link_url="https://d.in/1",
                    country="India", state="Maharashtra", latitude=18.5, longitude=73.9, **common),
            Article(headline="Dengue cases rise across India", article_url="https://d.in/2", link_url="https://d.in/2",
                    country="India", **common),                                           # India, no state
            Article(headline="Dengue season begins", article_url="https://d.in/3", link_url="https://d.in/3", **common),
            Article(headline="Dengue outbreak in Dhaka", article_url="https://d.in/4", link_url="https://d.in/4",
                    country="Bangladesh", latitude=23.8, longitude=90.4, **common),       # abroad: not listed
        ])
        s.commit()
        df = load_articles(s)
    unmapped = not_on_map(apply_filters(df, Filters(view="relevant")))
    assert sorted(unmapped["headline"]) == ["Dengue cases rise across India", "Dengue season begins"]
    why = dict(zip(unmapped["headline"], unmapped["why_unmapped"]))
    assert why["Dengue cases rise across India"] == "Only India is named, no state or district"
    assert why["Dengue season begins"] == "No Indian place could be found in the article"
