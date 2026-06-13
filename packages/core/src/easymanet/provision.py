"""Typed resolution for EasyMANET provision payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .manifest import Manifest, ManifestError


@dataclass(frozen=True)
class MeshConfig:
    id: object = ""
    password: object = ""
    channel: object = 0
    bandwidth_mhz: object = 0
    country: object = ""

    @classmethod
    def from_mapping(cls, mesh: dict[str, object]) -> "MeshConfig":
        return cls(
            id=mesh.get("id", ""),
            password=mesh.get("password", ""),
            channel=mesh.get("channel", 0),
            bandwidth_mhz=mesh.get("bandwidth_mhz", 0),
            country=mesh.get("country", ""),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "password": self.password,
            "channel": self.channel,
            "bandwidth_mhz": self.bandwidth_mhz,
            "country": self.country,
        }


@dataclass(frozen=True)
class LocalApConfig:
    enabled: object = False
    ssid: object = ""
    password: object = ""
    extra: dict[str, object] = field(default_factory=dict)
    fields: frozenset[str] = frozenset({"enabled"})

    @classmethod
    def from_mapping(cls, local_ap: dict[str, object]) -> "LocalApConfig":
        return cls(
            enabled=local_ap.get("enabled", False),
            ssid=local_ap.get("ssid", ""),
            password=local_ap.get("password", ""),
            extra=_extra_fields(local_ap, {"enabled", "ssid", "password"}),
            fields=_present_fields(local_ap, {"enabled", "ssid", "password"}),
        )

    def to_dict(self) -> dict[str, object]:
        return _with_present_fields(
            self.extra,
            self.fields,
            {
                "enabled": self.enabled,
                "ssid": self.ssid,
                "password": self.password,
            },
        )


@dataclass(frozen=True)
class GatewayWifiConfig:
    enabled: object = False
    ssid: object = ""
    password: object = ""
    encryption: object = None
    extra: dict[str, object] = field(default_factory=dict)
    fields: frozenset[str] = frozenset()

    @classmethod
    def from_mapping(cls, wifi: dict[str, object]) -> "GatewayWifiConfig":
        return cls(
            enabled=wifi.get("enabled", False),
            ssid=wifi.get("ssid", ""),
            password=wifi.get("password", ""),
            encryption=wifi.get("encryption"),
            extra=_extra_fields(wifi, {"enabled", "ssid", "password", "encryption"}),
            fields=_present_fields(wifi, {"enabled", "ssid", "password", "encryption"}),
        )

    def to_dict(self) -> dict[str, object]:
        return _with_present_fields(
            self.extra,
            self.fields,
            {
                "enabled": self.enabled,
                "ssid": self.ssid,
                "password": self.password,
                "encryption": self.encryption,
            },
        )


@dataclass(frozen=True)
class GatewayConfig:
    enabled: object = False
    uplink_interface: object = ""
    wifi_config: GatewayWifiConfig | None = None
    raw_wifi: object = None
    extra: dict[str, object] = field(default_factory=dict)
    fields: frozenset[str] = frozenset({"enabled"})

    @classmethod
    def from_mapping(cls, gateway: dict[str, object]) -> "GatewayConfig":
        wifi = gateway.get("wifi")
        return cls(
            enabled=gateway.get("enabled", False),
            uplink_interface=gateway.get("uplink_interface", ""),
            wifi_config=GatewayWifiConfig.from_mapping(wifi) if isinstance(wifi, dict) else None,
            raw_wifi=wifi,
            extra=_extra_fields(gateway, {"enabled", "uplink_interface", "wifi"}),
            fields=_present_fields(gateway, {"enabled", "uplink_interface", "wifi"}),
        )

    @property
    def wifi(self) -> GatewayWifiConfig | None:
        return self.wifi_config

    def to_dict(self) -> dict[str, object]:
        values: dict[str, object] = {
            "enabled": self.enabled,
            "uplink_interface": self.uplink_interface,
        }
        if self.wifi_config is not None:
            values["wifi"] = self.wifi_config.to_dict()
        else:
            values["wifi"] = self.raw_wifi
        return _with_present_fields(self.extra, self.fields, values)


@dataclass(frozen=True)
class ManagementConfig:
    root_password_hash: object = ""
    ssh_authorized_keys: object = field(default_factory=list)
    ssh_enabled: Optional[bool] = None

    @classmethod
    def from_mapping(
        cls,
        management: dict[str, object],
        *,
        ssh_enabled: Optional[bool] = None,
    ) -> "ManagementConfig":
        return cls(
            root_password_hash=management.get("root_password_hash", ""),
            ssh_authorized_keys=management.get("ssh_authorized_keys", []),
            ssh_enabled=ssh_enabled,
        )

    def to_dict(self) -> dict[str, object]:
        payload = {
            "root_password_hash": self.root_password_hash,
            "ssh_authorized_keys": self.ssh_authorized_keys,
        }
        if self.ssh_enabled is not None:
            payload["ssh_enabled"] = bool(self.ssh_enabled)
        return payload


@dataclass(frozen=True)
class ResolvedNode:
    name: str
    hostname: object
    role: object
    target: object
    ip: object
    local_ap: LocalApConfig
    gateway: GatewayConfig

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "hostname": self.hostname,
            "role": self.role,
            "target": self.target,
            "ip": self.ip,
            "local_ap": self.local_ap.to_dict(),
            "gateway": self.gateway.to_dict(),
        }


@dataclass(frozen=True)
class FleetNode:
    name: str
    hostname: object
    role: object
    target: object
    ip: object

    @classmethod
    def from_resolved_node(cls, node: ResolvedNode) -> "FleetNode":
        return cls(
            name=node.name,
            hostname=node.hostname,
            role=node.role,
            target=node.target,
            ip=node.ip,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "hostname": self.hostname,
            "role": self.role,
            "target": self.target,
            "ip": self.ip,
        }


@dataclass(frozen=True)
class FleetConfig:
    nodes: tuple[FleetNode, ...]

    def to_dict(self) -> dict[str, object]:
        return {"nodes": [node.to_dict() for node in self.nodes]}


@dataclass(frozen=True)
class ProvisionPayload:
    version: int
    mesh: MeshConfig
    node: ResolvedNode
    management: ManagementConfig
    fleet: FleetConfig

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mesh": self.mesh.to_dict(),
            "node": self.node.to_dict(),
            "management": self.management.to_dict(),
            "fleet": self.fleet.to_dict(),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def resolve_node_model(manifest: Manifest, node_name: str) -> ResolvedNode:
    defaults = _require_mapping(manifest.defaults, "defaults")
    node = manifest.get_node(node_name)
    if not isinstance(node, dict):
        raise ManifestError(
            f"Manifest node '{node_name}' must be a mapping, got {type(node).__name__}"
        )

    local_ap = _resolved_local_ap(defaults, node, node_name)
    gateway = _resolved_gateway(defaults, node, role=node.get("role", defaults.get("role", "point")))
    return ResolvedNode(
        name=node_name,
        hostname=node.get("hostname", node_name),
        role=node.get("role", defaults.get("role", "point")),
        target=node.get("target", defaults.get("target", "rpi4-mm6108-spi")),
        ip=node.get("ip", ""),
        local_ap=LocalApConfig.from_mapping(local_ap),
        gateway=GatewayConfig.from_mapping(gateway),
    )


def resolve_provision(
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> ProvisionPayload:
    mesh = _require_mapping(manifest.mesh, "mesh")
    defaults = _require_mapping(manifest.defaults, "defaults")
    management = defaults.get("management", {})
    if not isinstance(management, dict):
        raise ManifestError(
            f"defaults.management must be a mapping, got {type(management).__name__}"
        )

    return ProvisionPayload(
        version=1,
        mesh=MeshConfig.from_mapping(mesh),
        node=resolve_node_model(manifest, node_name),
        management=ManagementConfig.from_mapping(management, ssh_enabled=ssh_enabled),
        fleet=resolve_fleet_model(manifest),
    )


def resolve_fleet_model(manifest: Manifest) -> FleetConfig:
    return FleetConfig(
        nodes=tuple(
            FleetNode.from_resolved_node(resolve_node_model(manifest, name))
            for name in manifest.node_names()
        )
    )


def _require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ManifestError(
            f"Manifest section '{label}' must be a mapping, got {type(value).__name__}"
        )
    return value


def _mapping_or_empty(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _extra_fields(
    data: dict[str, object],
    known: set[str],
) -> dict[str, object]:
    return {key: value for key, value in data.items() if key not in known}


def _present_fields(
    data: dict[str, object],
    known: set[str],
) -> frozenset[str]:
    return frozenset(key for key in known if key in data)


def _with_present_fields(
    extra: dict[str, object],
    fields: frozenset[str],
    values: dict[str, object],
) -> dict[str, object]:
    payload = dict(extra)
    for key, value in values.items():
        if key in fields:
            payload[key] = value
    return payload


def _resolved_local_ap(
    defaults: dict[str, object],
    node: dict[str, object],
    node_name: str,
) -> dict[str, object]:
    resolved = {
        **_mapping_or_empty(defaults.get("local_ap", {})),
        **_mapping_or_empty(node.get("local_ap", {})),
    }
    if "enabled" not in resolved:
        resolved["enabled"] = False
    if resolved.get("ssid") is None:
        resolved["ssid"] = f"{node_name}-local"
    if resolved.get("enabled") and not resolved.get("password"):
        default_password = _mapping_or_empty(defaults.get("local_ap", {})).get("password", "")
        if default_password:
            resolved["password"] = default_password
    return resolved


def _resolved_gateway(
    defaults: dict[str, object],
    node: dict[str, object],
    *,
    role: object,
) -> dict[str, object]:
    default_gateway = _mapping_or_empty(defaults.get("gateway", {}))
    node_gateway = _mapping_or_empty(node.get("gateway", {}))
    resolved = {
        **default_gateway,
        **node_gateway,
    }
    default_wifi = default_gateway.get("wifi")
    node_wifi = node_gateway.get("wifi")
    if isinstance(default_wifi, dict):
        if "wifi" not in node_gateway:
            resolved["wifi"] = dict(default_wifi)
        elif isinstance(node_wifi, dict):
            resolved["wifi"] = {**default_wifi, **node_wifi}
    if role == "gate":
        resolved.setdefault("enabled", True)
    else:
        resolved.setdefault("enabled", False)
    return resolved
