from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devdash.cli.main import app
from devdash.core import config as cfgmod
from devdash.core import db, projects
from devdash.core.ai import context as ai_context

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path


def conn(home: Path):
    c = db.connect(cfgmod.db_path(home))
    db.init_db(c)
    return c


def test_schema_v5_adds_languages_and_migrates_v4(home):
    # simulate a v4 database: drop the column, stamp version 4
    c = conn(home)
    p = projects.create(c, "Old", engine="unity", color="#2563eb")
    c.execute("ALTER TABLE projects DROP COLUMN languages")
    c.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
    c.commit()
    c.close()
    # reopen: migration must add the column back and keep the row
    c = conn(home)
    assert db.schema_version(c) == 5
    cols = {r["name"] for r in c.execute("PRAGMA table_info(projects)")}
    assert "languages" in cols
    p2 = projects.get(c, p.id)
    assert p2 is not None and p2.languages == []
    c.close()


def test_languages_crud_and_validation(home):
    with conn(home) as c:
        p = projects.create(c, "Game", languages=["C#", "gdscript", "C#", ""])
        assert p.languages == ["c#", "gdscript"]  # normalized + deduped
        p = projects.update(c, p.id, languages=["c++"])
        assert p.languages == ["c++"]
        p = projects.update(c, p.id, languages=[])  # explicit empty list clears
        assert p.languages == []
        p = projects.update(c, p.id)  # no languages arg -> unchanged
        assert p.languages == []
        with pytest.raises(ValueError):
            projects.create(c, "Bad", languages="csharp")  # not a list
        with pytest.raises(ValueError):
            projects.create(c, "Bad2", languages=["x" * 40])  # too long


def test_api_languages(home):
    from devdash.gui.api import Api, ApiError
    api = Api(home=home)
    p = api.projects_create({"name": "API Game", "engine": "godot",
                              "languages": ["gdscript", "C#"], "set_active": True})
    assert p["languages"] == ["gdscript", "c#"]
    p = api.projects_update({"ref": p["slug"], "languages": ["gdscript"]})
    assert p["languages"] == ["gdscript"]
    with pytest.raises(ApiError):
        api.projects_create({"name": "X", "languages": "gdscript"})  # wrong type -> 400


def test_cli_lang_option(home):
    r = runner.invoke(app, ["--json", "projects", "create", "CLI Game",
                            "--lang", "csharp", "--lang", "gdscript", "--set-active"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["languages"] == ["csharp", "gdscript"]
    r = runner.invoke(app, ["--json", "projects", "update", "cli-game", "--lang", "c++"])
    assert json.loads(r.output)["languages"] == ["c++"]


def test_ai_context_mentions_languages(home):
    with conn(home) as c:
        p = projects.create(c, "Game", engine="unity", languages=["csharp"])
        projects.set_active(c, p.id)
        text = ai_context.build_context(c)
        assert "langages: csharp" in text


def test_gui_theming_wiring():
    html = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "devdash/gui/static/styles.css").read_text(encoding="utf-8")
    # language inputs in both project modals
    assert 'id="f-langs"' in html and 'id="e-langs"' in html
    assert 'parseLangs($("#f-langs").value)' in js and 'parseLangs($("#e-langs").value)' in js
    # project color drives the --proj theme variable
    assert 'setProperty("--proj"' in js and "--proj: var(--accent)" in css
    assert "var(--proj)" in css  # used by header/tab/sidebar highlight
    # engine color dots
    assert "ENGINE_COLORS" in js and "engineColor(" in js and ".dot.eng" in css
    assert "lang-badge" in css and "lang-badge" in js
