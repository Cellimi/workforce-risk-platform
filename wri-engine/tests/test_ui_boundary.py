"""The UI layer must stay disposable.

`app/` talks to the engine over HTTP and nothing else. If a page needs something the API does
not expose, the fix is a new endpoint, not an import. This test is the guardrail.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
APP_FILES = sorted(APP_DIR.glob("*.py"))


def test_the_app_directory_has_files():
    assert APP_FILES, "no UI files found"


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.name)
def test_the_ui_never_imports_the_engine(path):
    tree = ast.parse(path.read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [name for name in imported if name.split(".")[0] == "wri_engine"]
    assert not offenders, (
        f"{path.name} imports {offenders} directly. The UI must go through the API so it "
        f"stays replaceable without touching the engine."
    )


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: p.name)
def test_the_ui_compiles(path):
    compile(path.read_text(), str(path), "exec")


def test_every_page_carries_the_synthetic_data_caption():
    source = (APP_DIR / "streamlit_app.py").read_text()
    assert "Synthetic data for a fictional county" in source
    # page_header() prints the caption, and every page calls it.
    tree = ast.parse(source)
    page_functions = {
        "executive_summary", "cost_matrix", "drill_down", "assumptions_page", "data_quality"
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in page_functions:
            calls = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            assert "page_header" in calls, f"{node.name} does not render the caption"


def test_headline_figures_have_a_how_it_was_calculated_expander():
    source = (APP_DIR / "streamlit_app.py").read_text()
    assert source.count("how_calculated(") >= 6
    assert 'f"How this number was calculated - {title}"' in source
