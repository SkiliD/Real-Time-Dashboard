# tests -- first-launch guided tour (in-app) + illustrated user guide (docs)
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from devdash.core import i18n

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    return tmp_path
HTML = (ROOT / "devdash/gui/static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "devdash/gui/static/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "devdash/gui/static/styles.css").read_text(encoding="utf-8")

TOUR_KEYS = ["tour.skip", "tour.prev", "tour.next", "tour.finish", "tour.step_label", "tour.replay"] + \
            [f"tour.step{i}.{p}" for i in range(1, 8) for p in ("t", "d")]


# --- in-app tour ---------------------------------------------------------------

def test_tour_overlay_markup():
    for el in ('id="tour"', 'id="tour-card"', 'id="tour-title"', 'id="tour-text"',
               'id="tour-step-label"', 'id="tour-skip"', 'id="tour-prev"', 'id="tour-next"',
               'class="tour-backdrop"'):
        assert el in HTML, el
    assert 'role="dialog"' in HTML


def test_tour_engine_and_wiring():
    assert "const TOUR_STEPS" in JS and len(re.findall(r'"#', JS.split("const TOUR_STEPS")[1].split("];")[0])) >= 5
    assert "function startTour()" in JS and "function tourEnd()" in JS and "function tourShow(i)" in JS
    assert 'state.tourDone = true; saveUi()' in JS          # shown once, persisted
    assert "if (!state.tourDone) startTour()" in JS          # auto-start at first boot
    assert "if (tour.active) return tourEnd();" in JS        # Esc closes the tour first
    assert "if (tour.active) tourShow(tour.idx);" in JS      # resize keeps the card anchored
    assert 'saveUi' in JS and "tourDone: state.tourDone" in JS
    assert "u.tourDone === true" in JS                       # restored from localStorage
    assert '$("#btn-tour").onclick' in JS                    # replay from the help modal
    assert 'id="btn-tour"' in HTML


def test_tour_flag_is_server_side_origin_proof():
    # localStorage is per-origin (127.0.0.1:PORT) and the port can change between
    # launches -> the tour flag must ALSO live in the DB (meta table)
    assert 'api.get("/api/tour")' in JS       # boot reads the server flag
    assert 'api.post("/api/tour", { done: true })' in JS  # tourEnd writes it
    srv = (ROOT / "devdash/gui/server.py").read_text(encoding="utf-8")
    assert '"/api/tour": lambda api, q: api.tour_get()' in srv
    assert '"/api/tour": lambda api, body: api.tour_post(body)' in srv


def test_tour_api_over_meta_table(home):
    import pytest
    from devdash.gui.api import Api
    from devdash.gui.api_common import ApiError
    a = Api(home=home)
    assert a.tour_get() == {"done": False}          # default: tour not seen yet
    assert a.tour_post({"done": True}) == {"done": True}
    assert a.tour_get() == {"done": True}           # persisted in meta
    assert a.tour_post({"done": False}) == {"done": False}
    assert a.tour_get() == {"done": False}
    with pytest.raises(ApiError) as e:
        a.tour_post({"done": "yes"})                 # not a boolean -> clean 400
    assert e.value.status == 400


def test_tour_css():
    assert "#tour { position: fixed; inset: 0; z-index: 90; }" in CSS
    assert ".tour-hl" in CSS and "@keyframes tourpulse" in CSS
    assert ".tour-card" in CSS


def test_tour_i18n_keys_both_langs():
    assert set(i18n.FR) == set(i18n.EN)
    for key in TOUR_KEYS:
        assert key in i18n.FR and key in i18n.EN, key
    # dynamic step keys used by tourShow must exist for every step
    for i in range(1, 8):
        assert i18n.tr(f"tour.step{i}.t", lang="fr") != f"tour.step{i}.t"
        assert i18n.tr(f"tour.step{i}.d", lang="en") != f"tour.step{i}.d"
    # static labels inside the tour markup resolve too
    for m in re.findall(r'data-i18n="(tour\.[^"]+)"', HTML):
        assert m in i18n.FR and m in i18n.EN, m


# --- illustrated docs -----------------------------------------------------------

def test_illustrated_guide_files_exist_and_linked():
    fr = ROOT / "docs/guide-utilisation.md"
    en = ROOT / "docs/guide-utilisation.en.md"
    assert fr.is_file() and en.is_file()
    for doc in (fr, en):
        text = doc.read_text(encoding="utf-8")
        for m in re.findall(r"\]\((assets/[^)]+)\)", text):
            assert (doc.parent / m).is_file(), f"{doc.name}: missing image {m}"
    assert "docs/guide-utilisation.md" in (ROOT / "README.fr.md").read_text(encoding="utf-8")
    assert "docs/guide-utilisation.en.md" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_guide_images_are_valid_svg():
    assets = sorted((ROOT / "docs/assets").glob("*.svg"))
    assert len(assets) >= 4
    for svg in assets:
        root = ET.parse(svg).getroot()  # raises if malformed
        assert root.tag.endswith("svg")
