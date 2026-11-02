"""Write sample records and the database schema to samples/ (a deliverable; regenerate any time).

    python -m outbreak_monitor.export_samples

Article samples hold metadata only (headline, URL, dates, classification, location): the article
text belongs to the publishers and is not republished.
"""

import csv
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.schema import CreateTable

from .db import Article, Base, OfficialReport, get_session_factory

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_SIZE = 25
ARTICLE_FIELDS = ["article_id", "headline", "source_name", "article_url", "published_at", "collected_at",
                  "collection_method", "disease_tags", "matched_keywords", "relevance", "content_type", "event_status",
                  "relevant_diseases", "relevance_reasons", "relevance_status", "country", "state", "district",
                  "locality", "latitude", "longitude", "location_level", "location_text", "location_confidence",
                  "location_review", "story_id"]
OFFICIAL_FIELDS = ["report_id", "source_name", "report_url", "report_year", "report_week", "state", "district",
                   "disease_raw", "diseases", "cases", "deaths", "onset_date", "reported_date", "status",
                   "action_taken", "latitude", "longitude", "location_level", "location_confidence"]


def _write(path: Path, fields: list[str], rows) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        count = 0
        for row in rows:
            values = [getattr(row, name) for name in fields]
            writer.writerow([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in values])
            count += 1
    return count


def main(session_factory=None, out_dir: Path = SAMPLES_DIR) -> dict:
    session_factory = session_factory or get_session_factory()
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = session_factory.kw["bind"]
    (out_dir / "schema.sql").write_text(
        "\n".join(f"{str(CreateTable(t).compile(engine)).strip()};\n" for t in Base.metadata.sorted_tables),
        encoding="utf-8")
    with session_factory() as s:
        # Newest relevant articles located in India, then a few the classifier rejected (to show both outcomes).
        relevant = s.scalars(select(Article).where(Article.relevance == "relevant", Article.country == "India")
                             .order_by(Article.published_at.desc()).limit(SAMPLE_SIZE - 5)).all()
        rejected = s.scalars(select(Article).where(Article.relevance == "irrelevant")
                             .order_by(Article.published_at.desc()).limit(5)).all()
        official = s.scalars(select(OfficialReport).order_by(OfficialReport.report_year.desc(),
                                                             OfficialReport.report_week.desc(),
                                                             OfficialReport.report_id).limit(SAMPLE_SIZE)).all()
        counts = {"articles": _write(out_dir / "articles_sample.csv", ARTICLE_FIELDS, [*relevant, *rejected]),
                  "official_reports": _write(out_dir / "official_reports_sample.csv", OFFICIAL_FIELDS, official)}
    print(f"Wrote {counts} and schema.sql to {out_dir}")
    return counts


if __name__ == "__main__":
    main()
