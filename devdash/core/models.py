"""Typed data models for the DevDash core (dataclasses + row mappers)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_tags(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(t) for t in value] if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


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

    @classmethod
    def from_row(cls, row) -> "Project":
        d = dict(row)
        d["archived"] = bool(d["archived"])
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

    @classmethod
    def from_row(cls, row) -> "Task":
        d = dict(row)
        d["tags"] = _parse_tags(d.get("tags", "[]"))
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

    @classmethod
    def from_row(cls, row) -> "LogEntry":
        d = dict(row)
        d["tags"] = _parse_tags(d.get("tags", "[]"))
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
        d["payload"] = json.loads(d.get("payload") or "{}")
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)
