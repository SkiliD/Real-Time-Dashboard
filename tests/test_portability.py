# tests -- portability

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "devdash"


def _py_sources():
    return sorted(PKG.rglob("*.py"))


def test_no_windows_only_modules_imported():
    bad = re.compile(r"^\s*(import|from)\s+(winreg|mss|wmi|pywintypes)\b", re.M)
    for f in _py_sources():
        text = f.read_text(encoding="utf-8")
        assert not bad.search(text), f"{f.relative_to(ROOT)} imports a Windows-only module"


def test_no_shell_true_in_subprocess_calls():
    for f in _py_sources():
        text = f.read_text(encoding="utf-8")
        assert "shell=True" not in text, f"{f.relative_to(ROOT)} uses shell=True"


def test_startfile_is_guarded():
    # the REST layer is split into gui/api*.py mixins — scan them all
    api = "\n".join((PKG / "gui").glob("api*.py") and
                    [f.read_text(encoding="utf-8") for f in sorted((PKG / "gui").glob("api*.py"))])
    assert 'sys.platform == "win32"' in api and 'hasattr(os, "startfile")' in api


def test_no_hardcoded_windows_paths_in_code():
    # C:\ or C:/ in CODE (i18n user-facing strings and docs may show examples)
    pat = re.compile(r"['\"][A-Za-z]:[\\\\/]")
    for f in _py_sources():
        if f.name == "i18n.py":
            continue  # user-facing example strings (e.g. placeholder paths) live here
        text = f.read_text(encoding="utf-8")
        assert not pat.search(text), f"{f.relative_to(ROOT)} hardcodes a drive path"


def test_home_dir_is_platform_neutral():
    from devdash.core import config
    import inspect
    src = inspect.getsource(config.devdash_home)
    assert "Path.home()" in src and ".devdash" in src
    assert "C:" not in src


def test_gitattributes_normalizes_line_endings():
    # * text=auto keeps LF in the repo so Linux/macOS checkouts are clean
    attr = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "text=auto" in attr


def test_build_script_platform_aware():
    src = (ROOT / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    assert 'sys.platform == "win32"' in src  # .exe name + SmartScreen hint only on Windows


def test_ci_matrix_covers_three_oses():
    wf = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "ubuntu-latest" in wf and "macos-latest" in wf and "windows-latest" in wf
    assert "pytest" in wf


def test_readmes_document_linux_webkit_dependency():
    for f in ["README.md", "README.fr.md"]:
        txt = (ROOT / f).read_text(encoding="utf-8")
        assert "webkit2gtk" in txt.lower(), f  # native window system package on Linux
