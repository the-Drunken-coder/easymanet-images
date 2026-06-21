"""Operator diagnostics collection and support bundle export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

from . import __version__ as EASYMANET_VERSION
from .download import images_manifest_path
from .manifest import ManifestError, load_manifest
from .provision import resolve_node_model
from .validate import validate
from .workspace import diagnostics_dir, ensure_workspace, resolve_fleet_config, workspace_payload

API_PORT = 10411
HTTP_TIMEOUT_SECONDS = 2
STATUS_TIMEOUT_SECONDS = 6
TOPOLOGY_TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 1_000_000
SUPPORT_SCHEMA_VERSION = 1
COMMON_GATEWAY_HOSTS = ("10.41.254.1", "openmanet.local", "easymanet.local")
BOOT_REPORT_IMPORT_DIR = "imported-boot-reports"
SECRET_KEYS = {
    "password",
    "passphrase",
    "psk",
    "key",
    "private_key",
    "root_password_hash",
    "ssh_authorized_keys",
    "token",
    "secret",
}


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    host: str
    endpoint: str
    payload: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "host": self.host,
            "endpoint": self.endpoint,
            "payload": self.payload,
            "error": self.error,
        }


def run_diagnostics(*, config: str = "") -> dict[str, Any]:
    """Collect live EasyMANET diagnostics from configured or common node APIs."""
    generated_at = _now_iso()
    config_path = ""
    validation_payload: dict[str, Any] = {"ok": False, "errors": ["config not provided"], "warnings": []}
    candidates: list[dict[str, str]] = []
    warnings: list[str] = []
    errors: list[str] = []

    if config:
        config_path = str(resolve_fleet_config(config))
        try:
            manifest = load_manifest(config_path)
            validation = validate(manifest)
            validation_payload = {
                "ok": validation.valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
            }
            for name in manifest.node_names():
                try:
                    node = resolve_node_model(manifest, name)
                except ManifestError as exc:
                    warnings.append(f"{name}: {exc}")
                    continue
                if node.ip:
                    candidates.append(
                        {
                            "name": str(name),
                            "host": str(node.ip),
                            "role": str(node.role),
                            "source": "fleet",
                        }
                    )
        except (ManifestError, OSError) as exc:
            errors.append(str(exc))

    if not candidates:
        candidates.extend({"name": "", "host": host, "role": "", "source": "common"} for host in COMMON_GATEWAY_HOSTS)

    nodes: dict[str, dict[str, Any]] = {}
    topology: dict[str, Any] = {}
    discovery: dict[str, Any] = {
        "candidates": candidates,
        "api_port": API_PORT,
        "generated_at": generated_at,
    }
    for candidate in candidates:
        host = candidate["host"]
        identity = fetch_node_api(host, "identity")
        if identity.ok:
            status = fetch_node_api(host, "status", timeout=STATUS_TIMEOUT_SECONDS)
            neighbors = fetch_node_api(host, "neighbors")
        else:
            status = _skipped_api_result(host, "status", "skipped because identity API was unavailable")
            neighbors = _skipped_api_result(host, "neighbors", "skipped because identity API was unavailable")
        node_key = _node_key(candidate, identity.payload, status.payload)
        nodes[node_key] = {
            "candidate": candidate,
            "identity": identity.to_dict(),
            "status": status.to_dict(),
            "neighbors": neighbors.to_dict(),
        }
        role = _payload_role(identity.payload) or candidate.get("role", "")
        if identity.ok and not topology and role == "gate":
            top = fetch_node_api(host, "topology", timeout=TOPOLOGY_TIMEOUT_SECONDS)
            if top.ok:
                topology = top.payload

    support_code = _diagnostics_support_code(nodes)
    payload = {
        "ok": support_code in {"EM-OK", "EM-DIAG-PARTIAL"},
        "schema_version": SUPPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "support_code": support_code,
        "support_level": "ok" if support_code == "EM-OK" else "warn",
        "config_path": config_path,
        "validation": validation_payload,
        "discovery": discovery,
        "topology": topology,
        "nodes": nodes,
        "warnings": warnings,
        "errors": errors,
    }
    payload["summary"] = render_summary(payload)
    return payload


def fetch_node_api(host: str, endpoint: str, *, timeout: int = HTTP_TIMEOUT_SECONDS) -> ApiResult:
    url = f"http://{host}:{API_PORT}/v1/{endpoint}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "EasyMANETDiagnostics/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator LAN/mesh API.
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        return ApiResult(False, host, endpoint, {}, f"HTTP {exc.code}")
    except (URLError, TimeoutError, OSError) as exc:
        return ApiResult(False, host, endpoint, {}, str(exc))
    if len(body) > MAX_RESPONSE_BYTES:
        return ApiResult(False, host, endpoint, {}, "response too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ApiResult(False, host, endpoint, {}, f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return ApiResult(False, host, endpoint, {}, "JSON payload is not an object")
    return ApiResult(True, host, endpoint, payload)


def _skipped_api_result(host: str, endpoint: str, error: str) -> ApiResult:
    return ApiResult(False, host, endpoint, {}, error)


def render_summary(payload: dict[str, Any]) -> str:
    lines = [
        "EasyMANET Diagnostics",
        f"Generated: {payload.get('generated_at', '')}",
        f"Support code: {payload.get('support_code', 'EM-DIAG-PARTIAL')}",
    ]
    config_path = payload.get("config_path") or ""
    if config_path:
        lines.append(f"Config: {config_path}")
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    if validation:
        lines.append(f"Validation: {'OK' if validation.get('ok') else 'ISSUES'}")
    topology = payload.get("topology") if isinstance(payload.get("topology"), dict) else {}
    if topology.get("nodes"):
        online = sum(1 for node in topology.get("nodes", []) if node.get("status") == "online")
        lines.append(f"Topology: {online}/{len(topology.get('nodes', []))} nodes online")
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), dict) else {}
    for name, record in nodes.items():
        status_payload = _api_payload(record, "status")
        identity_payload = _api_payload(record, "identity")
        node = status_payload.get("node") if isinstance(status_payload.get("node"), dict) else identity_payload.get("node", {})
        mesh = status_payload.get("mesh") if isinstance(status_payload.get("mesh"), dict) else {}
        internet = status_payload.get("internet") if isinstance(status_payload.get("internet"), dict) else {}
        manageability = status_payload.get("manageability") if isinstance(status_payload.get("manageability"), dict) else {}
        code = status_payload.get("support_code") or ("EM-OK" if record.get("identity", {}).get("ok") else "EM-API-DOWN")
        lines.append(
            "Node {name}: role={role} ip={ip} mesh={mesh} neighbors={neighbors} internet={internet} manage={manage} code={code}".format(
                name=node.get("name") or name,
                role=node.get("role", ""),
                ip=node.get("ip", ""),
                mesh=_ok_label(mesh.get("ok")),
                neighbors=mesh.get("neighbor_count", ""),
                internet=_ok_label(internet.get("ok")),
                manage=_ok_label(manageability.get("ok")),
                code=code,
            )
        )
    for warning in payload.get("warnings") or []:
        lines.append(f"Warning: {warning}")
    for error in payload.get("errors") or []:
        lines.append(f"Error: {error}")
    return "\n".join(lines) + "\n"


def export_support_bundle(*, config: str = "") -> dict[str, Any]:
    ensure_workspace()
    diagnostics = run_diagnostics(config=config)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    code = str(diagnostics.get("support_code") or "EM-DIAG-PARTIAL").lower().replace("_", "-")
    bundle_path = diagnostics_dir() / f"easymanet-support-{timestamp}-{code}.zip"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SUPPORT_SCHEMA_VERSION,
        "generated_at": diagnostics["generated_at"],
        "support_code": diagnostics["support_code"],
        "support_level": diagnostics["support_level"],
        "easymanet_version": EASYMANET_VERSION,
        "workspace": workspace_payload(),
        "redaction": "obvious secrets redacted by default",
    }
    with ZipFile(bundle_path, "w", ZIP_DEFLATED) as zf:
        _zip_json(zf, "manifest.json", manifest)
        zf.writestr("summary.txt", diagnostics["summary"])
        _zip_json(zf, "fleet/validation.json", diagnostics.get("validation", {}))
        _zip_json(zf, "mesh/discovery.json", diagnostics.get("discovery", {}))
        _zip_json(zf, "mesh/topology.json", diagnostics.get("topology", {}))
        for node_name, record in (diagnostics.get("nodes") or {}).items():
            safe_name = _safe_name(node_name)
            for endpoint in ("identity", "status", "neighbors"):
                endpoint_record = record.get(endpoint, {})
                _zip_json(zf, f"nodes/{safe_name}/{endpoint}.json", endpoint_record)
        _zip_json(zf, "operator/state.json", workspace_payload())
        image_manifest = images_manifest_path()
        if image_manifest.exists():
            _zip_text(zf, "image/release-metadata.json", image_manifest.read_text(encoding="utf-8"))
        config_path = diagnostics.get("config_path") or ""
        if config_path and Path(config_path).exists():
            _zip_text(zf, "fleet/redacted-fleet.yml", redacted_yaml_text(Path(config_path)))
        _zip_imported_boot_reports(zf)
    return {"ok": True, "bundle_path": str(bundle_path), "summary": diagnostics["summary"], "diagnostics": diagnostics}


def import_boot_report(*, source: str) -> dict[str, Any]:
    if not source.strip():
        return {"ok": False, "errors": ["source is required"], "imported": []}
    src = Path(source).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "errors": [f"source does not exist: {src}"], "imported": []}
    report_root = _boot_report_root(src)
    if not report_root.is_dir():
        return {"ok": False, "errors": [f"source is not a directory: {report_root}"], "imported": []}
    reports = [path for path in report_root.iterdir() if path.is_dir() and path.name.startswith("boot-report")]
    if not reports:
        return {"ok": False, "errors": [f"no boot reports found under {report_root}"], "imported": []}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_root = diagnostics_dir() / BOOT_REPORT_IMPORT_DIR / timestamp
    target_root.mkdir(parents=True, exist_ok=True)
    imported: list[str] = []
    for report in sorted(reports, key=lambda path: path.name):
        dest = target_root / report.name
        shutil.copytree(report, dest, symlinks=True)
        imported.append(str(dest))
    return {"ok": True, "source": str(report_root), "target": str(target_root), "imported": imported}


def redacted_yaml_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except OSError:
        return ""
    except yaml.YAMLError:
        return _redact_text(text)
    return yaml.safe_dump(redact_value(data), sort_keys=False)


def redact_value(value: Any, key: str = "") -> Any:
    if _is_secret_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {item_key: redact_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def _diagnostics_support_code(nodes: dict[str, dict[str, Any]]) -> str:
    if not nodes:
        return "EM-API-DOWN"
    status_codes = []
    api_failures = 0
    missing_configured_node = False
    for record in nodes.values():
        status = record.get("status", {})
        if status.get("ok"):
            code = _api_payload(record, "status").get("support_code")
            if isinstance(code, str) and code:
                status_codes.append(code)
        elif not record.get("identity", {}).get("ok"):
            api_failures += 1
            candidate = record.get("candidate", {})
            if isinstance(candidate, dict) and candidate.get("source") == "fleet":
                missing_configured_node = True
    if missing_configured_node:
        return "EM-NODE-MISSING"
    for code in ("EM-BOOT-INCOMPLETE", "EM-NODE-MISSING", "EM-MESH-DOWN", "EM-INET-DOWN"):
        if code in status_codes:
            return code
    if status_codes and api_failures:
        return "EM-DIAG-PARTIAL"
    if status_codes:
        return "EM-OK" if all(code == "EM-OK" for code in status_codes) else status_codes[0]
    return "EM-API-DOWN"


def _node_key(candidate: dict[str, str], identity: dict[str, Any], status: dict[str, Any]) -> str:
    for payload in (status, identity):
        node = payload.get("node") if isinstance(payload.get("node"), dict) else {}
        if node.get("name"):
            return str(node["name"])
    return candidate.get("name") or candidate.get("host") or "unknown"


def _payload_role(payload: dict[str, Any]) -> str:
    node = payload.get("node") if isinstance(payload.get("node"), dict) else {}
    return str(node.get("role") or "")


def _api_payload(record: dict[str, Any], endpoint: str) -> dict[str, Any]:
    api = record.get(endpoint, {})
    payload = api.get("payload") if isinstance(api, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _ok_label(value: Any) -> str:
    if value is True or value == "true":
        return "OK"
    if value is False or value == "false":
        return "DOWN"
    return "UNKNOWN"


def _zip_json(zf: ZipFile, name: str, payload: Any) -> None:
    zf.writestr(name, json.dumps(redact_value(payload), indent=2, sort_keys=True) + "\n")


def _zip_text(zf: ZipFile, name: str, text: str) -> None:
    zf.writestr(name, _redact_text(text))


def _zip_imported_boot_reports(zf: ZipFile) -> None:
    root = diagnostics_dir() / BOOT_REPORT_IMPORT_DIR
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        arcname = f"boot-reports/{path.relative_to(root).as_posix()}"
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            zf.writestr(f"{arcname}.skipped.txt", "Skipped non-text boot-report artifact during redacted export.\n")
            continue
        if path.suffix == ".json":
            try:
                _zip_json(zf, arcname, json.loads(text))
                continue
            except json.JSONDecodeError:
                pass
        _zip_text(zf, arcname, text)


def _boot_report_root(source: Path) -> Path:
    if (source / "easymanet").is_dir():
        return source / "easymanet"
    if source.name == "easymanet":
        return source
    return source


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe or "unknown"


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_KEYS:
        return True
    if any(part in lowered for part in ("password", "passphrase", "secret", "private_key", "api_key", "token", "psk")):
        return True
    return (lowered == "key" or lowered.endswith("_key")) and lowered not in {"public_key", "primary_key"}


def _redact_text(text: str) -> str:
    return re.sub(r"(?i)(password|passphrase|secret|token|root_password_hash|key)([\"'\s:=]+)([^\"'\n]+)", r"\1\2<redacted>", text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
