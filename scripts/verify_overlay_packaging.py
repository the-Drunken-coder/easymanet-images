#!/usr/bin/env python3
"""Ensure provisioning/openwrt-overlay files are listed in pyproject.toml data-files."""

from pathlib import Path
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "provisioning" / "openwrt-overlay"
PYPROJECT_PATH = ROOT / "pyproject.toml"
METADATA_SUFFIXES = {".pyc", ".pyo", ".swp", ".swo"}


def _is_packaged_overlay_file(path: Path) -> bool:
    if not path.is_file():
        return False
    rel_parts = path.relative_to(OVERLAY).parts
    if any(part.startswith(".") or part == "__pycache__" for part in rel_parts):
        return False
    return path.suffix not in METADATA_SUFFIXES


def _packaged_overlay_paths() -> set[str]:
    with PYPROJECT_PATH.open("rb") as f:
        data = tomllib.load(f)
    packaged: set[str] = set()
    overlay_prefix = "provisioning/openwrt-overlay/"
    data_files = (
        data.get("tool", {})
        .get("setuptools", {})
        .get("data-files", {})
    )
    if not isinstance(data_files, dict):
        return packaged
    for filenames in data_files.values():
        if not isinstance(filenames, list):
            continue
        for name in filenames:
            if isinstance(name, str) and name.startswith(overlay_prefix):
                packaged.add(name)
    return packaged


def main() -> int:
    packaged = _packaged_overlay_paths()
    if not OVERLAY.is_dir():
        print(f"Overlay directory not found: {OVERLAY}", file=sys.stderr)
        return 1

    overlay_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in OVERLAY.rglob("*")
        if _is_packaged_overlay_file(path)
    )
    missing = [rel for rel in overlay_files if rel not in packaged]

    if missing:
        print(
            "Overlay files missing from pyproject.toml [tool.setuptools.data-files]:",
            file=sys.stderr,
        )
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
        return 1

    print(f"All {len(overlay_files)} overlay files are listed in pyproject.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
