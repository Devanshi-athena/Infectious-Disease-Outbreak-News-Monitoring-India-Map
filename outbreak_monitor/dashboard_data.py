"""Data behind the dashboard (app.py): loading articles, filters, story markers and review updates.
Kept free of Streamlit so it can be tested on its own."""

import html
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from .db import Article, OfficialReport, last_successful_run

IST = timezone(timedelta(hours=5, minutes=30), "IST")
REVIEW_OPTIONS = ("pending", "relevant", "irrelevant")
# Muted classic tones; always shown together with the text label, never colour alone.
EVENT_COLOURS = {"new": "#B23A48", "suspected": "#C9822B", "historical": "#8A8A8A", "unclear": "#3B6EA5"}
EVENT_LABELS = {"new": "New report", "suspected": "Suspected", "historical": "Historical", "unclear": "Unclear"}
OFFICIAL_COLOUR = "#2E6B4F"   # official surveillance records: green squares (news reports are circles)
VIEWS = {
    "relevant": "Relevant outbreak reports",
    "review": "Relevant, location needs review",
    "all": "All collected articles",
}

COLUMNS = ["article_id", "headline", "source_name", "article_url", "published_at", "collected_at",
           "disease_tags", "relevant_diseases", "relevance", "content_type", "event_status", "relevance_status",
           "country", "state", "district", "locality", "latitude", "longitude", "location_level",
           "location_text", "location_confidence", "location_review", "story_id"]


@dataclass
class Filters:
    view: str = "relevant"
    diseases: list[str] = field(default_factory=list)   # empty = all
    states: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    start: date | None = None
    end: date | None = None
    include_abroad: bool = False


def to_ist(dt: datetime | None) -> datetime | None:
    """Stored times are naive UTC; show them in Indian Standard Time."""
    return dt.replace(tzinfo=timezone.utc).astimezone(IST) if dt else None


def load_articles(session) -> pd.DataFrame:
    """All articles (without the long text) with a few derived columns for display and filtering."""
    rows = session.execute(select(*[getattr(Article, c) for c in COLUMNS])).all()
    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df.assign(diseases=[], effective_relevance=[], published_date=[], place=[])
    df["diseases"] = [list(r or t or []) for r, t in zip(df["relevant_diseases"], df["disease_tags"])]
    # A person's review decision overrides the automatic one.
    df["effective_relevance"] = [s if s in ("relevant", "irrelevant") else a
                                 for s, a in zip(df["relevance_status"], df["relevance"])]
    df["published_date"] = pd.to_datetime(df["published_at"]).dt.date
    df["place"] = [place_label(r) for r in df.itertuples()]
    return df


def place_label(row) -> str:
    """'Tizit, Mon, Nagaland' / 'Kerala' / 'United States (outside India)' / 'Not determined'.
    Missing values arrive from pandas as NaN, so only real strings count."""
    if isinstance(row.country, str) and row.country != "India":
        return f"{row.country} (outside India)"
    parts = [p for p in (row.locality, row.district, row.state) if isinstance(p, str) and p]
    unique = [p for i, p in enumerate(parts) if p not in parts[:i]]
    return ", ".join(unique) or "Not determined"


def options(df: pd.DataFrame) -> dict[str, list]:
    """Values for the filter widgets."""
    if df.empty:
        return {"diseases": [], "states": [], "sources": []}
    return {"diseases": sorted({d for ds in df["diseases"] for d in ds}),
            "states": sorted(s for s in df["state"].dropna().unique()),
            "sources": sorted(df["source_name"].unique())}


def apply_filters(df: pd.DataFrame, f: Filters) -> pd.DataFrame:
    if df.empty:
        return df
    keep = pd.Series(True, index=df.index)
    if f.view in ("relevant", "review"):
        keep &= df["effective_relevance"] == "relevant"
    if f.view == "review":
        keep &= df["location_review"].fillna(False).astype(bool)
    if not f.include_abroad:
        keep &= df["country"].isna() | (df["country"] == "India")
    if f.diseases:
        wanted = set(f.diseases)
        keep &= df["diseases"].map(lambda ds: bool(wanted & set(ds)))
    if f.states:
        keep &= df["state"].isin(f.states)
    if f.sources:
        keep &= df["source_name"].isin(f.sources)
    if f.start:
        keep &= df["published_date"].map(lambda d: d is not None and not pd.isna(d) and d >= f.start)
    if f.end:
        keep &= df["published_date"].map(lambda d: d is not None and not pd.isna(d) and d <= f.end)
    return df[keep].sort_values("published_at", ascending=False)


def summary(df: pd.DataFrame, session) -> dict:
    """Totals shown above the map."""
    located = df[(df["country"] == "India") & df["latitude"].notna()] if not df.empty else df
    locations = {(r.state, r.district, r.locality) for r in located.itertuples()} if not located.empty else set()
    last = last_successful_run(session)
    return {"articles": len(df), "locations": len(locations), "stories": located["story_id"].nunique() if not located.empty else 0,
            "last_refresh": to_ist(last.finished_at) if last else None}


def district_zoom_options(df: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """'District, State' -> centre of the mapped reports in that district, for the zoom selector."""
    if df.empty:
        return {}
    located = df[(df["country"] == "India") & df["latitude"].notna() & df["district"].notna()]
    centres = located.groupby(["district", "state"])[["latitude", "longitude"]].mean()
    return {f"{d}, {s}": (float(lat), float(lon)) for (d, s), (lat, lon) in sorted(centres.iterrows())}


@dataclass
class StoryMarker:
    latitude: float
    longitude: float
    event_status: str | None
    headline: str
    popup_html: str
    article_ids: list[int]


def story_markers(df: pd.DataFrame) -> list[StoryMarker]:
    """One marker per story (the same report from several publishers), placed at its location.
    Only reports located in India with coordinates are mapped."""
    if df.empty:
        return []
    located = df[(df["country"] == "India") & df["latitude"].notna() & df["longitude"].notna()]
    markers = []
    for _, group in located.sort_values("published_at").groupby("story_id", sort=False):
        first = group.iloc[0]
        markers.append(StoryMarker(float(first["latitude"]), float(first["longitude"]), first["event_status"],
                                   first["headline"], popup_html(group), [int(i) for i in group["article_id"]]))
    return markers


def _esc(value) -> str:
    """HTML-escape a value from an external site (headlines, URLs, names); '—' if missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return html.escape(str(value))


def published_ist(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
        return None
    return to_ist(value.to_pydatetime() if hasattr(value, "to_pydatetime") else value)


def popup_html(group: pd.DataFrame) -> str:
    """Card-style popup for one story. Every value comes from external sites, so everything is escaped."""
    first = group.iloc[0]
    status = first["event_status"] if first["event_status"] in EVENT_COLOURS else "unclear"
    diseases = ", ".join(sorted({d for ds in group["diseases"] for d in ds})) or "—"
    published = published_ist(first["published_at"])
    review = (" · <span style='color:#B23A48;font-weight:600'>location needs review</span>"
              if bool(first["location_review"]) else "")
    sources = "".join(
        f"<li style='margin:2px 0'><a href=\"{_esc(r.article_url)}\" target=\"_blank\" rel=\"noopener noreferrer\" "
        f"style='color:#1F3A5F'>{_esc(r.source_name)}</a></li>" for r in group.itertuples())
    return (
        "<div style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:13px;color:#222831;width:320px\">"
        f"<div style='background:{EVENT_COLOURS[status]};color:#fff;padding:5px 10px;border-radius:4px 4px 0 0;"
        f"font-size:11px;letter-spacing:.06em;text-transform:uppercase'>{_esc(diseases)} · {EVENT_LABELS[status]}</div>"
        "<div style='padding:8px 10px 4px 10px'>"
        f"<div style=\"font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.3;margin-bottom:6px\">"
        f"{_esc(first['headline'])}</div>"
        f"<div style='margin:2px 0'>📍 {_esc(first['place'])}</div>"
        f"<div style='color:#6B6B6B;font-size:12px;margin:0 0 4px 20px'>{_esc(first['location_level'])} level · "
        f"{_esc(first['location_confidence'])} confidence{review}</div>"
        f"<div style='margin:2px 0'>🗓 {published.strftime('%d %b %Y, %H:%M IST') if published else '—'}</div>"
        f"<div style='margin-top:8px;color:#6B6B6B;font-size:11px;letter-spacing:.06em;text-transform:uppercase'>"
        f"Reported by</div><ul style='margin:2px 0 6px 18px;padding:0'>{sources}</ul></div></div>")


def latest_reports(df: pd.DataFrame, limit: int = 6) -> list[dict]:
    """Newest stories (one per story) for the side panel, newest first."""
    if df.empty:
        return []
    newest = df.sort_values("published_at", ascending=False).drop_duplicates("story_id").head(limit)
    return [{"headline": r.headline, "url": r.article_url, "source": r.source_name, "place": r.place,
             "diseases": ", ".join(r.diseases), "event_status": r.event_status if r.event_status in EVENT_COLOURS
             else "unclear", "published": published_ist(r.published_at)} for r in newest.itertuples()]


def reports_by_state(df: pd.DataFrame, limit: int = 6) -> list[tuple[str, int]]:
    """States with the most matching reports (stories), for orientation only."""
    if df.empty:
        return []
    india = df[(df["country"] == "India") & df["state"].notna()].drop_duplicates("story_id")
    return [(state, int(n)) for state, n in india["state"].value_counts().head(limit).items()]


def save_reviews(session, changes: dict[int, str]) -> int:
    """Store a person's relevance decisions (relevance_status). Returns how many were changed."""
    changed = 0
    for article_id, status in changes.items():
        if status not in REVIEW_OPTIONS:
            raise ValueError(f"invalid review status: {status}")
        article = session.get(Article, int(article_id))
        if article is not None and article.relevance_status != status:
            article.relevance_status = status
            changed += 1
    session.commit()
    return changed


OFFICIAL_COLUMNS = ["report_id", "source_name", "report_url", "report_year", "report_week", "state", "district",
                    "disease_raw", "diseases", "cases", "deaths", "onset_date", "reported_date", "status",
                    "action_taken", "latitude", "longitude", "location_level", "location_confidence"]


def load_official(session) -> pd.DataFrame:
    """Official outbreak records (IDSP weekly reports), newest reporting week first."""
    rows = session.execute(select(*[getattr(OfficialReport, c) for c in OFFICIAL_COLUMNS])).all()
    df = pd.DataFrame(rows, columns=OFFICIAL_COLUMNS)
    if df.empty:
        return df
    df["diseases"] = [list(d or []) for d in df["diseases"]]
    df["week_label"] = [f"Week {w}, {y}" for w, y in zip(df["report_week"], df["report_year"])]
    return df.sort_values(["report_year", "report_week", "report_id"], ascending=[False, False, True])


def filter_official(df: pd.DataFrame, diseases: list[str], states: list[str]) -> pd.DataFrame:
    """Disease and state filters apply; the news publication-date filter does not (these reports are
    published weeks after the outbreak). Records whose disease is not a configured one ("Chickenpox")
    are shown only when no disease is selected."""
    if df.empty:
        return df
    keep = pd.Series(True, index=df.index)
    if diseases:
        wanted = set(diseases)
        keep &= df["diseases"].map(lambda ds: bool(wanted & set(ds)))
    if states:
        keep &= df["state"].isin(states)
    return df[keep]


def official_popup_html(r) -> str:
    """Popup for one official record. Values come from a downloaded PDF, so everything is escaped."""
    def day(d):
        return d.strftime("%d %b %Y") if isinstance(d, date) else "—"
    deaths = "" if r.deaths is None or pd.isna(r.deaths) else f" · {int(r.deaths)} death(s)"
    cases = "—" if r.cases is None or pd.isna(r.cases) else f"{int(r.cases)}"
    place = ", ".join(p for p in (r.district, r.state) if isinstance(p, str)) or "—"
    return (
        "<div style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:13px;color:#222831;width:320px\">"
        f"<div style='background:{OFFICIAL_COLOUR};color:#fff;padding:5px 10px;border-radius:4px 4px 0 0;"
        "font-size:11px;letter-spacing:.06em;text-transform:uppercase'>Official report · IDSP</div>"
        "<div style='padding:8px 10px 4px 10px'>"
        f"<div style=\"font-family:Georgia,'Times New Roman',serif;font-size:15px;margin-bottom:6px\">"
        f"{_esc(r.disease_raw)}: {cases} case(s){deaths}</div>"
        f"<div style='margin:2px 0'>📍 {_esc(place)}</div>"
        f"<div style='margin:2px 0'>🗓 Started {day(r.onset_date)} · reported {day(r.reported_date)}</div>"
        f"<div style='margin:2px 0'>Status: {_esc(r.status)}</div>"
        f"<div style='margin:6px 0 2px 0;color:#6B6B6B;font-size:12px'>ID {_esc(r.report_id)} · {_esc(r.week_label)}"
        f" · <a href=\"{_esc(r.report_url)}\" target=\"_blank\" rel=\"noopener noreferrer\" "
        "style='color:#1F3A5F'>weekly report (PDF)</a></div></div></div>")
