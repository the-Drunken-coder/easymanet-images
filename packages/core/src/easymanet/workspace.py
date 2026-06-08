"""Shared EasyMANET workspace paths."""

import os
from pathlib import Path
from typing import Any

WORKSPACE_ENV = "EASYMANET_WORKSPACE"
WORKSPACE_NAME = "EasyMANET"
FLEETS_DIR_NAME = "Fleets"
IMAGES_DIR_NAME = "Images"
DIAGNOSTICS_DIR_NAME = "Diagnostics"
BUILDS_DIR_NAME = "Builds"
README_NAME = "README.txt"
FLEET_SUFFIXES = {".yml", ".yaml"}


def documents_dir() -> Path:
    return Path.home() / "Documents"


def workspace_root() -> Path:
    configured = os.environ.get(WORKSPACE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return documents_dir() / WORKSPACE_NAME


def fleets_dir() -> Path:
    return workspace_root() / FLEETS_DIR_NAME


def images_dir() -> Path:
    return workspace_root() / IMAGES_DIR_NAME


def diagnostics_dir() -> Path:
    return workspace_root() / DIAGNOSTICS_DIR_NAME


def builds_dir() -> Path:
    return workspace_root() / BUILDS_DIR_NAME


def ensure_workspace() -> Path:
    root = workspace_root()
    for path in (root, fleets_dir(), images_dir(), diagnostics_dir(), builds_dir()):
        path.mkdir(parents=True, exist_ok=True)
    readme = root / README_NAME
    if not readme.exists():
        readme.write_text(_readme_text())
    return root


def fleet_files() -> list[Path]:
    root = fleets_dir()
    if not root.exists():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in FLEET_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix().lower(),
    )


def fleet_file_records() -> list[dict[str, Any]]:
    root = fleets_dir()
    records = []
    for path in fleet_files():
        try:
            stat = path.stat()
        except OSError:
            continue
        records.append(
            {
                "name": path.name,
                "stem": path.stem,
                "path": str(path),
                "relative_path": path.relative_to(root).as_posix(),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
        )
    return records


def resolve_fleet_config(value: str) -> Path:
    candidate = Path(value).expanduser()
    for path in _fleet_path_candidates(candidate):
        if path.exists():
            return path

    if not candidate.is_absolute():
        workspace_candidate = fleets_dir() / value
        for path in _fleet_path_candidates(workspace_candidate):
            if path.exists():
                return path

    return candidate


def _fleet_path_candidates(path: Path) -> list[Path]:
    candidates = [path]
    if path.suffix.lower() not in FLEET_SUFFIXES:
        candidates.extend(Path(f"{path}{suffix}") for suffix in (".yml", ".yaml"))
    return candidates


def workspace_payload() -> dict[str, Any]:
    root = ensure_workspace()
    return {
        "root": str(root),
        "fleets_dir": str(fleets_dir()),
        "images_dir": str(images_dir()),
        "diagnostics_dir": str(diagnostics_dir()),
        "builds_dir": str(builds_dir()),
        "fleet_files": fleet_file_records(),
    }


def _readme_text() -> str:
    return (
        "EasyMANET workspace\n"
        "\n"
        "Put fleet YAML files in the Fleets folder. The CLI and desktop app\n"
        "will find those files automatically.\n"
        "\n"
        "Images, diagnostics, and build artifacts are stored in the sibling\n"
        "folders created here.\n"
    )
