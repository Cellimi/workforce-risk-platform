"""`wri-admin` -- set up an installation from the server's own command line.

The first firm administrator cannot be created through the API, because the API requires a
signed-in firm administrator. Whoever installs the product runs this once:

    wri-admin create-user owner@firm.example --name "Pat Owner" --firm-admin

and is asked for a password. Everything after that can be done through the API.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from wri_engine.settings import Settings
from wri_engine.store import MIN_PASSWORD_LENGTH, Store


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if first != getpass.getpass("Again: "):
        raise SystemExit("the two passwords did not match")
    return first


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

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.database_url)

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
