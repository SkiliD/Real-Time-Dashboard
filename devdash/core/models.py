from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .i18n import tr

def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- text length caps (long descriptions are a feature, but unbounded is not) --
MAX_TITLE_LEN = 1_000
MAX_TEXT_LEN = 100_000  # descriptions / devlog entries: room for long-term notes


def check_text(value: str, field: str, max_len: int = MAX_TEXT_LEN) -> str:
    """Cap a user text field; raise a clean ValueError when it is too long."""
    if value is not None and len(value) > max_len:
        raise ValueError(tr("err.text_too_long", field=field, max=max_len, got=len(value)))
    return value


# --- local-day helpers -------------------------------------------------------
# Timestamps are stored in UTC, but "today"/streaks/day buckets are computed in
# the user's LOCAL timezone (converted to UTC for comparison with stored values),
# so a session at 20:00-24:00 local counts on the right day.

def local_now() -> datetime:
    """Current time in the machine's local timezone (tz-aware)."""
    return datetime.now().astimezone()


def day_start_utc(days_ago: int = 0) -> str:
    """Start of the local day `days_ago` days ago, as a UTC ISO string."""
    d = local_now() - timedelta(days=days_ago)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def today_str_local() -> str:
    """Today's date (local timezone) as YYYY-MM-DD."""
    return local_now().date().isoformat()


def to_local_date(iso_utc: str) -> str:
    """Convert a stored UTC ISO timestamp to the local calendar date (YYYY-MM-DD)."""
    return datetime.fromisoformat(iso_utc).astimezone().date().isoformat()


def parse_tags(raw: str) -> list[str]:
    """Parse a stored JSON tags column; corrupt JSON -> empty list (never crash)."""
    try:
        value = json.loads(raw or "[]")
        return [str(t) for t in value] if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def escape_like(q: str) -> str:
    """Escape LIKE wildcards in user input so a search matches literally.
    Used with `LIKE ? ESCAPE '\\'` in tasks.list and logs.list."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class Project:
    id: int
    slug: str
    name: str
    description: str
    engine: str
    color: str
    status: str
    archived: bool
    created_at: str
    updated_at: str
    languages: list[str] = field(default_factory=list)  # JSON array in the row
    path: str = ""  # connected project folder (empty = none); opened in the OS explorer only

    @classmethod
    def from_row(cls, row) -> "Project":
        d = dict(row)
        d["archived"] = bool(d["archived"])
        d["languages"] = parse_tags(d.get("languages"))  # corrupt JSON -> []
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Feature:
    id: int
    project_id: int
    name: str
    description: str
    status: str          # open|proposed|done|rejected
    source: str          # manual|ai
    confidence: float | None
    reasoning: str | None
    feedback: str | None
    created_at: str
    validated_at: str | None
    deleted_at: str | None = None  # soft delete (corbeille); NULL = visible

    @classmethod
    def from_row(cls, row) -> "Feature":
        return cls(**dict(row))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    id: int
    project_id: int
    parent_id: int | None
    title: str
    description: str
    priority: str
    estimate_min: int | None
    status: str
    tags: list[str] = field(default_factory=list)
    feature_id: int | None = None
    order_index: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    deleted_at: str | None = None  # soft delete (corbeille); NULL = visible

    @classmethod
    def from_row(cls, row) -> "Task":
        d = dict(row)
        d["tags"] = parse_tags(d.get("tags", "[]"))
        return cls(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tags"] = list(self.tags)
        return d


@dataclass
class Session:
    id: int
    project_id: int
    task_id: int | None
    kind: str            # focus|pause|custom
    started_at: str
    ended_at: str | None
    duration_min: float | None
    planned_min: int | None = None
    note: str = ""

    @classmethod
    def from_row(cls, row) -> "Session":
        return cls(**dict(row))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LogEntry:
    id: int
    project_id: int
    task_id: int | None
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    deleted_at: str | None = None  # soft delete (corbeille); NULL = visible

    @classmethod
    def from_row(cls, row) -> "LogEntry":
        d = dict(row)
        d["tags"] = parse_tags(d.get("tags", "[]"))
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GitCommit:
    hash: str
    repo_path: str
    short: str
    author: str
    message: str
    branch: str | None
    committed_at: str
    parsed_task_id: int | None
    parsed_feature: str | None

    @classmethod
    def from_row(cls, row) -> "GitCommit":
        return cls(**dict(row))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Event:
    id: int
    entity: str
    entity_id: str | None
    action: str
    payload: dict = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def from_row(cls, row) -> "Event":
        d = dict(row)
        try:
            value = json.loads(d.get("payload") or "{}")
            d["payload"] = value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            d["payload"] = {}  # corrupt payload (e.g. hand-edited import): never crash readers
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)
