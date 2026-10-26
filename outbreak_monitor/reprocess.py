"""Re-apply headline cleanup, keyword tagging, relevance classification, story grouping and outbreak
location to every stored article, using the current configuration files.

Runs automatically at the start of a collection run when config/diseases.yaml, relevance.yaml,
locations.yaml or the reference place data has changed, so stored data always reflects the current
configuration. Manual review status (relevance_status) is kept.

    python -m outbreak_monitor.reprocess
"""

from collections import Counter

from sqlalchemy import select

from .config import load_disease_config
from .db import Article, get_session_factory
from .location import LocationResolver, build_resolver
from .location import apply as apply_location
from .logs import setup_logging
from .matcher import KeywordMatcher
from .processing import StoryIndex, clean_headline, tag_content
from .relevance import NO_KEYWORD, RelevanceClassifier, apply, load_relevance_config


def reprocess(session, matcher: KeywordMatcher, classifier: RelevanceClassifier,
              locator: LocationResolver, keywords: dict[str, list[str]]) -> Counter:
    """Update all articles in place and commit. Returns counts of (relevance, content_type, event_status)."""
    stories = StoryIndex()
    counts = Counter()
    articles = session.scalars(select(Article).order_by(Article.published_at, Article.article_id)).all()
    for a in articles:
        a.headline = clean_headline(a.headline, a.source_name)
        body = a.article_text or a.excerpt
        a.disease_tags, a.matched_keywords = tag_content(matcher, a.headline, body)
        apply(a, classifier.classify(a.headline, body, a.article_url, a.published_at) if a.disease_tags else NO_KEYWORD)
        apply_location(a, locator.locate_article(a.headline, body, a.relevant_diseases or a.disease_tags or [],
                                                 keywords, geocode=a.relevance == "relevant"))
        a.story_id = stories.find_story(a.source_name, a.headline, a.published_at) or a.article_id
        stories.add(a.source_name, a.headline, a.published_at, a.story_id)
        counts[(a.relevance, a.content_type, a.event_status)] += 1
    session.commit()
    return counts


def main():
    setup_logging()
    diseases = load_disease_config().diseases
    factory = get_session_factory()
    with factory() as session:
        counts = reprocess(session, KeywordMatcher(diseases), RelevanceClassifier(diseases, load_relevance_config()),
                           build_resolver(factory), {d.name: d.keywords for d in diseases})
    print(f"Re-processed {sum(counts.values())} articles")
    for (relevance, content_type, status), n in counts.most_common():
        print(f"  {n:4d}  {relevance:10s}  {content_type:24s}  {status or ''}")


if __name__ == "__main__":
    main()
