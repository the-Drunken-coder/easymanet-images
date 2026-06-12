"""Render resolved provision.json from fleet manifest."""

from typing import Optional

from .manifest import Manifest
from .provision import resolve_provision


def render(
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> str:
    return resolve_provision(manifest, node_name, ssh_enabled=ssh_enabled).to_json()


def render_dict(
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> dict[str, object]:
    return resolve_provision(manifest, node_name, ssh_enabled=ssh_enabled).to_dict()
