"""Coverage (recall) check against an independent index. Evaluation only, not used for collection.

For each configured site, Bing News is asked which articles mention each disease search term
(`site:<domain> "<term>"`, last lookback_days). Each result is then looked up in our database:

    stored          in the articles table
    rejected:<why>  downloaded and rejected (seen_links: no_keyword / too_old / duplicate)
    missed          not seen, and a keyword is in its headline or URL  -> a real gap
    missed_body     not seen, keyword only in the body (often a passing mention)

Bing returns ~14 results per query, so this is a sample, not the full list of articles.

    python -m evaluation.recall_check            # prints a table, writes data/recall_check.csv
"""

import csv
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlsplit

import feedparser
from sqlalchemy import select

from outbreak_monitor.config import load_disease_config, load_sources
from outbreak_monitor.db import Article, SeenLink, get_session_factory, utcnow
from outbreak_monitor.fetch import get
from outbreak_monitor.matcher import KeywordMatcher
from outbreak_monitor.urls import canonicalize_url

OUT = Path(__file__).resolve().parent.parent / "data" / "recall_check.csv"
BING = "https://www.bing.com/news/search?q={q}&format=rss&count=50"
STATUSES = ["stored", "rejected", "missed", "missed_body"]


def article_key(url: str) -> str:
    """Numeric id at the end of most news URLs, so URL variants of one article compare equal."""
    m = re.search(r"(\d{6,})(?:\.\w+)?/?$", url)
    return m.group(1) if m else urlsplit(url).path.rstrip("/")


def bing_results(domain: str, terms: list[str], cutoff: datetime) -> dict[str, str]:
    found = {}
    for term in terms:
        feed = feedparser.parse(get(BING.format(q=quote_plus(f'site:{domain} "{term}"'))).content)
        for e in feed.entries:
            t = e.get("published_parsed")
            if t and datetime(*t[:6]) < cutoff:
                continue
            link = parse_qs(urlsplit(e.link).query).get("url", [e.link])[0]  # unwrap Bing click link
            found[canonicalize_url(link)] = e.title
        time.sleep(1)
    return found


def main():
    cfg = load_disease_config()
    terms = sorted({t for d in cfg.diseases for t in d.search_terms})
    matcher = KeywordMatcher(cfg.diseases)
    cutoff = utcnow() - timedelta(days=cfg.settings.lookback_days)

    with get_session_factory()() as s:
        stored = {article_key(u) for row in s.execute(select(Article.article_url, Article.link_url)) for u in row}
        seen = {article_key(u): o for u, o in s.execute(select(SeenLink.url, SeenLink.outcome))}

    rows, table = [], []
    for source in (s for s in load_sources() if s.enabled):
        results = {u: t for u, t in bing_results(source.domain.removeprefix("www."), terms, cutoff).items()
                   if source.owns(urlsplit(u).netloc)}
        counts = Counter()
        for url, title in results.items():
            key = article_key(url)
            if key in stored:
                status = "stored"
            elif key in seen:
                status = f"rejected:{seen[key]}"
            else:
                status = "missed" if matcher.match(title, urlsplit(url).path) else "missed_body"
            counts[status.split(":")[0]] += 1
            rows.append({"source": source.name, "status": status, "title": title, "url": url})
        found_relevant = counts["stored"] + counts["missed"]
        table.append((source.name, len(results), *(counts[k] for k in STATUSES),
                      f"{counts['stored'] / found_relevant:.0%}" if found_relevant else "-"))

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "status", "title", "url"])
        writer.writeheader()
        writer.writerows(rows)

    header = ("Source", "Bing found", *STATUSES, "headline recall")
    print("| " + " | ".join(header) + " |\n|" + "---|" * len(header))
    for r in table:
        print("| " + " | ".join(str(x) for x in r) + " |")
    print(f"\nDetails: {OUT}")


if __name__ == "__main__":
    main()
