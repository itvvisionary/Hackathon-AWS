#!/usr/bin/env python3
"""Build an AWS Lambda layer zip from a requirements file.

Works on Windows, macOS and Linux. Dependencies are installed as *Linux*
(manylinux) wheels for the target Lambda Python version and architecture, so a
layer built on a Windows laptop still runs on Lambda.

Layer layout produced (required by Lambda for Python):
    python/<packages...>

The zip is deterministic (sorted entries, fixed timestamps), so rebuilding with
the same dependencies yields the same zip.

Used by the App pipeline (.github/workflows/app.yml), which passes the Python
version and architecture read from the deployed function. Locally (from App/):
    python scripts/build_layer.py
    python scripts/build_layer.py --python-version 3.12 --architecture arm64
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Lambda hard limit (the pipeline publishes through S3, so the 50 MB
# direct-upload limit does not apply).
MAX_UNZIPPED_TOTAL = 250 * 1024 * 1024  # function + all layers, unzipped

PLATFORMS = {
    "x86_64": "manylinux2014_x86_64",
    "arm64": "manylinux2014_aarch64",
}

# Fixed timestamp for reproducible zips (zip format minimum is 1980-01-01).
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# Junk that is never needed at runtime.
PRUNE_DIR_NAMES = {"__pycache__"}
PRUNE_SUFFIXES = {".pyc", ".pyo"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--requirements",
        default=str(PROJECT_ROOT / "layer" / "requirements.txt"),
        help="Requirements file to install into the layer.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "layer" / "dist" / "langgraph-layer.zip"),
        help="Output zip path.",
    )
    parser.add_argument("--python-version", default="3.12", help="Target Lambda Python version, e.g. 3.12.")
    parser.add_argument("--architecture", default="x86_64", choices=sorted(PLATFORMS), help="Target Lambda architecture.")
    return parser.parse_args()


def pip_install(requirements: Path, target: Path, python_version: str, architecture: str) -> None:
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--requirement", str(requirements),
        "--target", str(target),
        "--platform", PLATFORMS[architecture],
        "--implementation", "cp",
        "--python-version", python_version,
        "--only-binary=:all:",  # never compile sdists for the host OS
        "--upgrade",
        "--no-compile",
        "--disable-pip-version-check",
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def prune(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in PRUNE_DIR_NAMES:
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file() and path.suffix in PRUNE_SUFFIXES:
            path.unlink()


def write_lock(site_packages: Path, lock_path: Path) -> None:
    """Record every installed distribution (including transitive) as name==version."""
    pins = []
    for dist_info in site_packages.glob("*.dist-info"):
        name, _, version = dist_info.name[: -len(".dist-info")].partition("-")
        pins.append(f"{name}=={version}")
    lock_path.write_text("\n".join(sorted(pins, key=str.lower)) + "\n", encoding="utf-8")


def make_zip(build_root: Path, output: Path) -> int:
    """Zip build_root's contents deterministically. Returns total unzipped bytes."""
    unzipped = 0
    files = sorted(p for p in build_root.rglob("*") if p.is_file())
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = path.relative_to(build_root).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # rw-r--r--
            data = path.read_bytes()
            unzipped += len(data)
            zf.writestr(info, data)
    return unzipped


def main() -> int:
    args = parse_args()
    requirements = Path(args.requirements).resolve()
    output = Path(args.output).resolve()
    if not requirements.is_file():
        print(f"Requirements file not found: {requirements}", file=sys.stderr)
        return 1

    build_root = output.parent / "build"
    site_packages = build_root / "python"  # Lambda adds /opt/python to sys.path

    shutil.rmtree(build_root, ignore_errors=True)
    site_packages.mkdir(parents=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    pip_install(requirements, site_packages, args.python_version, args.architecture)
    prune(site_packages)
    write_lock(site_packages, output.with_suffix(".lock.txt"))

    unzipped = make_zip(build_root, output)
    zipped = output.stat().st_size
    shutil.rmtree(build_root, ignore_errors=True)

    print(f"\nLayer written: {output}")
    print(f"  target   : python{args.python_version} / {args.architecture}")
    print(f"  zipped   : {zipped / 1024 / 1024:.1f} MB")
    print(f"  unzipped : {unzipped / 1024 / 1024:.1f} MB")

    if unzipped > MAX_UNZIPPED_TOTAL:
        print("ERROR: unzipped size exceeds Lambda's 250 MB limit.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
