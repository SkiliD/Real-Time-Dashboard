"""Shared pytest fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A real temporary git repository with 3 commits (one per parsing case)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    g("init")
    g("config", "user.email", "tester@devdash.local")
    g("config", "user.name", "Tester")
    g("config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    g("add", ".")
    g("commit", "-m", "chore: init project")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    g("add", ".")
    g("commit", "-m", "feat(dash): add dash movement")
    (repo / "c.txt").write_text("c", encoding="utf-8")
    g("add", ".")
    g("commit", "-m", "fix: i-frames during dash #2")
    return repo
