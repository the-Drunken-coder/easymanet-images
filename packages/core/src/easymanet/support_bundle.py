"""Redacted support bundle export."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .download import cache_dir, images_manifest_path, version_file_path
from .disks import list_disks
from .manifest import ManifestError, load_manifest
from .validate import validate
from .workspace import diagnostics_dir, resolve_fleet_config, workspace_payload

SECRET_KEY_PATTERN = re.compile(
    r"(password|passphrase|private|priv_key|psk|secret|token|api[_-]?key|root_password_hash)",
    re.IGNORECASE,
)
SECRET_LINE_PATTERN = re.compile(
    r"(?P<prefix>\b(?P<key>password|passphrase|psk|secret|token|api[_-]?key|root_password_hash)\b\s*[:=]\s*)"
    r"(?:(?P<quote>['\"])(?P<quoted>[^\n]*?)(?P=quote)|(?P<unquoted>[^\s\n]+))",
    re.IGNORECASE,
)
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
SENSITIVE_FILENAME_PATTERN = re.compile(
    r"(^|/)(id_(rsa|dsa|ecdsa|ed25519)|.*private.*key.*|.*\.pem)$",
    re.IGNORECASE,
)
REDACTED = "<redacted>"


@dataclass(frozen=True)
class SupportBundleResult:
    path: Path
    files: list[str]
    redactions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "path": str(self.path),
            "files": self.files,
            "redactions": self.redactions,
        }


def default_support_bundle_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return diagnostics_dir() / f"easymanet-support-{stamp}.zip"


def create_support_bundle(
    *,
    config: str = "",
    node: str = "",
    boot_report: str = "",
    output: str = "",
    include_mesh: bool = False,
    include_disks: bool = False,
    flash_log: str = "",
    flash_result: dict[str, Any] | None = None,
    mesh_payload: dict[str, Any] | None = None,
) -> SupportBundleResult:
    output_path = Path(output).expanduser() if output else default_support_bundle_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    redactions: list[str] = []

    def add_text(zf: zipfile.ZipFile, name: str, text: str) -> None:
        zf.writestr(name, text)
        files.append(name)

    def add_json(zf: zipfile.ZipFile, name: str, payload: Any) -> None:
        add_text(zf, name, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        add_json(
            zf,
            "support-bundle.json",
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "easymanet_version": __version__,
                "config": config,
                "node": node,
                "redacted": True,
            },
        )
        add_json(zf, "workspace/state.json", workspace_payload())
        add_json(zf, "images/inventory.json", image_inventory())

        if config:
            _add_fleet_context(zf, config=config, node=node, add_text=add_text, add_json=add_json, redactions=redactions)

        if include_disks:
            try:
                add_json(zf, "disks/disks.json", [disk.__dict__ for disk in list_disks(include_all=False)])
            except Exception as exc:  # noqa: BLE001 - diagnostics should continue best-effort.
                add_json(zf, "disks/error.json", {"errors": [str(exc)]})

        if flash_log:
            add_text(zf, "flash/log.txt", redact_text(flash_log, redactions))
        if flash_result:
            add_json(zf, "flash/result.json", redact_data(flash_result, redactions))

        if include_mesh and mesh_payload:
            add_json(zf, "mesh/topology.json", redact_data(mesh_payload, redactions))

        if boot_report:
            _add_boot_report(zf, Path(boot_report).expanduser(), files=files, redactions=redactions)

        add_json(zf, "redaction-report.json", {"redacted": sorted(set(redactions))})

    return SupportBundleResult(path=output_path, files=files, redactions=sorted(set(redactions)))


def image_inventory() -> dict[str, Any]:
    return {
        "cache_dir": str(cache_dir()),
        "images_manifest": _read_json_file(images_manifest_path()),
        "version_file": _read_json_file(version_file_path()),
        "cached_images": [
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": _file_size(path),
            }
            for path in sorted(cache_dir().glob("*.img*"))
            if path.is_file()
        ] if cache_dir().exists() else [],
    }


def redact_text(text: str, redactions: list[str] | None = None) -> str:
    def replace_private_key(_match: re.Match[str]) -> str:
        if redactions is not None:
            redactions.append("private_key")
        return REDACTED

    def replace(match: re.Match[str]) -> str:
        if redactions is not None:
            redactions.append(match.group("key").lower())
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}{REDACTED}{quote}"

    text = PRIVATE_KEY_BLOCK_PATTERN.sub(replace_private_key, text)
    return SECRET_LINE_PATTERN.sub(replace, text)


def redact_data(value: Any, redactions: list[str] | None = None) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                if redactions is not None:
                    redactions.append(str(key))
                result[key] = REDACTED
            else:
                result[key] = redact_data(item, redactions)
        return result
    if isinstance(value, list):
        return [redact_data(item, redactions) for item in value]
    return value


def _add_fleet_context(zf: zipfile.ZipFile, *, config: str, node: str, add_text, add_json, redactions: list[str]) -> None:
    config_path = resolve_fleet_config(config)
    try:
        raw = config_path.read_text()
        add_text(zf, "fleet/redacted-config.yml", redact_text(raw, redactions))
    except OSError as exc:
        add_json(zf, "fleet/config-error.json", {"errors": [str(exc)]})
        return

    try:
        manifest = load_manifest(str(config_path))
        result = validate(manifest, node_name=node or None)
        add_json(
            zf,
            "fleet/validation.json",
            {
                "ok": result.valid,
                "errors": result.errors,
                "warnings": result.warnings,
                "nodes": manifest.node_names(),
            },
        )
    except ManifestError as exc:
        add_json(zf, "fleet/validation.json", {"ok": False, "errors": [str(exc)], "warnings": []})


def _add_boot_report(zf: zipfile.ZipFile, root: Path, *, files: list[str], redactions: list[str]) -> None:
    if not root.exists():
        zf.writestr("boot-reports/error.json", json.dumps({"errors": [f"Boot report not found: {root}"]}, indent=2) + "\n")
        files.append("boot-reports/error.json")
        return
    if root.is_symlink():
        zf.writestr(
            "boot-reports/error.json",
            json.dumps({"errors": [f"Boot report symlink skipped: {root}"]}, indent=2) + "\n",
        )
        files.append("boot-reports/error.json")
        return
    if root.is_file():
        arcname = f"boot-reports/{root.name}"
        if _sensitive_boot_report_name(root.name):
            zf.writestr(arcname, REDACTED + "\n")
            redactions.append("private_key_file")
            files.append(arcname)
            return
        try:
            zf.writestr(arcname, redact_text(root.read_text(encoding="utf-8"), redactions))
        except UnicodeDecodeError:
            with root.open("rb") as src, zf.open(arcname, "w") as dst:
                shutil.copyfileobj(src, dst)
        files.append(arcname)
        return
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        arcname = f"boot-reports/{rel}"
        if _sensitive_boot_report_name(rel):
            zf.writestr(arcname, REDACTED + "\n")
            redactions.append("private_key_file")
            files.append(arcname)
            continue
        try:
            zf.writestr(arcname, redact_text(path.read_text(encoding="utf-8"), redactions))
        except UnicodeDecodeError:
            with path.open("rb") as src, zf.open(arcname, "w") as dst:
                shutil.copyfileobj(src, dst)
        files.append(arcname)


def _sensitive_boot_report_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return bool(SENSITIVE_FILENAME_PATTERN.search(normalized))


def _read_json_file(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
