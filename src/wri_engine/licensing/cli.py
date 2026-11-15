"""`wri-license` -- the licensor's tool for issuing keys and reading usage logs.

Three things are done here, all on the licensor's own machine:

    wri-license keygen --out-dir ~/wri-keys
        Run ONCE. Writes the signing key (keep it secret, back it up) and the verification
        key (give this to installations).

    wri-license issue --signing-key ~/wri-keys/wri_license_signing_key.pem \\
        --licensee "Northgate Advisory LLP" --client "Ridgeline Builders" \\
        --sector private_sector --expires 2027-12-31
        Prints a key for one engagement.

    wri-license verify-log usage.jsonl
        Reads a usage log a licensee exported and says whether it holds together.

`wri-license inspect` shows what a key says. The signing key is never needed by an
installation and must never be put in one, in this repository, or in a message.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from wri_engine.licensing.keys import (
    LicenseError,
    generate_keypair,
    issue,
    load_private_key,
    load_public_key,
    verify,
)
from wri_engine.licensing.usage import from_jsonl, verify_chain

SIGNING_KEY_FILE = "wri_license_signing_key.pem"
VERIFICATION_KEY_FILE = "wri_license_public_key.pem"


def _keygen(args) -> int:
    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    signing, verification = out / SIGNING_KEY_FILE, out / VERIFICATION_KEY_FILE
    if signing.exists() or verification.exists():
        # Replacing the signing key would orphan every key already issued.
        raise SystemExit(
            f"{out} already holds a key pair. Nothing was changed. Every key issued so far "
            f"depends on the existing signing key; choose another folder if you really mean "
            f"to start a new one."
        )
    private_pem, public_pem = generate_keypair()
    # Created readable by its owner only, before anything is written to it.
    descriptor = os.open(signing, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(private_pem)
    verification.write_text(public_pem)
    print(f"signing key (SECRET - keep it, back it up, never share it): {signing}")
    print(f"verification key (give this to installations):              {verification}")
    return 0


def _issue(args) -> int:
    private = load_private_key(Path(args.signing_key).expanduser().read_text())
    key = issue(
        private,
        licensee=args.licensee,
        client_name=args.client,
        sector=args.sector,
        issued_on=date.fromisoformat(args.issued) if args.issued else date.today(),
        expires_on=date.fromisoformat(args.expires),
        max_employees=args.max_employees,
    )
    print(key)
    return 0


def _inspect(args) -> int:
    public = load_public_key(Path(args.verification_key).expanduser().read_text())
    license_ = verify(args.key, public)
    for name, value in license_.as_dict().items():
        print(f"{name:14} {value if value is not None else '(no limit)'}")
    state = "EXPIRED" if license_.is_expired(date.today()) else "in date"
    print(f"{'today':14} {state}")
    return 0


def _verify_log(args) -> int:
    records = from_jsonl(Path(args.file).expanduser().read_text())
    problems = verify_chain(records)
    keys = sorted({r["key_id"] for r in records if r.get("key_id")})
    opened = sum(1 for r in records if r.get("event") == "engagement_opened")
    print(f"{len(records)} entries; {opened} engagement(s) opened; {len(keys)} key(s) used")
    for key_id in keys:
        print(f"  key {key_id}")
    if problems:
        print("THE LOG DOES NOT HOLD TOGETHER:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("The chain is intact: nothing has been removed, changed or reordered since the")
    print("first entry. Compare the keys above with the keys you issued.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wri-license", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="create the signing and verification keys (once)")
    keygen.add_argument("--out-dir", required=True)
    keygen.set_defaults(run=_keygen)

    issue_ = sub.add_parser("issue", help="issue a key for one engagement")
    issue_.add_argument("--signing-key", required=True, help="path to the SECRET signing key")
    issue_.add_argument("--licensee", required=True, help="the consulting firm")
    issue_.add_argument("--client", required=True, help="the client the engagement is for")
    issue_.add_argument("--sector", required=True, help="public_sector or private_sector")
    issue_.add_argument("--expires", required=True, help="last day the key is good, YYYY-MM-DD")
    issue_.add_argument("--issued", help="YYYY-MM-DD; today if left out")
    issue_.add_argument("--max-employees", type=int, help="largest employee file allowed")
    issue_.set_defaults(run=_issue)

    inspect = sub.add_parser("inspect", help="show what a key says")
    inspect.add_argument("key")
    inspect.add_argument("--verification-key", required=True)
    inspect.set_defaults(run=_inspect)

    log = sub.add_parser("verify-log", help="check a usage log a licensee exported")
    log.add_argument("file")
    log.set_defaults(run=_verify_log)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (LicenseError, FileNotFoundError, ValueError) as exc:
        print(f"wri-license: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
