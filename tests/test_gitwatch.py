"""Git integration tests (step f): real temporary git repo, read-only sync,
commit parsing (#id / feat(name)), task links, status, events, idempotence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.core import config, db, events, gitwatch, projects


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home: Path):
    c = db.connect(home / "devdash.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def project(conn):
    p = projects.create(conn, "Game", engine="unity")
    projects.set_active(conn, p.id)
    return p


def test_parse_message():
    pats = config.DEFAULT_COMMIT_PATTERNS
    assert gitwatch.parse_message("fix: dash #42", pats) == (42, None)
    assert gitwatch.parse_message("task-7: wip", pats) == (7, None)
    assert gitwatch.parse_message("feat(dash): add dash", pats) == (None, "dash")
    assert gitwatch.parse_message("chore: init", pats) == (None, None)


def test_link_sync_parse_status(conn, project, git_repo, home):
    repo = gitwatch.link(conn, project.id, str(git_repo))
    assert gitwatch.get_link(conn, project.id) == repo

    res = gitwatch.sync_repo(conn, repo, config.DEFAULT_COMMIT_PATTERNS)
    assert res["new"] == 3 and res["total"] == 3
    assert res["branch"] in ("main", "master")

    # idempotent: re-sync finds 0 new commits
    res2 = gitwatch.sync_repo(conn, repo, config.DEFAULT_COMMIT_PATTERNS)
    assert res2["new"] == 0

    all_c = gitwatch.commits(conn, project_id=project.id)
    assert len(all_c) == 3

    # parsing: "#2" -> task 2 (even though task 2 does not exist yet: no FK)
    by_task = gitwatch.commits_for_task(conn, 2)
    assert len(by_task) == 1 and "i-frames" in by_task[0].message
    # "feat(dash)" -> feature dash
    feats = [c for c in all_c if c.parsed_feature == "dash"]
    assert len(feats) == 1 and "dash movement" in feats[0].message
    # unparsed: the "chore: init" commit
    unp = gitwatch.unparsed_commits(conn, repo)
    assert len(unp) == 1 and unp[0].message == "chore: init project"

    # status snapshot
    st = gitwatch.status(conn, project.id)
    assert st["linked"] is True and st["branch"] in ("main", "master")
    assert st["last_commit"]["short"] and st["commits_today"] == 3
    assert st["cached_commits"] == 3 and st["last_sync_at"] is not None

    # events: one git.commit event per new commit (only on first sync)
    evs = [e for e in events.since(conn, 0) if e.entity == "git" and e.action == "commit"]
    assert len(evs) == 3
    assert evs[0].payload["repo_path"] == repo


def test_sync_all_and_errors(conn, project, git_repo, tmp_path, home):
    gitwatch.link(conn, project.id, str(git_repo))
    out = gitwatch.sync_all(conn, home=home)
    assert len(out["repos"]) == 1 and out["repos"][0]["new"] == 3
    assert out["errors"] == []


def test_link_rejects_non_repo(conn, project, tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(gitwatch.GitError):
        gitwatch.link(conn, project.id, str(not_a_repo))
    with pytest.raises(gitwatch.GitError):
        gitwatch.sync_repo(conn, str(not_a_repo), config.DEFAULT_COMMIT_PATTERNS)


def test_unlink(conn, project, git_repo):
    gitwatch.link(conn, project.id, str(git_repo))
    gitwatch.unlink(conn, project.id)
    assert gitwatch.get_link(conn, project.id) is None
    assert gitwatch.status(conn, project.id)["linked"] is False


def test_new_commit_detected_on_resync(conn, project, git_repo):
    """The watcher signal: a commit added AFTER the first sync appears on re-sync."""
    import subprocess
    repo = gitwatch.link(conn, project.id, str(git_repo))
    gitwatch.sync_repo(conn, repo, config.DEFAULT_COMMIT_PATTERNS)
    # user commits something new in their repo
    (git_repo / "d.txt").write_text("d", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat(ui): pause menu #5"], cwd=git_repo,
                   check=True, capture_output=True)
    res = gitwatch.sync_repo(conn, repo, config.DEFAULT_COMMIT_PATTERNS)
    assert res["new"] == 1
    c = gitwatch.commits_for_task(conn, 5)
    assert len(c) == 1 and "pause menu" in c[0].message
