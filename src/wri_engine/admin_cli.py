"""`wri-admin` -- set up an installation from the server's own command line.

The first firm administrator cannot be created through the API, because the API requires a
signed-in firm administrator. Whoever installs the product runs this once:

    wri-admin create-user owner@firm.example --name "Pat Owner" --firm-admin

and is asked for a password. Everything after that can be done through the API.

Four more commands look after the installation itself:

    wri-admin status                       what is installed, and whether it is in order
    wri-admin audit [--expect-head N:HASH] whether the access audit log has been edited
    wri-admin backup --out FILE.tar.gz     everything the installation owns, in one file
    wri-admin restore FILE.tar.gz          put a backup back (the product must be stopped)

A backup contains client HR data. See `wri_engine.backup`.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

from wri_engine import __version__
from wri_engine.access.audit import AuditLog
from wri_engine.backup import BackupError, create_backup, restore_backup
from wri_engine.licensing.keys import LicenseError, load_public_key
from wri_engine.settings import Settings
from wri_engine.store import MIN_PASSWORD_LENGTH, SCHEMA_VERSION, SchemaTooNew, Store
from wri_engine.store.db import stored_schema_version


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if first != getpass.getpass("Again: "):
        raise SystemExit("the two passwords did not match")
    return first


def _status(settings: Settings) -> int:
    """Say what is installed and whether it is in order. Changes nothing: it does not
    create the database, and it does not bring an older one up to date."""
    problems: list[str] = []
    data_dir = Path(settings.data_dir)
    print(f"product version     {__version__}")
    print(f"sign-in             {settings.auth_mode}")
    print(f"data directory      {data_dir}")
    if not data_dir.is_dir():
        problems.append(f"the data directory {data_dir} does not exist")
    elif not os.access(data_dir, os.W_OK):
        problems.append(f"the data directory {data_dir} cannot be written to")

    bundled = settings.database_url.startswith("sqlite")
    print(f"database            {'bundled (SQLite)' if bundled else 'external'}")
    try:
        recorded = stored_schema_version(settings.database_url)
    except Exception as exc:  # the operator needs the reason, whatever it is
        recorded = None
        problems.append(f"the database could not be opened: {exc}")
    if recorded is None:
        print("data version        not started yet")
    else:
        print(f"data version        {recorded} (this product reads up to {SCHEMA_VERSION})")
        if recorded > SCHEMA_VERSION:
            problems.append(str(SchemaTooNew(recorded)))
        else:
            store = Store(settings.database_url)
            users = store.list_users()
            print(f"user accounts       {len(users)}")
            if not any(u.is_firm_admin and u.is_active for u in users):
                problems.append(
                    "there is no firm administrator. Create one: "
                    "wri-admin create-user you@firm.example --firm-admin"
                )
            store.close()

    found = AuditLog(settings.audit_log_path).check()
    if found.intact:
        print(
            f"audit log           intact, {found.entries:,} entries"
            + (f" after {found.unchained:,} older unchained lines" if found.unchained else "")
            + f"; head {found.head_seq}:{found.head_hash[:16]}"
        )
    else:
        print(f"audit log           {len(found.problems)} PROBLEM(S): see wri-admin audit")
        problems.append("the audit log does not hold together. Run: wri-admin audit")

    if settings.auth_mode != "local":
        print("license keys        not used in the synthetic walkthrough")
    elif not settings.license_public_key.strip():
        print("license keys        NOT ENFORCED: no verification key is installed")
    else:
        try:
            load_public_key(settings.license_public_key)
            mark = hashlib.sha256(settings.license_public_key.strip().encode()).hexdigest()[:16]
            print(f"license keys        enforced (verification key {mark})")
        except LicenseError as exc:
            problems.append(str(exc))

    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    print("in order" if not problems else f"{len(problems)} problem(s) found")
    return 1 if problems else 0


def _audit(args, settings: Settings) -> int:
    """Check the access audit log, and optionally that it still holds an entry recorded
    earlier: the way to notice entries removed from the end, or a log rebuilt from scratch."""
    path = settings.audit_log_path
    found = AuditLog(path).check()
    print(f"file                {path}")
    print(f"entries             {found.entries:,} chained, {found.unchained:,} older unchained")
    print(f"head                {found.head_seq}:{found.head_hash}")
    problems = list(found.problems)
    if args.expect_head:
        seq_text, _, expected = args.expect_head.partition(":")
        if not seq_text.isdigit() or len(expected) != 64:
            print("wri-admin: --expect-head takes SEQ:HASH as printed above", file=sys.stderr)
            return 2
        if not AuditLog(path).holds(int(seq_text), expected):
            problems.append(
                f"the log no longer holds entry {seq_text} as it was recorded. Entries have "
                f"been removed from the end, or the log has been rebuilt."
            )
    for problem in problems[:50]:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if len(problems) > 50:
        print(f"... and {len(problems) - 50} more", file=sys.stderr)
    if problems:
        print("THE AUDIT LOG DOES NOT HOLD TOGETHER")
        return 1
    print(
        "intact. Keep the head above somewhere the server's administrators cannot change, "
        "and check against it later with --expect-head."
    )
    return 0


def _backup_or_restore(args, settings: Settings) -> int:
    """`-` in place of a file name means the standard stream, which is how a container
    hands a backup to the host. Everything said to the operator then goes to standard
    error, so it can never end up inside the backup."""
    say = lambda line: print(line, file=sys.stderr)  # noqa: E731
    try:
        if args.command == "backup":
            target = sys.stdout.buffer if args.out == "-" else Path(args.out)
            report = create_backup(settings, target, now=datetime.now())
            where = "standard output" if args.out == "-" else args.out
            say(f"backed up {report.files} file(s) to {where}")
            if not report.database_included:
                say("the accounts database is NOT in this backup: back it up separately")
            say("this backup contains client data. Keep it as you would the server.")
        else:
            source = sys.stdin.buffer if args.file == "-" else Path(args.file)
            report = restore_backup(settings, source, replace=args.replace, now=datetime.now())
            say(
                f"restored {report.files} file(s) from a backup made by version "
                f"{report.product_version}"
            )
            if report.set_aside:
                say(f"what was there before is in {report.set_aside}; remove it when sure")
    except BackupError as exc:
        say(f"wri-admin: {exc}")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wri-admin", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-user", help="create a user account")
    create.add_argument("email")
    create.add_argument("--name", default="")
    create.add_argument("--firm-admin", action="store_true")
    create.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )

    sub.add_parser("list-users", help="list user accounts")
    sub.add_parser("status", help="what is installed, and whether it is in order")

    audit = sub.add_parser("audit", help="check whether the access audit log has been edited")
    audit.add_argument(
        "--expect-head",
        metavar="SEQ:HASH",
        help="a head printed earlier; checks the log still holds that entry unchanged",
    )

    backup = sub.add_parser("backup", help="write everything the installation owns to a file")
    backup.add_argument(
        "--out", required=True, help="the .tar.gz to write; must not exist. - for standard output"
    )

    restore = sub.add_parser("restore", help="put a backup back; stop the product first")
    restore.add_argument("file", help="the backup, or - to read it from standard input")
    restore.add_argument(
        "--replace",
        action="store_true",
        help="allow it where an installation already exists; what is there is moved aside",
    )

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "status":
        return _status(settings)
    if args.command == "audit":
        return _audit(args, settings)
    if args.command in ("backup", "restore"):
        return _backup_or_restore(args, settings)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        store = Store(settings.database_url)
    except SchemaTooNew as exc:
        print(f"wri-admin: {exc}", file=sys.stderr)
        return 2

    if args.command == "create-user":
        try:
            user = store.create_user(
                args.email, _password(args), name=args.name, is_firm_admin=args.firm_admin
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        kind = "firm administrator" if user.is_firm_admin else "user"
        print(f"created {kind} {user.email}")
    elif args.command == "list-users":
        for user in store.list_users():
            flags = ("firm-admin " if user.is_firm_admin else "") + (
                "" if user.is_active else "inactive"
            )
            print(f"{user.id}\t{user.email}\t{flags.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
