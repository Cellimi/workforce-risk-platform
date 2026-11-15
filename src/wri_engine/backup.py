"""Backing up an installation and putting one back.

Everything an installation owns is in its data directory:

    wri.db              accounts, sessions, engagements, memberships, client-specific values,
                        the firm's name and logo, the license usage log
    pseudonym.salt      what makes an analyst's masked employee ids stable; lose it and
                        every masked id changes
    engagements/        each client's files, one folder per engagement
    audit_log.jsonl     who asked for what

A backup is one `.tar.gz` holding those, followed by a `MANIFEST.json` that says which
version of the product made it and the SHA-256 of every file inside. **A backup contains
client HR data.** It deserves the same care as the server it came from.

Three things are done carefully:

* The database is copied with SQLite's own online backup, so a backup taken while the
  product is running is a consistent snapshot and never a half-written file.
* Each file is hashed as it is written, in the same pass, so the manifest describes the
  bytes that are actually in the backup even if a file was growing at the time.
* A restore unpacks into a holding folder and checks every file against the manifest before
  it touches anything. It refuses a backup made by a newer product than this one, and it
  never deletes what it replaces: the existing data is moved into a dated folder beside it.

A backup can be written to, and read from, a stream as well as a file. That is how the
container install gets one out to the host without the two sharing a folder.

An installation that keeps its accounts in another database (`WRI_DATABASE_URL`) backs that
database up with the database's own tools. This module then covers the files only, and the
manifest says so.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from wri_engine import __version__
from wri_engine.settings import Settings
from wri_engine.store.db import SCHEMA_VERSION, stored_schema_version

MANIFEST = "MANIFEST.json"
PRODUCT = "wri"
DATABASE = "wri.db"
AUDIT_LOG = "audit_log.jsonl"
#: Kept beside the database. Anything else in the data directory is not the product's.
FILES = ("pseudonym.salt", AUDIT_LOG)
FOLDERS = ("engagements",)
SET_ASIDE_PREFIX = "replaced-"


class BackupError(Exception):
    """A backup could not be made or put back. The message is written for the operator."""


@dataclass(frozen=True)
class BackupReport:
    files: int
    database_included: bool
    schema_version: int | None
    product_version: str
    set_aside: Path | None = None


def _sqlite_path(settings: Settings) -> Path | None:
    """The database file, when the installation keeps its accounts in the bundled SQLite
    database inside the data directory. None for any other database."""
    url = settings.database_url
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    path = Path(url.removeprefix("sqlite:///"))
    return path if path.resolve().parent == Path(settings.data_dir).resolve() else None


def _snapshot(database: Path, into: Path) -> None:
    """Copy a SQLite database that may be in use, as one consistent moment."""
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(into)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def _whole_lines(log: Path, into: Path) -> None:
    """Copy a log that is being appended to, stopping at the last complete line."""
    data = log.read_bytes()
    into.write_bytes(data[: data.rfind(b"\n") + 1])


class _Hashing:
    """Reads at most `size` bytes of a file and hashes exactly what it hands over."""

    def __init__(self, handle: BinaryIO, size: int):
        self._handle, self._left, self.digest = handle, size, hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        n = self._left if n < 0 else min(n, self._left)
        chunk = self._handle.read(n)
        self._left -= len(chunk)
        self.digest.update(chunk)
        return chunk


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_bytes(archive: tarfile.TarFile, name: str, body: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(body), 0o600
    archive.addfile(info, io.BytesIO(body))


def _archive(target: Path | BinaryIO, mode: str) -> tarfile.TarFile:
    """A gzipped archive on a file, or on a stream that cannot be rewound."""
    if isinstance(target, Path):
        return tarfile.open(target, f"{mode}:gz")
    return tarfile.open(fileobj=target, mode=f"{mode}|gz")


def create_backup(settings: Settings, out: Path | BinaryIO, *, now: datetime) -> BackupReport:
    """Write a backup of the installation to a new file, or to a stream, and say what went
    into it."""
    data_dir = Path(settings.data_dir)
    if not data_dir.is_dir():
        raise BackupError(f"there is no data directory at {data_dir}, so nothing to back up")
    to_file = isinstance(out, Path)
    if to_file:
        if out.exists():
            raise BackupError(f"{out} already exists. Nothing was written; choose another name.")
        if data_dir.resolve() in out.resolve().parents:
            raise BackupError(
                f"{out} is inside the data directory. Write the backup somewhere else, or "
                f"it shares the fate of what it protects."
            )

    database = _sqlite_path(settings)
    with tempfile.TemporaryDirectory() as scratch:
        members: list[tuple[str, Path]] = []
        if database is not None and database.exists():
            _snapshot(database, Path(scratch) / DATABASE)
            members.append((DATABASE, Path(scratch) / DATABASE))
        for name in FILES:
            path = data_dir / name
            if not path.is_file():
                continue
            if name == AUDIT_LOG:
                _whole_lines(path, Path(scratch) / name)
                path = Path(scratch) / name
            members.append((name, path))
        for folder in FOLDERS:
            root = data_dir / folder
            for path in sorted(root.rglob("*")) if root.is_dir() else []:
                if path.is_file() and not path.is_symlink():
                    members.append((path.relative_to(data_dir).as_posix(), path))

        if to_file:
            out.parent.mkdir(parents=True, exist_ok=True)
            # Owner-only from the first byte: a backup holds client HR data.
            out.touch(mode=0o600, exist_ok=False)
        hashes: dict[str, str] = {}
        with _archive(out, "w") as archive:
            for name, path in members:
                info = archive.gettarinfo(str(path), arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as handle:
                    reader = _Hashing(handle, info.size)
                    archive.addfile(info, reader)
                hashes[name] = reader.digest.hexdigest()
            manifest = {
                "product": PRODUCT,
                "product_version": __version__,
                "schema_version": stored_schema_version(settings.database_url)
                if database is not None
                else None,
                "database_included": DATABASE in hashes,
                "created_at": now.replace(microsecond=0).isoformat(),
                "files": hashes,
            }
            # Last, because it describes what was actually written before it.
            _add_bytes(archive, MANIFEST, json.dumps(manifest, indent=2, sort_keys=True).encode())
    return BackupReport(
        files=len(members),
        database_included=manifest["database_included"],
        schema_version=manifest["schema_version"],
        product_version=__version__,
    )


def _safe_name(name: str) -> bool:
    """A file this product would have written: the database, a known file, or something
    under a known folder. Never an absolute path and never one that climbs out."""
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or ".." in parts:
        return False
    return name == DATABASE or name in FILES or (len(parts) > 1 and parts[0] in FOLDERS)


def _existing(data_dir: Path) -> list[Path]:
    names = (DATABASE, *FILES, *FOLDERS)
    return [data_dir / name for name in names if (data_dir / name).exists()]


def _unpack(source: Path | BinaryIO, staging: Path) -> dict:
    """Unpack a backup into a holding folder and return its manifest. Refuses anything this
    product would not have written, before it is written."""
    manifest = None
    try:
        with _archive(source, "r") as archive:
            for member in archive:
                if member.name == MANIFEST:
                    manifest = json.loads(archive.extractfile(member).read())
                    continue
                if not member.isfile() or not _safe_name(member.name):
                    raise BackupError(
                        f"the backup holds something this product did not write "
                        f"({member.name!r}). Nothing was changed."
                    )
                archive.extract(member, staging, filter="data")
    except (OSError, EOFError, tarfile.TarError, ValueError) as exc:
        raise BackupError(
            f"this is not a backup made by this product, or it is damaged ({exc}). "
            f"Nothing was changed."
        ) from None
    if not isinstance(manifest, dict) or manifest.get("product") != PRODUCT:
        raise BackupError("this is not a backup made by this product. Nothing was changed.")
    if not isinstance(manifest.get("files"), dict):
        raise BackupError("the backup has no list of its files. Nothing was changed.")
    return manifest


def _verify(manifest: dict, staging: Path) -> None:
    unpacked = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
    listed = set(manifest["files"])
    if unpacked != listed:
        odd = sorted(unpacked ^ listed)[:3]
        raise BackupError(
            f"the backup's contents do not match its own list of files ({', '.join(odd)}). "
            f"It is incomplete or has been changed. Nothing was changed."
        )
    for name, expected in manifest["files"].items():
        if _sha256(staging / name) != expected:
            raise BackupError(
                f"{name!r} does not match the backup's own record of it; the file is "
                f"damaged or has been changed. Nothing was changed."
            )


def restore_backup(
    settings: Settings, source: Path | BinaryIO, *, replace: bool = False, now: datetime
) -> BackupReport:
    """Put a backup back, from a file or a stream. The product must be stopped.

    Nothing is changed until the whole backup has been unpacked and every file in it matches
    the manifest. If the data directory already holds an installation, `replace` must be
    given, and what was there is moved into a dated folder beside it, not deleted.
    """
    data_dir = Path(settings.data_dir)
    present = _existing(data_dir)
    if present and not replace:
        raise BackupError(
            f"{data_dir} already holds an installation. Nothing was changed. To put the "
            f"backup in its place, say so explicitly (--replace); what is there now will be "
            f"moved aside, not deleted."
        )
    data_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=data_dir))
    try:
        manifest = _unpack(source, staging)
        made_for = manifest.get("schema_version")
        if isinstance(made_for, int) and made_for > SCHEMA_VERSION:
            raise BackupError(
                f"this backup was made by a newer version of the product "
                f"({manifest.get('product_version')}, data version {made_for}); this one "
                f"({__version__}) understands data up to version {SCHEMA_VERSION}. Install "
                f"the newer version first. Nothing was changed."
            )
        if manifest.get("database_included") and _sqlite_path(settings) is None:
            raise BackupError(
                "this backup holds the bundled database, but this installation is set to "
                "use another one (WRI_DATABASE_URL). Nothing was changed."
            )
        _verify(manifest, staging)

        set_aside = None
        if present:
            set_aside = data_dir / f"{SET_ASIDE_PREFIX}{now.strftime('%Y%m%dT%H%M%S')}"
            set_aside.mkdir()
            for path in present:
                shutil.move(str(path), set_aside / path.name)
        for path in sorted(staging.iterdir()):
            shutil.move(str(path), data_dir / path.name)
        for name in (DATABASE, "pseudonym.salt"):
            if (data_dir / name).exists():
                (data_dir / name).chmod(0o600)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return BackupReport(
        files=len(manifest["files"]),
        database_included=bool(manifest.get("database_included")),
        schema_version=made_for if isinstance(made_for, int) else None,
        product_version=str(manifest.get("product_version", "")),
        set_aside=set_aside,
    )
