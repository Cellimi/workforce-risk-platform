"""The versions that ship are fixed, checked, and no more than the product uses.

`requirements.lock` is what goes into the image; `requirements-dev.lock` is the same
versions plus the tools for testing. These tests hold the two to `pyproject.toml` and to
each other, so a dependency added in one place and forgotten in another fails here and not
on a licensee's server. They also hold `pyproject.toml` to the code: a library the engine
declares and never imports is dead weight in every install (DuckDB was 65 MB of it).
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from wri_engine.paths import PROJECT_ROOT

PROJECT = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["project"]
CORE = [Requirement(r) for r in PROJECT["dependencies"]]
UI = [Requirement(r) for r in PROJECT["optional-dependencies"]["ui"]]
DEV = [Requirement(r) for r in PROJECT["optional-dependencies"]["dev"]]

#: Why each library the engine declares is there: the module the engine imports from it,
#: or, where nothing imports it, what needs it. Adding a core dependency means adding a
#: line here, which is the moment to ask whether it belongs in `ui` or `dev` instead.
CORE_IS_USED_AS = {
    "pydantic": "pydantic",
    "pyyaml": "yaml",
    "fastapi": "fastapi",
    "sqlalchemy": "sqlalchemy",
    "reportlab": "reportlab",
    "openpyxl": "openpyxl",
    "pillow": "PIL",
    "cryptography": "cryptography",
    "uvicorn": None,  # the server the API is run under; started as a command
    "python-multipart": None,  # FastAPI needs it to read an uploaded file
}
UI_IS_USED_AS = {"streamlit": "streamlit", "plotly": "plotly", "pandas": "pandas", "httpx": "httpx"}


def pins(lock: str) -> dict[str, list[tuple[str, str]]]:
    """Every pinned line of a lock file: name -> [(version, the whole entry)]."""
    text = (PROJECT_ROOT / lock).read_text()
    found: dict[str, list[tuple[str, str]]] = {}
    for entry in re.split(r"\n(?=[A-Za-z0-9])", text):
        match = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;\\]+)", entry)
        if match:
            found.setdefault(canonicalize_name(match.group(1)), []).append((match.group(2), entry))
    return found


RUNTIME = pins("requirements.lock")
DEVELOPMENT = pins("requirements-dev.lock")


def imported_in(folder: str) -> set[str]:
    modules = set()
    for path in (PROJECT_ROOT / folder).rglob("*.py"):
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if match:
                modules.add(match.group(1))
    return modules


# ---------------------------------------------------------------------------
# the lock files
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "lock, required",
    [(RUNTIME, CORE + UI), (DEVELOPMENT, CORE + UI + DEV)],
    ids=["requirements.lock", "requirements-dev.lock"],
)
def test_everything_declared_is_pinned_at_a_version_it_allows(lock, required):
    for requirement in required:
        name = canonicalize_name(requirement.name)
        assert name in lock, f"{requirement.name} is declared but not in the lock: run `make lock`"
        for pinned, _ in lock[name]:
            assert requirement.specifier.contains(pinned), (
                f"the lock pins {requirement.name} at {pinned}, which pyproject.toml "
                f"({requirement.specifier}) does not allow: run `make lock`"
            )


@pytest.mark.parametrize("lock", [RUNTIME, DEVELOPMENT], ids=["runtime", "dev"])
def test_every_pinned_library_carries_a_fingerprint(lock):
    """A download is accepted only if it matches a SHA-256 recorded when the lock was made.
    One entry without a fingerprint and the installer refuses the whole file."""
    assert len(lock) > 40
    for name, entries in lock.items():
        for pinned, entry in entries:
            assert re.search(r"--hash=sha256:[0-9a-f]{64}", entry), f"{name}=={pinned}"


def test_what_is_tested_is_what_ships():
    """Every library in the image is in the development lock at the very same version, so
    the test suite runs on what a licensee receives."""
    for name, entries in RUNTIME.items():
        assert name in DEVELOPMENT, name
        assert {v for v, _ in entries} == {v for v, _ in DEVELOPMENT[name]}, name
    extra = set(DEVELOPMENT) - set(RUNTIME)
    assert {"pytest", "ruff", "hypothesis"} <= extra
    # Nothing that exists only for testing reaches the image.
    assert not {"pytest", "pytest-cov", "ruff", "hypothesis", "pypdf", "coverage"} & set(RUNTIME)


def test_this_environment_is_the_locked_one():
    """`make install` puts exactly the locked versions in place. If this fails, the tests
    are running on something other than what is written down: run `make install`."""
    for name in (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "cryptography",
        "streamlit",
        "pandas",
        "pytest",
    ):
        locked = {v for v, _ in DEVELOPMENT[name]}
        assert version(name) in locked, f"{name}: installed {version(name)}, locked {locked}"


# ---------------------------------------------------------------------------
# no more than the product uses
# ---------------------------------------------------------------------------
def test_the_engine_declares_only_what_it_uses():
    declared = {canonicalize_name(r.name) for r in CORE}
    assert declared == set(CORE_IS_USED_AS), "say in CORE_IS_USED_AS why each one is there"
    engine_imports = imported_in("src")
    for name, module in CORE_IS_USED_AS.items():
        if module is not None:
            assert module in engine_imports, (
                f"{name} is declared but the engine never imports {module}"
            )
    # And the other way: the engine imports nothing third-party it does not declare.
    for module in ("pandas", "numpy", "duckdb", "httpx", "streamlit", "plotly", "requests"):
        assert module not in engine_imports, f"the engine imports {module} without declaring it"


def test_the_screen_declares_what_it_uses():
    declared = {canonicalize_name(r.name) for r in UI}
    assert declared == set(UI_IS_USED_AS)
    screen_imports = imported_in("app")
    for name, module in UI_IS_USED_AS.items():
        assert module in screen_imports, f"{name} is declared for the screen but never imported"


def test_a_library_nothing_uses_is_not_installed():
    assert "duckdb" not in RUNTIME and "duckdb" not in DEVELOPMENT


# ---------------------------------------------------------------------------
# the image is built from the lock
# ---------------------------------------------------------------------------
def test_the_image_installs_from_the_lock_and_nothing_else():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "--no-deps ." in dockerfile and "pip check" in dockerfile
    # Never the open-ended ranges in pyproject.toml.
    assert '".[ui]"' not in dockerfile and "pip install ." not in dockerfile
    # The base image is one exact build, not a tag that moves.
    assert re.search(r"^ARG PYTHON_IMAGE=python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert dockerfile.count("FROM ${PYTHON_IMAGE}") == 2
    # pip is gone from what ships.
    assert "pip uninstall --yes --quiet pip" in dockerfile
    ignore = (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
    assert "!requirements.lock" in ignore and "!requirements-dev.lock" not in ignore


def test_make_install_uses_the_lock():
    makefile = (PROJECT_ROOT / "Makefile").read_text()
    install = makefile[makefile.index("\ninstall:") : makefile.index("\nlock:")]
    commands = [line for line in install.splitlines() if line.startswith("\t")]
    from_lock = [line for line in commands if "requirements-dev.lock" in line]
    assert len(from_lock) == 2 and all("--require-hashes" in line for line in from_lock)
    assert '".[ui,dev]"' not in makefile
    lock = makefile[makefile.index("\nlock:") : makefile.index("\ngenerate:")]
    assert "--generate-hashes" in lock and "-c requirements.lock" in lock


def test_both_locks_cover_the_python_versions_the_project_supports():
    """One lock for 3.11 and 3.12, Linux and a developer's laptop: where a library needs a
    different version on one of them, the lock says which applies where."""
    assert Path(PROJECT_ROOT / "requirements.lock").read_text().count("python_full_version") >= 2
    assert PROJECT["requires-python"] == ">=3.11"
