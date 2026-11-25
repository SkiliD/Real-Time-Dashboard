# tests -- audit batch P2 (medium security S5-S11 + medium bugs B2-B6)
# Works on Windows, Mac and Linux.

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from devdash.core import config as cfgmod, db, gitwatch, projects as proj, tasks as tsk
from devdash.core.ai import _is_loopback
from devdash.core.ai import context as ctx_mod
from devdash.core import backup as backup_mod
from devdash.core import sysopen
from devdash.core import i18n
from devdash.gui.api import Api, ApiError


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def conn(home):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture()
def api(home) -> Api:
    a = Api(home=home)
    a.projects_create({"name": "Game", "engine": "unity", "set_active": True})
    return a


def _g(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# --- S5: loopback detection resolves DNS (no "127."-prefix bypass) --------------

def test_is_loopback_resolves_instead_of_prefix_matching():
    assert _is_loopback("http://localhost:11434") is True
    assert _is_loopback("http://127.0.0.1:11434") is True
    assert _is_loopback("http://[::1]:8080/v1") is True
    assert _is_loopback("http://127.0.0.1.invalid/v1") is False   # unresolvable -> fail closed
    assert _is_loopback("https://api.mistral.ai/v1") is False
    assert _is_loopback("not a url") is False


# --- S6: backup/export temp files are created with O_EXCL (mkstemp) -------------

def test_backup_and_export_use_exclusive_temp_files(home, tmp_path):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    proj.create(c, "P")
    c.close()
    out = tmp_path / "backups"
    r = backup_mod.make_backup(home=home, out_dir=out)
    zip_path = Path(r["path"])
    assert zip_path.parent == out and zip_path.name.startswith("devdash-backup-")
    assert not zip_path.is_symlink()
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        assert "devdash.db" in z.namelist()
    # no leftover snapshot temp files
    assert not list(out.glob(".devdash-snapshot-*"))
    r2 = backup_mod.export_json(home=home, out_dir=out)
    p2 = Path(r2["path"])
    assert p2.name.startswith("devdash-export-") and json.loads(p2.read_text(encoding="utf-8"))["tables"]


# --- S7: ensure_gitignore refuses to write through a symlink --------------------

def test_ensure_gitignore_refuses_symlink(git_repo, tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    gi = git_repo / ".gitignore"
    gi.write_text("orig", encoding="utf-8")
    gi.unlink()
    try:
        os.symlink(target, gi)
    except OSError:
        pytest.skip("symlink creation needs privileges on this platform")
    with pytest.raises(gitwatch.GitError):
        gitwatch.ensure_gitignore(str(git_repo))
    assert target.read_text(encoding="utf-8") == "outside"  # untouched


# --- S9: AI context is delimited as untrusted data -------------------------------

def test_build_context_delimits_untrusted_data(conn):
    p = proj.create(conn, "Ctx", engine="unity")
    proj.set_active(conn, p.id)
    text = ctx_mod.build_context(conn, max_chars=100000)
    assert "DÉBUT DES DONNÉES PROJET" in text and "FIN DES DONNÉES PROJET" in text


# --- S10: sysopen only opens directories -----------------------------------------

def test_sysopen_refuses_non_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        sysopen.open_in_explorer(str(f))


# --- S11: git output is decoded as UTF-8 (no locale mojibake) --------------------

def test_git_output_decoded_utf8(git_repo):
    (git_repo / "u.txt").write_text("u", encoding="utf-8")
    _g(git_repo, "add", ".")
    _g(git_repo, "commit", "-m", "fix: émoji 🎉 accents à ç")
    r = gitwatch.run_readonly(str(git_repo), "log --format=%s -1")
    assert r["ok"] and "🎉" in r["stdout"] and "émoji" in r["stdout"]


# --- B2: porcelain/numstat for exotic paths, renames, staged+unstaged sum --------

def test_changes_unicode_filename_and_numstat_match(git_repo):
    f = git_repo / "fichier é ü.txt"
    f.write_text("one\n", encoding="utf-8")
    _g(git_repo, "add", ".")
    _g(git_repo, "commit", "-m", "chore: add unicode file")
    f.write_text("one\ntwo\n", encoding="utf-8")
    ch = gitwatch.changes(str(git_repo))
    assert ch["ok"]
    row = next((x for x in ch["files"] if x["path"] == "fichier é ü.txt"), None)
    assert row is not None, [x["path"] for x in ch["files"]]
    assert row["added"] == 1  # numstat matched the porcelain path (was 0 before the fix)


def test_changes_rename_keeps_destination(git_repo):
    _g(git_repo, "mv", "a.txt", "renamed-a.txt")
    ch = gitwatch.changes(str(git_repo))
    paths = [x["path"] for x in ch["files"]]
    assert "renamed-a.txt" in paths and "a.txt" not in paths


def test_changes_sums_staged_and_unstaged(git_repo):
    f = git_repo / "a.txt"
    f.write_text("a\nline2\n", encoding="utf-8")
    _g(git_repo, "add", "a.txt")          # staged diff
    f.write_text("a\nline2\nline3\n", encoding="utf-8")  # + unstaged diff
    ch = gitwatch.changes(str(git_repo))
    row = next(x for x in ch["files"] if x["path"] == "a.txt")
    u = gitwatch.parse_numstat(gitwatch.run_readonly(str(git_repo), "diff --numstat")["stdout"])
    s = gitwatch.parse_numstat(gitwatch.run_readonly(str(git_repo), "diff --cached --numstat")["stdout"])
    assert u["a.txt"][0] > 0 and s["a.txt"][0] > 0
    assert row["added"] == u["a.txt"][0] + s["a.txt"][0]  # SUMMED, not overwritten by staged


# --- B3: sync_repo rolls back on failure (shared MCP connection stays clean) -----

def test_sync_repo_rolls_back_on_parse_error(conn, git_repo, monkeypatch):
    real = gitwatch.parse_message
    def boom(subject, patterns):
        if "dash" in subject:
            raise ValueError("bad pattern")
        return real(subject, patterns)
    monkeypatch.setattr(gitwatch, "parse_message", boom)
    with pytest.raises(ValueError):
        gitwatch.sync_repo(conn, str(git_repo), cfgmod.DEFAULT_COMMIT_PATTERNS)
    n = conn.execute("SELECT COUNT(*) AS c FROM git_commits").fetchone()["c"]
    assert n == 0  # partial inserts rolled back
    # connection is usable afterwards (no dangling transaction)
    proj.create(conn, "After")
    assert proj.list(conn)


# --- B4: config.json atomic write + locked load-modify-save ----------------------

def test_config_update_config_is_atomic_and_locked(home):
    def set_lang_en(c):
        c.lang = "en"
    cfg = cfgmod.update_config(home, set_lang_en)
    assert cfg.lang == "en"
    path = cfgmod.config_path(home)
    assert json.loads(path.read_text(encoding="utf-8"))["lang"] == "en"
    assert not list(path.parent.glob(".config-*.tmp"))  # no temp litter
    # concurrent updates: every cycle is load->modify->save under the lock,
    # so the file is always a complete valid JSON (never torn / never lost keys)
    def worker(i):
        cfgmod.update_config(home, lambda c: setattr(c, "lang", "fr" if i % 2 else "en"))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["lang"] in ("fr", "en") and "ai_provider" in data


def test_api_session_stop_note_type_validated(api):
    api.session_start({"kind": "focus", "minutes": 25})
    with pytest.raises(ApiError) as ei:
        api.session_stop({"note": {"x": 1}})  # wrong type -> clean 400, not a 500
    assert ei.value.status == 400


def test_tasks_update_tags_none_clears_and_string_rejected(conn):
    p = proj.create(conn, "T")
    t = tsk.add(conn, p.id, "task", tags=["a", "b"])
    t = tsk.update(conn, t.id, tags=None)   # None -> [] (NOT NULL column), no TypeError
    assert t.tags == []
    with pytest.raises(ValueError):
        tsk.update(conn, t.id, tags="abc")  # not a list


# --- B5/B6: GUI regression guards (static) ---------------------------------------

def test_gui_p2_fixes_present():
    js = (Path(__file__).parent.parent / "devdash" / "gui" / "static" / "app.js").read_text(encoding="utf-8")
    assert "state.timeByTask," in js and "], renderKanban);" in js       # B5: badge ⏱ refresh (+ session task id for the running-card highlight)
    assert '$("#help-tabs-max")' in js                                   # B6: missing '#'
    assert 't.estimate_min == null ? "" : String(t.estimate_min)' in js  # B6: estimate 0 kept
    assert "state.lastStatsError" in js                                  # B6: no toast spam on poll


def test_tl_keys_added_both_langs():
    for k in ("tl.feature.updated", "tl.feature.deleted", "tl.project.deleted",
              "tl.import", "tl.import.imported"):
        assert k in i18n.FR and k in i18n.EN, k


def test_branches_detached_head_label(git_repo):
    _g(git_repo, "checkout", "--detach", "HEAD")
    br = gitwatch.branches(str(git_repo))
    assert br["ok"] and br["current"] == "(HEAD detached)"
