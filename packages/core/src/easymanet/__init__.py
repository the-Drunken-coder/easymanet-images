"""EasyMANET - Zero-touch OpenMANET provisioning and imaging."""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path


def _version() -> str:
    try:
        return metadata.version("easymanet")
    except metadata.PackageNotFoundError:
        pyproject = _source_pyproject()
        if not pyproject:
            return "0+unknown"
        match = re.search(r'^version = "([^"]+)"$', pyproject.read_text(), re.MULTILINE)
        return match.group(1) if match else "0+unknown"


def _source_pyproject() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            return candidate
    return None


__version__ = _version()
