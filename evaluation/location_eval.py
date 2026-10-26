"""Location accuracy against a labelled sample (evaluation/location_labels.csv).

Each row gives an article URL and the expected state/UT and district:
    expected_district empty -> a state-level report is expected (no district)
    expected_district "?"   -> not determinable from the article; only the state is scored
    expected_state empty    -> no Indian state should be assigned (national/foreign item)

    python -m evaluation.location_eval
"""

import csv
from pathlib import Path

from sqlalchemy import select

from outbreak_monitor.db import Article, get_session_factory

LABELS = Path(__file__).resolve().parent / "location_labels.csv"


def main():
    with open(LABELS, encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    with get_session_factory()() as s:
        found = {a.article_url: a for a in s.scalars(select(Article).where(
            Article.article_url.in_([row["article_url"] for row in labels])))}

    state_ok = district_ok = district_n = flagged = 0
    misses = []
    for row in labels:
        a = found.get(row["article_url"])
        if a is None:
            misses.append(("missing from database", row["headline"], "", ""))
            continue
        got_state, got_district = a.state or "", a.district or ""
        flagged += bool(a.location_review)
        if got_state == row["expected_state"]:
            state_ok += 1
        if row["expected_district"] != "?":
            district_n += 1
            if got_state == row["expected_state"] and got_district == row["expected_district"]:
                district_ok += 1
                continue
        if got_state != row["expected_state"] or row["expected_district"] not in ("?", got_district):
            misses.append((f"{'review' if a.location_review else 'not flagged'}", row["headline"],
                           f"{row['expected_state']} / {row['expected_district'] or '(state-level)'}",
                           f"{got_state or '-'} / {got_district or '(state-level)'}"))

    n = len(labels)
    print(f"Labelled articles: {n}")
    print(f"State/UT correct:       {state_ok}/{n} = {state_ok / n:.0%}")
    print(f"District level correct: {district_ok}/{district_n} = {district_ok / district_n:.0%}")
    print(f"Flagged for review:     {flagged}/{n}")
    print("\nMismatches (flag | headline | expected | got):")
    for flag, headline, expected, got in misses:
        print(f"  [{flag}] {headline[:70]}\n      expected {expected:40s} got {got}")


if __name__ == "__main__":
    main()
