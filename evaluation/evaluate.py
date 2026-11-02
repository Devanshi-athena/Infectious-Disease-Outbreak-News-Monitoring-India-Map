"""Evaluation against the labelled article sample (evaluation/labels.csv).

Scores the automatic results stored in the database (not a person's review decisions):
  - relevance: precision, recall and F1 of relevance == "relevant";
  - event status and diseases, on articles both the labels and the system call relevant;
  - location: country, state/UT and district, on relevant articles located in India by the labels.

Labels: relevant yes/no; event_status; diseases ("; "-separated); country; state; district
(empty = state-level report, "?" = a district is involved but not determinable: only the state is scored).
District names are compared through the reference place data, so spelling variants count as equal.

    python -m evaluation.evaluate            # prints the results and writes evaluation/results.md
"""

import csv
from pathlib import Path

from sqlalchemy import select

from outbreak_monitor.config import load_disease_config
from outbreak_monitor.db import Article, get_session_factory
from outbreak_monitor.official import DistrictLocator

HERE = Path(__file__).resolve().parent
LABELS = HERE / "labels.csv"
RESULTS = HERE / "results.md"


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {a / b:.0%}" if b else "n/a"


def main() -> dict:
    with open(LABELS, encoding="utf-8") as f:
        labels = list(csv.DictReader(f))
    with get_session_factory()() as s:
        found = {a.article_url: a for a in s.scalars(select(Article).where(
            Article.article_url.in_([row["article_url"] for row in labels])))}
    locator = DistrictLocator()

    configured = [d.name for d in load_disease_config().diseases]

    def canonical(state: str | None, district: str | None) -> tuple[str | None, str | None]:
        """Reference-data names, so "Ahmedabad"/"Ahmadabad" or "SAS Nagar (Mohali)" compare equal."""
        place = locator.locate(state or None, district or None)
        return place["state"] if place["location_level"] else (state or None), place["district"]

    def disease_name(label: str) -> str:
        """A labelled disease as its configured name ("Influenza" -> "Influenza (H1N1 / H3N2)")."""
        return next((c for c in configured if c.startswith(label) or label.startswith(c)), label)

    tp = fp = fn = tn = 0
    status_ok = disease_exact = disease_overlap = both = 0
    country_ok = state_ok = district_ok = state_n = district_n = flagged = 0
    errors: list[tuple[str, str, str, str]] = []
    missing = 0
    for row in labels:
        a = found.get(row["article_url"])
        if a is None:
            missing += 1
            continue
        want, got = row["relevant"] == "yes", a.relevance == "relevant"
        tp += want and got
        fp += got and not want
        fn += want and not got
        tn += not want and not got
        if want != got:
            errors.append(("relevance", row["headline"], "relevant" if want else "irrelevant",
                           f"{a.relevance} ({a.content_type})"))
        if not (want and got):
            continue
        both += 1
        status_ok += row["event_status"] == (a.event_status or "")
        wanted = {disease_name(d.strip()) for d in row["diseases"].split(";") if d.strip()}
        system = set(a.relevant_diseases or [])
        disease_exact += wanted == system
        disease_overlap += bool(wanted & system)
        if not wanted & system:
            errors.append(("disease", row["headline"], "; ".join(sorted(wanted)), "; ".join(sorted(system))))

        abroad = row["country"] not in ("", "India")
        country_ok += (a.country not in (None, "India")) if abroad else (a.country in (None, "India"))
        if row["country"] != "India" or not row["state"]:
            continue
        flagged += bool(a.location_review)
        want_state, want_district = canonical(row["state"], "" if row["district"] == "?" else row["district"])
        state_n += 1
        got_state, got_district = canonical(a.state, a.district)
        state_hit = got_state == want_state
        state_ok += state_hit
        if row["district"] != "?":
            district_n += 1
            district_hit = state_hit and got_district == want_district
            district_ok += district_hit
        else:
            district_hit = True
        if not (state_hit and district_hit):
            errors.append(("location", row["headline"], f"{want_state} / {row['district'] or '(state level)'}",
                           f"{a.state or '-'} / {a.district or '(state level)'}"
                           f"{' [flagged]' if a.location_review else ''}"))

    precision, recall = tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    results = {"labelled": len(labels), "missing": missing, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
               "precision": precision, "recall": recall, "f1": f1}
    lines = [
        "# Evaluation results", "",
        f"Labelled articles: {len(labels)} (missing from the database: {missing})", "",
        "## Relevance", "",
        "| | Labelled relevant | Labelled irrelevant |", "|---|---|---|",
        f"| **System relevant** | {tp} (true positives) | {fp} (false positives) |",
        f"| **System irrelevant** | {fn} (false negatives) | {tn} (true negatives) |", "",
        f"- Precision: {precision:.0%} · Recall: {recall:.0%} · F1: {f1:.2f}", "",
        f"## On the {both} articles both call relevant", "",
        f"- Event status matches: {pct(status_ok, both)}",
        f"- Diseases exactly the same: {pct(disease_exact, both)}; at least one disease shared: "
        f"{pct(disease_overlap, both)}",
        f"- Inside / outside India correct: {pct(country_ok, both)}",
        f"- State/UT correct (located in India by the labels): {pct(state_ok, state_n)}",
        f"- District level correct (district, or state level when the label has no district): "
        f"{pct(district_ok, district_n)}",
        f"- Flagged for location review: {flagged}/{state_n}", "",
        "## Disagreements", "", "| Kind | Headline | Label | System |", "|---|---|---|---|",
        *[f"| {kind} | {h.replace('|', '/')[:90]} | {want} | {got} |" for kind, h, want, got in errors],
    ]
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return results


if __name__ == "__main__":
    main()
