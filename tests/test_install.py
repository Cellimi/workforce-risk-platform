"""The install package: versions, backups, the upgrade guard, and what ships.

The container itself is built and run by hand before a release (see docs/WORKPLAN.md); what
is held here is everything about it that can be checked without Docker: that a backup comes
back whole or not at all, that an older product leaves newer data alone, that the package
builder cannot be handed the wrong key, and that the files an operator receives say what the
code does.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import stat
import subprocess
import tarfile
import tomllib
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_accounts_engagements import OWNER, SAMPLE, Installation
from wri_engine import __version__, admin_cli
from wri_engine.api.main import create_app
from wri_engine.backup import BackupError, create_backup, restore_backup
from wri_engine.licensing import generate_keypair
from wri_engine.paths import PROJECT_ROOT
from wri_engine.settings import Settings
from wri_engine.store import SCHEMA_VERSION, SchemaTooNew, Store
from wri_engine.store.db import stored_schema_version

DEPLOY = PROJECT_ROOT / "deploy"
NOW = datetime(2026, 11, 15, 9, 30, 0)


def load_packager():
    spec = importlib.util.spec_from_file_location("wri_packager", DEPLOY / "package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def local(data_dir: Path) -> Settings:
    return Settings(auth_mode="local", data_dir=data_dir)


@pytest.fixture
def site(tmp_path):
    if not (SAMPLE / "hr_empl_master.csv").exists():
        pytest.skip("sample export missing; run `make generate`")
    site = Installation(tmp_path / "data")
    site.engagement = site.new_engagement("FY26 review", "Pine Valley County")
    assert site.upload_sample(site.engagement).status_code == 200
    # The audit log is written beside the data in an installation.
    (tmp_path / "data" / "audit_log.jsonl").write_text('{"n": 1}\n{"n": 2}\n{"n": 3, "cut')
    return site


def reopened(data_dir: Path):
    """The product started again on a data directory, signed in as the owner."""
    app = create_app(local(data_dir))
    http = TestClient(app)
    token = http.post("/auth/login", json={"email": OWNER[0], "password": OWNER[1]}).json()["token"]
    return http, {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# one version, said in one place
# ---------------------------------------------------------------------------
def test_the_version_is_the_packages_own(tmp_path):
    declared = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert __version__ == declared
    http = TestClient(create_app(local(tmp_path)))
    assert http.get("/health").json() == {
        "status": "ok",
        "auth_mode": "local",
        "version": declared,
    }
    assert http.get("/openapi.json").json()["info"]["version"] == declared


# ---------------------------------------------------------------------------
# the data version, and an older product meeting newer data
# ---------------------------------------------------------------------------
def test_a_database_records_the_data_version_it_is_at(tmp_path):
    url = f"sqlite:///{tmp_path / 'wri.db'}"
    assert stored_schema_version(url) is None
    assert not (tmp_path / "wri.db").exists()  # looking does not create anything
    Store(url).close()
    assert stored_schema_version(url) == SCHEMA_VERSION


def test_a_database_from_before_versions_were_recorded_is_brought_up_to_date(tmp_path):
    """An installation made before this change has no record of its data version. Opening
    it must keep everything and start the record."""
    url = f"sqlite:///{tmp_path / 'wri.db'}"
    store = Store(url)
    store.create_user(*OWNER, is_firm_admin=True)
    with store.engine.begin() as connection:
        connection.execute(text("DROP TABLE installation"))
    store.close()
    assert stored_schema_version(url) is None

    again = Store(url)
    assert [u.email for u in again.list_users()] == [OWNER[0]]
    again.close()
    assert stored_schema_version(url) == SCHEMA_VERSION


def test_an_older_product_leaves_newer_data_exactly_as_it_found_it(tmp_path):
    """Going back to an older version after an upgrade must not half-open the data. It
    refuses, says what to do, and changes nothing."""
    path = tmp_path / "wri.db"
    url = f"sqlite:///{path}"
    store = Store(url)
    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE installation SET value = '99' WHERE key = 'schema_version'")
        )
        # Something a newer version might have added. It must still be there afterwards.
        connection.execute(text("CREATE TABLE from_the_future (x INTEGER)"))
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(SchemaTooNew, match="newer version of the product") as refused:
        Store(url)
    assert "restore the backup taken before the upgrade" in str(refused.value)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(SchemaTooNew):
        create_app(Settings(auth_mode="local", data_dir=tmp_path))


# ---------------------------------------------------------------------------
# backup and restore
# ---------------------------------------------------------------------------
def test_a_backup_brings_back_everything_on_a_new_server(site, tmp_path):
    before = site.net(site.engagement)
    salt = (tmp_path / "data" / "pseudonym.salt").read_text()
    backup = tmp_path / "out" / "monday.tar.gz"
    report = create_backup(site.settings, backup, now=NOW)
    assert report.database_included and report.schema_version == SCHEMA_VERSION
    # Owner-only: a backup holds client HR data.
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    with tarfile.open(backup) as archive:
        names = archive.getnames()
        manifest = json.loads(archive.extractfile("MANIFEST.json").read())
    assert names[-1] == "MANIFEST.json"
    assert {"wri.db", "pseudonym.salt", "audit_log.jsonl"} <= set(names)
    assert any(n.startswith(f"engagements/{site.engagement}/") for n in names)
    assert manifest["product_version"] == __version__
    assert set(manifest["files"]) == set(names) - {"MANIFEST.json"}

    elsewhere = tmp_path / "new-server"
    restored = restore_backup(local(elsewhere), backup, now=NOW)
    assert restored.files == report.files and restored.set_aside is None
    http, headers = reopened(elsewhere)
    summary = http.get(f"/engagements/{site.engagement}/costs/summary", headers=headers).json()
    assert summary["total_net"] == str(before)
    # Masked ids stay the same across the move, because their salt came too.
    assert (elsewhere / "pseudonym.salt").read_text() == salt
    assert stat.S_IMODE((elsewhere / "wri.db").stat().st_mode) == 0o600


def test_a_backup_of_a_log_in_use_stops_at_the_last_whole_line(site, tmp_path):
    backup = tmp_path / "b.tar.gz"
    create_backup(site.settings, backup, now=NOW)
    with tarfile.open(backup) as archive:
        log = archive.extractfile("audit_log.jsonl").read().decode()
    assert log == '{"n": 1}\n{"n": 2}\n'


def test_a_backup_travels_over_a_stream(site, tmp_path):
    """How the container hands a backup to the host: written to one stream, read from
    another, with no folder shared between them."""
    before = site.net(site.engagement)
    stream = io.BytesIO()
    create_backup(site.settings, stream, now=NOW)
    elsewhere = tmp_path / "streamed"
    restore_backup(local(elsewhere), io.BytesIO(stream.getvalue()), now=NOW)
    http, headers = reopened(elsewhere)
    summary = http.get(f"/engagements/{site.engagement}/costs/summary", headers=headers).json()
    assert summary["total_net"] == str(before)


def test_a_backup_is_not_written_where_it_would_be_lost_or_overwrite(site, tmp_path):
    inside = tmp_path / "data" / "backup.tar.gz"
    with pytest.raises(BackupError, match="inside the data directory"):
        create_backup(site.settings, inside, now=NOW)
    assert not inside.exists()
    out = tmp_path / "b.tar.gz"
    create_backup(site.settings, out, now=NOW)
    with pytest.raises(BackupError, match="already exists"):
        create_backup(site.settings, out, now=NOW)
    with pytest.raises(BackupError, match="nothing to back up"):
        create_backup(local(tmp_path / "nowhere"), tmp_path / "c.tar.gz", now=NOW)


def test_a_restore_never_deletes_what_it_replaces(site, tmp_path):
    data = tmp_path / "data"
    backup = tmp_path / "b.tar.gz"
    create_backup(site.settings, backup, now=NOW)
    site.store.create_user("later@firm.example", "a-later-password-1")
    site.store.close()

    with pytest.raises(BackupError, match="already holds an installation"):
        restore_backup(site.settings, backup, now=NOW)
    report = restore_backup(site.settings, backup, replace=True, now=NOW)
    assert report.set_aside == data / "replaced-20261115T093000"
    # What was there, including the account made after the backup, was kept aside.
    aside = Store(f"sqlite:///{report.set_aside / 'wri.db'}")
    assert "later@firm.example" in [u.email for u in aside.list_users()]
    aside.close()
    assert (report.set_aside / "engagements" / site.engagement).is_dir()
    # And the installation is the one from the backup.
    back = Store(site.settings.database_url)
    assert "later@firm.example" not in [u.email for u in back.list_users()]
    back.close()
    assert not list(data.glob(".restore-*"))


def rebuilt(backup: Path, into: Path, change) -> Path:
    """A copy of a backup with something done to it on the way."""
    with tarfile.open(backup) as source, tarfile.open(into, "w:gz") as target:
        for member in source.getmembers():
            body = source.extractfile(member).read()
            member, body = change(member, body)
            if member is not None:
                member.size = len(body)
                target.addfile(member, io.BytesIO(body))
    return into


def untouched(data: Path) -> dict:
    return {
        p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(data.rglob("*"))
        if p.is_file()
    }


def test_a_damaged_or_altered_backup_changes_nothing(site, tmp_path):
    """Every way a backup can be wrong is caught before the installation is touched."""
    data = tmp_path / "data"
    backup = tmp_path / "b.tar.gz"
    create_backup(site.settings, backup, now=NOW)
    site.store.close()
    before = untouched(data)

    def altered(member, body):
        return (
            (member, body + b"x") if member.name.endswith("hr_empl_master.csv") else (member, body)
        )

    def dropped(member, body):
        return (None, body) if member.name == "pseudonym.salt" else (member, body)

    def escaping(member, body):
        if member.name == "pseudonym.salt":
            member.name = "../outside.txt"
        return member, body

    def foreign(member, body):
        if member.name == "pseudonym.salt":
            member.name = "authorized_keys"
        return member, body

    def from_the_future(member, body):
        if member.name == "MANIFEST.json":
            manifest = json.loads(body)
            manifest["schema_version"] = SCHEMA_VERSION + 1
            body = json.dumps(manifest).encode()
        return member, body

    def no_manifest(member, body):
        return (None, body) if member.name == "MANIFEST.json" else (member, body)

    cases = {
        "does not match the backup's own record": altered,
        "do not match its own list of files": dropped,
        "something this product did not write": escaping,
        "something this product did not write ('authorized_keys')": foreign,
        "newer version of the product": from_the_future,
        "not a backup made by this product": no_manifest,
    }
    for number, (message, change) in enumerate(cases.items()):
        bad = rebuilt(backup, tmp_path / f"bad{number}.tar.gz", change)
        with pytest.raises(BackupError, match=re.escape(message)):
            restore_backup(site.settings, bad, replace=True, now=NOW)
        assert untouched(data) == before, message
        assert not (tmp_path / "outside.txt").exists()

    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"this is not a backup")
    with pytest.raises(BackupError, match="not a backup made by this product, or it is damaged"):
        restore_backup(site.settings, junk, replace=True, now=NOW)
    assert untouched(data) == before


def test_an_external_database_is_said_to_be_outside_the_backup(site, tmp_path):
    """Where the accounts live in the firm's own database server, the backup covers the
    client files and says plainly that the accounts are not in it."""
    external = Settings(
        auth_mode="local",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'elsewhere' / 'accounts.db'}",
    )
    stream = io.BytesIO()
    report = create_backup(external, stream, now=NOW)
    assert report.database_included is False and report.schema_version is None
    with tarfile.open(fileobj=io.BytesIO(stream.getvalue())) as archive:
        assert "wri.db" not in archive.getnames()
    # And a backup that does hold the bundled database is not put into such an installation.
    full = io.BytesIO()
    create_backup(site.settings, full, now=NOW)
    with pytest.raises(BackupError, match="set to use another one"):
        restore_backup(
            Settings(
                auth_mode="local",
                data_dir=tmp_path / "other",
                database_url=f"sqlite:///{tmp_path / 'elsewhere' / 'accounts.db'}",
            ),
            io.BytesIO(full.getvalue()),
            now=NOW,
        )


# ---------------------------------------------------------------------------
# wri-admin: status, backup, restore
# ---------------------------------------------------------------------------
def run_admin(monkeypatch, settings: Settings, *argv: str) -> int:
    monkeypatch.setattr(admin_cli.Settings, "from_env", classmethod(lambda cls: settings))
    return admin_cli.main(list(argv))


def test_status_says_what_is_installed_and_changes_nothing(monkeypatch, capsys, tmp_path):
    data = tmp_path / "data"
    assert run_admin(monkeypatch, local(data), "status") == 1
    out = capsys.readouterr()
    assert "does not exist" in out.err and not data.exists()

    data.mkdir()
    assert run_admin(monkeypatch, local(data), "status") == 0
    out = capsys.readouterr().out
    assert f"product version     {__version__}" in out
    assert "data version        not started yet" in out
    assert "NOT ENFORCED: no verification key is installed" in out
    assert "in order" in out and not (data / "wri.db").exists()

    Store(local(data).database_url).close()
    assert run_admin(monkeypatch, local(data), "status") == 1
    out = capsys.readouterr()
    assert "there is no firm administrator" in out.err and "1 problem(s) found" in out.out

    store = Store(local(data).database_url)
    store.create_user(*OWNER, is_firm_admin=True)
    store.close()
    _private, public = generate_keypair()
    licensed = Settings(auth_mode="local", data_dir=data, license_public_key=public)
    assert run_admin(monkeypatch, licensed, "status") == 0
    out = capsys.readouterr().out
    assert "user accounts       1" in out
    assert re.search(r"license keys        enforced \(verification key [0-9a-f]{16}\)", out)

    broken = Settings(auth_mode="local", data_dir=data, license_public_key="not a key")
    assert run_admin(monkeypatch, broken, "status") == 1
    assert "verification key could not be read" in capsys.readouterr().err


def test_status_reports_data_newer_than_the_product(monkeypatch, capsys, tmp_path):
    settings = local(tmp_path)
    store = Store(settings.database_url)
    with store.engine.begin() as connection:
        connection.execute(text("UPDATE installation SET value = '7' WHERE key = 'schema_version'"))
    store.close()
    assert run_admin(monkeypatch, settings, "status") == 1
    out = capsys.readouterr()
    assert f"data version        7 (this product reads up to {SCHEMA_VERSION})" in out.out
    assert "newer version of the product" in out.err
    # Every other command stops the same way, without a traceback.
    assert run_admin(monkeypatch, settings, "list-users") == 2
    assert "newer version of the product" in capsys.readouterr().err


def test_the_admin_tool_backs_up_and_restores(monkeypatch, capsys, site, tmp_path):
    out = tmp_path / "b.tar.gz"
    assert run_admin(monkeypatch, site.settings, "backup", "--out", str(out)) == 0
    said = capsys.readouterr()
    # Everything said to the operator goes to standard error, never into a streamed backup.
    assert said.out == "" and "contains client data" in said.err
    assert run_admin(monkeypatch, site.settings, "backup", "--out", str(out)) == 2
    assert "already exists" in capsys.readouterr().err

    site.store.close()
    assert run_admin(monkeypatch, site.settings, "restore", str(out)) == 2
    assert "already holds an installation" in capsys.readouterr().err
    assert run_admin(monkeypatch, site.settings, "restore", str(out), "--replace") == 0
    assert "what was there before is in" in capsys.readouterr().err
    elsewhere = local(tmp_path / "elsewhere")
    assert run_admin(monkeypatch, elsewhere, "restore", str(out)) == 0
    assert run_admin(monkeypatch, elsewhere, "list-users") == 0
    assert OWNER[0] in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the package builder and the verification key
# ---------------------------------------------------------------------------
@pytest.fixture
def keys(tmp_path):
    private, public = generate_keypair()
    folder = tmp_path / "keys"
    folder.mkdir()
    (folder / "wri_license_signing_key.pem").write_text(private)
    (folder / "wri_license_public_key.pem").write_text(public)
    return folder


def test_the_package_builder_refuses_the_signing_key(keys, tmp_path):
    """The one mistake that would let every licensee issue their own keys: pointing the
    build at the wrong file of the pair. It stops before anything is written."""
    packager = load_packager()
    out = tmp_path / "dist"
    with pytest.raises(packager.PackageError, match="holds a PRIVATE key") as refused:
        packager.assemble(out, key=keys / "wri_license_signing_key.pem", with_image=False)
    assert "Nothing was built" in str(refused.value) and not out.exists()
    both = keys / "both.pem"
    both.write_text(
        (keys / "wri_license_public_key.pem").read_text()
        + (keys / "wri_license_signing_key.pem").read_text()
    )
    with pytest.raises(packager.PackageError, match="holds a PRIVATE key"):
        packager.assemble(out, key=both, with_image=False)
    assert packager.main(["--license-key", str(both), "--out", str(out), "--no-image"]) == 2
    assert not out.exists()


def test_the_package_builder_needs_a_real_verification_key_or_to_be_told(keys, tmp_path):
    packager = load_packager()
    out = tmp_path / "dist"
    with pytest.raises(packager.PackageError, match="no verification key was given"):
        packager.assemble(out, key=None, with_image=False)
    with pytest.raises(packager.PackageError, match="there is no file at"):
        packager.assemble(out, key=keys / "missing.pem", with_image=False)
    (keys / "junk.pem").write_text(
        "-----BEGIN PUBLIC KEY-----\nnot one\n-----END PUBLIC KEY-----\n"
    )
    with pytest.raises(packager.PackageError, match="not a verification key this product can use"):
        packager.assemble(out, key=keys / "junk.pem", with_image=False)
    with pytest.raises(packager.PackageError, match="not both"):
        packager.assemble(
            out, key=keys / "wri_license_public_key.pem", unlicensed=True, with_image=False
        )
    assert not out.exists()


def test_the_package_holds_what_the_guide_says_and_checks_out(keys, tmp_path):
    packager = load_packager()
    out = tmp_path / "dist"
    archive = packager.assemble(out, key=keys / "wri_license_public_key.pem", with_image=False)
    version = packager.product_version()
    folder = out / f"wri-{version}"
    assert archive == out / f"wri-{version}.tar.gz" and archive.is_file()
    assert sorted(p.name for p in folder.iterdir()) == [
        "INSTALL.md",
        "LICENSE",
        "SHA256SUMS",
        "VERSION",
        "compose.yaml",
        "wri",
        "wri.env.example",
    ]
    assert stat.S_IMODE((folder / "wri").stat().st_mode) == 0o755
    assert "license keys enforced: yes" in (folder / "VERSION").read_text()
    assert "built for: linux/amd64" in (folder / "VERSION").read_text()
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    # The key is baked into the image, never laid beside it in the package.
    assert not list(folder.glob("*.pem"))
    with tarfile.open(archive) as tar:
        assert all(n == f"wri-{version}" or n.startswith(f"wri-{version}/") for n in tar.getnames())
    with pytest.raises(packager.PackageError, match="already exists"):
        packager.assemble(out, key=keys / "wri_license_public_key.pem", with_image=False)

    # With no key, only when asked for by name, and named so it cannot pass for a release.
    packager.assemble(out, key=None, unlicensed=True, with_image=False)
    unlicensed = out / f"wri-{version}-unlicensed"
    assert "license keys enforced: NO" in (unlicensed / "VERSION").read_text()
    assert packager.image_tag(version, licensed=False) == f"wri:{version}-unlicensed"
    assert packager.image_tag(version, licensed=True) == f"wri:{version}"


# ---------------------------------------------------------------------------
# what ships: the image, the services, the script and the guide
# ---------------------------------------------------------------------------
def test_no_key_can_reach_the_image_from_a_checkout():
    """The verification key enters the image only through its own build context, and the
    signing key not at all: every .pem is kept out of the main one."""
    ignore = (PROJECT_ROOT / ".dockerignore").read_text().splitlines()
    assert ignore[: ignore.index("!pyproject.toml")].count("*") == 1
    assert "**/*.pem" in ignore and "**/*.key" in ignore
    assert ignore.index("**/*.pem") > ignore.index("!config")
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    assert "COPY --from=license . ./config/" in dockerfile
    assert not re.search(r"^COPY \. ", dockerfile, re.M)
    # Tests, the prototype and any local data are never in the image.
    for kept_out in ("tests", "legacy", "data", ".git"):
        assert f"!{kept_out}" not in ignore
    assert "/config/license_public_key.pem" in (PROJECT_ROOT / ".gitignore").read_text()


def test_the_image_runs_unprivileged_and_as_one_worker():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER wri$", dockerfile, re.M)
    assert dockerfile.rstrip().endswith('CMD ["api"]')
    entrypoint = (DEPLOY / "entrypoint.sh").read_text()
    # One worker: an engagement's costed results are held in the process that made them.
    assert "--workers 1" in entrypoint and entrypoint.startswith("#!/bin/sh")


def test_the_services_publish_only_the_screen_and_only_to_this_machine():
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    api, ui = compose["services"]["api"], compose["services"]["ui"]
    # The part that holds the data is never published.
    assert "ports" not in api
    assert ui["ports"] == ["${WRI_BIND:-127.0.0.1}:${WRI_PORT:-8501}:8501"]
    assert ui["environment"]["WRI_API_URL"] == "http://api:8000"
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"
    for service in (api, ui):
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service
        assert "privileged" not in service and "network_mode" not in service
    assert api["volumes"] == ["wri-data:/data"] and "volumes" not in ui
    assert list(compose["volumes"]) == ["wri-data"]
    example = (DEPLOY / "wri.env.example").read_text()
    assert re.search(r"^WRI_BIND=127\.0\.0\.1$", example, re.M)


def test_the_operators_script_is_sound_and_never_removes_the_data():
    script = (DEPLOY / "wri").read_text()
    checked = subprocess.run(["bash", "-n", str(DEPLOY / "wri")], capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert "set -euo pipefail" in script
    for forbidden in ("--volumes", "down -v", "volume rm", "volume prune", "system prune"):
        assert forbidden not in script, forbidden
    # An upgrade backs up before it changes anything, with the old image.
    upgrade = script[script.index("cmd_upgrade()") : script.index("cmd_rollback()")]
    assert upgrade.index("write_backup") < upgrade.index("set_setting WRI_IMAGE")
    # A restore and a rollback ask first.
    assert script.count("Type yes to go ahead") == 2


def test_the_guide_covers_every_command_and_every_setting():
    guide = (PROJECT_ROOT / "docs" / "INSTALL.md").read_text()
    script = (DEPLOY / "wri").read_text()
    commands = re.findall(r"^  ([a-z-]+)\) shift", script, re.M)
    assert set(commands) >= {"install", "start", "stop", "status", "backup", "restore", "upgrade"}
    for command in commands:
        assert f"./wri {command}" in guide, command
    settings = re.findall(r"^(WRI_[A-Z_]+)=", (DEPLOY / "wri.env.example").read_text(), re.M)
    assert len(settings) == 6
    for setting in settings:
        assert f"`{setting}`" in guide, setting
    compose = (DEPLOY / "compose.yaml").read_text()
    for setting in settings:
        assert setting in compose, setting
    # The guide does not promise what the product does not do.
    assert "No single sign-on" in guide and "No TLS of its own" in guide
