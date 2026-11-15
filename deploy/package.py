"""Build the install package a licensee receives.

    make package LICENSE_PUBLIC_KEY=~/wri-keys/wri_license_public_key.pem

The package is one folder, also written as one `.tar.gz`:

    wri-<version>/
      INSTALL.md                 the configuration guide and the upgrade path
      wri                        the operator's script: install, start, backup, upgrade ...
      compose.yaml               the two services and the data volume
      wri.env.example            every setting, with what it does
      wri-image-<version>.tar.gz the product itself, as a container image
      requirements.lock          every dependency in the image, with its exact version
      VERSION, LICENSE, SHA256SUMS

The license verification key is baked into the image, because by the owner's decision that
is the only place it ships. Three things are checked before it goes anywhere:

* it must be given. A package without one enforces no license keys, so that has to be asked
  for by name (`--unlicensed`) and the image is tagged `-unlicensed` so it cannot be
  mistaken for a release;
* it must be the VERIFICATION key. If the file holds a private key, the build stops: that is
  the signing key, and putting it in a package would let any licensee issue keys;
* it must actually be a key this product can check signatures with.

`--no-image` assembles everything except the image. It needs no Docker and is what the
tests use.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_NAME = "license_public_key.pem"
#: The kind of machine the image is for. Servers are almost always this; a package built on
#: an Apple-silicon laptop without it would not start on one.
DEFAULT_PLATFORM = "linux/amd64"
#: What goes in the package beside the image: source path -> name in the package.
PACKAGE_FILES = {
    "docs/INSTALL.md": "INSTALL.md",
    "deploy/wri": "wri",
    "deploy/compose.yaml": "compose.yaml",
    "deploy/wri.env.example": "wri.env.example",
    "LICENSE": "LICENSE",
}
EXECUTABLE = {"wri"}


class PackageError(Exception):
    """The package cannot be built. The message is written for whoever is building it."""


def product_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def check_verification_key(path: Path) -> str:
    """The key's text, once it is certain this is a verification key and nothing else."""
    from wri_engine.licensing.keys import LicenseError, load_public_key

    path = Path(path).expanduser()
    if not path.is_file():
        raise PackageError(f"there is no file at {path}")
    text = path.read_text(errors="replace")
    if "PRIVATE KEY" in text:
        raise PackageError(
            f"{path} holds a PRIVATE key. That is the signing key: it issues license keys "
            f"and must never be put in a package, an installation or a repository. Give "
            f"the verification key instead (wri_license_public_key.pem). Nothing was built."
        )
    try:
        load_public_key(text)
    except LicenseError as exc:
        raise PackageError(
            f"{path} is not a verification key this product can use: {exc}"
        ) from None
    return text


def image_tag(version: str, licensed: bool) -> str:
    return f"wri:{version}" if licensed else f"wri:{version}-unlicensed"


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+ " + " ".join(shlex.quote(part) for part in command), flush=True)
    return subprocess.run(command, check=True, **kwargs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_image(
    tag: str, version: str, key_text: str | None, folder: Path, platform: str = DEFAULT_PLATFORM
) -> None:
    """Build the image, check it is what it claims to be, and save it into the package."""
    with tempfile.TemporaryDirectory() as staged:
        if key_text is not None:
            (Path(staged) / KEY_NAME).write_text(key_text)
        # Extra arguments for a build behind a proxy, e.g.
        #   WRI_DOCKER_BUILD_ARGS="--network host --secret id=ca,src=/path/to/ca.pem"
        extra = shlex.split(os.environ.get("WRI_DOCKER_BUILD_ARGS", ""))
        _run(
            [
                "docker", "build",
                "--platform", platform,
                "--build-context", f"license={staged}",
                "--build-arg", f"WRI_VERSION={version}",
                "--build-arg", f"WRI_LICENSED={'yes' if key_text is not None else 'no'}",
                "--tag", tag,
                *extra,
                str(ROOT),
            ]
        )  # fmt: skip

    def inside(script: str) -> str:
        done = _run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh", tag, "-c", script],
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()

    # The image must be the version it is tagged as, must hold the verification key exactly
    # when it should, and must hold no private key anywhere in what this product put there.
    found = inside('python -c "import wri_engine; print(wri_engine.__version__)"')
    if found != version:
        raise PackageError(f"the image reports version {found!r}, expected {version!r}")
    has_key = inside(f"test -f /app/config/{KEY_NAME} && echo yes || echo no")
    if (has_key == "yes") != (key_text is not None):
        raise PackageError(f"verification key in the image: {has_key}; expected otherwise")
    marker = "PRIVATE " + "KEY-----"  # split, so this file never matches its own search
    stray = inside(
        f"grep -rl '{marker}' /app "
        "$(python -c 'import wri_engine,os;print(os.path.dirname(wri_engine.__file__))') "
        "2>/dev/null || true"
    )
    if stray:
        raise PackageError(f"a private key was found in the image, in: {stray}")
    (folder / "requirements.lock").write_text(inside("cat /opt/wri/requirements.lock") + "\n")

    target = folder / f"wri-image-{version}.tar.gz"
    print(f"saving {tag} to {target.name} (this takes a minute)", flush=True)
    with subprocess.Popen(["docker", "save", tag], stdout=subprocess.PIPE) as save:
        with gzip.open(target, "wb", compresslevel=6) as out:
            shutil.copyfileobj(save.stdout, out, 1 << 20)
        if save.wait() != 0:
            raise PackageError("docker save failed")


def assemble(
    out_dir: Path,
    *,
    key: Path | None,
    unlicensed: bool = False,
    with_image: bool = True,
    platform: str = DEFAULT_PLATFORM,
) -> Path:
    """Build the package folder and its `.tar.gz`; return the archive's path."""
    if key is None and not unlicensed:
        raise PackageError(
            "no verification key was given. A release needs one: "
            "make package LICENSE_PUBLIC_KEY=/path/to/wri_license_public_key.pem. "
            "For an image that enforces no license keys, ask for it by name: --unlicensed."
        )
    if key is not None and unlicensed:
        raise PackageError("give a verification key or --unlicensed, not both")
    key_text = check_verification_key(key) if key is not None else None

    version = product_version()
    name = f"wri-{version}" + ("" if key_text is not None else "-unlicensed")
    folder = Path(out_dir) / name
    archive = Path(out_dir) / f"{name}.tar.gz"
    for existing in (folder, archive):
        if existing.exists():
            raise PackageError(f"{existing} already exists. Move it away first; nothing was built.")
    folder.mkdir(parents=True)

    for source, target in PACKAGE_FILES.items():
        shutil.copyfile(ROOT / source, folder / target)
        (folder / target).chmod(0o755 if target in EXECUTABLE else 0o644)
    (folder / "VERSION").write_text(
        f"{version}\n"
        f"license keys enforced: {'yes' if key_text is not None else 'NO'}\n"
        f"built for: {platform}\n"
    )
    if with_image:
        build_image(image_tag(version, key_text is not None), version, key_text, folder, platform)

    files = sorted(p for p in folder.iterdir() if p.is_file())
    (folder / "SHA256SUMS").write_text("".join(f"{_sha256(p)}  {p.name}\n" for p in files))
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(folder, arcname=name)
    print(f"package:  {archive}")
    print(f"sha256:   {_sha256(archive)}")
    if key_text is None:
        print("THIS PACKAGE ENFORCES NO LICENSE KEYS. It is not a release.")
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--license-key", help="the VERIFICATION key (wri_license_public_key.pem)")
    parser.add_argument("--unlicensed", action="store_true", help="build with no key, by name")
    parser.add_argument("--out", default=str(ROOT / "dist"))
    parser.add_argument("--no-image", action="store_true", help="everything but the image")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, help=f"default {DEFAULT_PLATFORM}")
    args = parser.parse_args(argv)
    try:
        assemble(
            Path(args.out),
            key=Path(args.license_key) if args.license_key else None,
            unlicensed=args.unlicensed,
            with_image=not args.no_image,
            platform=args.platform,
        )
    except PackageError as exc:
        print(f"package: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"package: a build step failed ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
