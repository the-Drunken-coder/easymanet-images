"""Render resolved provision.json from fleet manifest."""

import json
from typing import Any, Dict, Optional

from .manifest import Manifest, ManifestError
from .validate import resolve_node


def render(
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> str:
    mesh = manifest.mesh
    if not isinstance(mesh, dict):
        raise ManifestError(f"Manifest section 'mesh' must be a mapping, got {type(mesh).__name__}")
    defaults = manifest.defaults
    if not isinstance(defaults, dict):
        raise ManifestError(
            f"Manifest section 'defaults' must be a mapping, got {type(defaults).__name__}"
        )
    resolved_node = resolve_node(manifest, node_name)
    management = defaults.get("management", {})
    if not isinstance(management, dict):
        raise ManifestError(
            f"defaults.management must be a mapping, got {type(management).__name__}"
        )

    management_block: Dict[str, Any] = {
        "root_password_hash": management.get("root_password_hash", ""),
        "ssh_authorized_keys": management.get("ssh_authorized_keys", []),
    }
    if ssh_enabled is not None:
        management_block["ssh_enabled"] = bool(ssh_enabled)

    provision: Dict[str, Any] = {
        "version": 1,
        "mesh": {
            "id": mesh.get("id", ""),
            "password": mesh.get("password", ""),
            "channel": mesh.get("channel", 0),
            "bandwidth_mhz": mesh.get("bandwidth_mhz", 0),
            "country": mesh.get("country", ""),
        },
        "node": resolved_node,
        "management": management_block,
    }

    return json.dumps(provision, indent=2)


def render_dict(
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    return json.loads(render(manifest, node_name, ssh_enabled=ssh_enabled))
