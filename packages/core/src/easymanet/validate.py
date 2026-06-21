"""Config validation.

Validates fleet.yml configuration against all required rules.
Returns a list of errors and warnings.
"""

import ipaddress
import re
from typing import List, Optional

from .manifest import Manifest
from .provision import GatewayConfig, LocalApConfig, resolve_node_model

VALID_ROLES = {"gate", "point"}
VALID_TARGETS = {"rpi4-mm6108-spi"}
VALID_BANDWIDTHS = {1, 2, 4, 8}
MM6108_TARGET = "rpi4-mm6108-spi"
MM6108_US_VALID_MESH = {(42, 2)}
VALID_WIFI_ENCRYPTION = {"psk2", "sae", "none", "psk", "psk-mixed"}
COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")

SSH_KEY_PATTERN = re.compile(
    r"^(?:"
    r"ssh-(?:ed25519|rsa|ecdsa|dss)|"
    r"ecdsa-sha2-nistp(?:256|384|521)|"
    r"sk-(?:ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com"
    r")\s+[A-Za-z0-9+/]+={0,2}(?:\s+.+)?$"
)


class ValidationResult:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


def validate_ip(ip_str: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return f"Invalid IP address: {ip_str}"
    if not isinstance(ip, ipaddress.IPv4Address):
        return f"Invalid IPv4 address: {ip_str}"
    return None


def validate_ssh_key(key: str) -> Optional[str]:
    if not SSH_KEY_PATTERN.match(key.strip()):
        return f"Invalid SSH public key format: {key[:50]}..."
    return None


def _as_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def validate(manifest: Manifest, node_name: Optional[str] = None) -> ValidationResult:
    result = ValidationResult()

    if manifest.version != 1:
        result.add_error(f"version must be 1, got {manifest.version}")

    mesh = manifest.mesh
    if not mesh:
        result.add_error("mesh section is required")
    elif not isinstance(mesh, dict):
        result.add_error(f"mesh section must be a mapping, got {type(mesh).__name__}")
    else:
        if not mesh.get("id"):
            result.add_error("mesh.id is required")
        if not mesh.get("password"):
            result.add_error("mesh.password is required")
        if mesh.get("channel") is None:
            result.add_error("mesh.channel is required")
        if mesh.get("bandwidth_mhz") is None:
            result.add_error("mesh.bandwidth_mhz is required")
        else:
            bandwidth = _as_int(mesh["bandwidth_mhz"])
            if bandwidth is None:
                result.add_error(
                    f"mesh.bandwidth_mhz must be numeric, got '{mesh['bandwidth_mhz']}'"
                )
            elif bandwidth not in VALID_BANDWIDTHS:
                result.add_error(
                    f"mesh.bandwidth_mhz must be one of {sorted(VALID_BANDWIDTHS)}, "
                    f"got {mesh['bandwidth_mhz']}"
                )
        country = mesh.get("country", "")
        if not country:
            result.add_error("mesh.country is required")
        elif not COUNTRY_PATTERN.match(str(country)):
            result.add_error(
                f"mesh.country must be a two-letter ISO country code (e.g. US), got '{country}'"
            )

    nodes = manifest.nodes
    if not nodes:
        result.add_error("nodes section is required (at least one node must be defined)")
        return result
    if not isinstance(nodes, dict):
        result.add_error(f"nodes section must be a mapping, got {type(nodes).__name__}")
        return result

    defaults = manifest.defaults
    if not isinstance(defaults, dict):
        result.add_error(f"defaults section must be a mapping, got {type(defaults).__name__}")
        defaults = {}

    hostnames_seen: dict = {}
    ips_seen: dict = {}
    node_names_lower = set()

    default_gateway = defaults.get("gateway", {})
    if not isinstance(default_gateway, dict):
        result.add_error(
            f"defaults.gateway must be a mapping, got {type(default_gateway).__name__}"
        )
        default_gateway = {}

    default_local_ap = defaults.get("local_ap", {})
    if not isinstance(default_local_ap, dict):
        result.add_error(
            f"defaults.local_ap must be a mapping, got {type(default_local_ap).__name__}"
        )
        default_local_ap = {}

    management = defaults.get("management", {})
    if not isinstance(management, dict):
        result.add_error(
            f"defaults.management must be a mapping, got {type(management).__name__}"
        )
        management = {}

    _validate_target_mesh_settings(result, mesh, defaults, nodes, node_name)

    for name in nodes:
        if not isinstance(name, str):
            result.add_error(f"Node name must be a string, got {type(name).__name__}")
            continue
        if name.lower() in node_names_lower:
            result.add_error(f"Duplicate node name (case-insensitive): {name}")
        node_names_lower.add(name.lower())

        node = nodes[name]
        if not isinstance(node, dict):
            result.add_error(f"Node '{name}' must be a mapping, got {type(node).__name__}")
            continue

        role = node.get("role", defaults.get("role", "point"))
        if role not in VALID_ROLES:
            result.add_error(f"Node '{name}': role must be one of {sorted(VALID_ROLES)}, got '{role}'")

        target = node.get("target", defaults.get("target"))
        if target not in VALID_TARGETS:
            result.add_error(
                f"Node '{name}': target must be one of {sorted(VALID_TARGETS)}, got '{target}'"
            )

        hostname = node.get("hostname", "")
        if not hostname:
            result.add_error(f"Node '{name}': hostname is required")
        elif hostname in hostnames_seen:
            result.add_error(
                f"Duplicate hostname '{hostname}' in node '{name}' "
                f"(also used by node '{hostnames_seen[hostname]}')"
            )
        else:
            hostnames_seen[hostname] = name

        ip = node.get("ip", "")
        if not ip:
            result.add_error(f"Node '{name}': ip is required")
        else:
            err = validate_ip(ip)
            if err:
                result.add_error(f"Node '{name}': {err}")
            elif ip in ips_seen:
                result.add_error(
                    f"Duplicate IP '{ip}' in node '{name}' "
                    f"(also used by node '{ips_seen[ip]}')"
                )
            else:
                ips_seen[ip] = name

        node_local_ap = node.get("local_ap", {})
        if not isinstance(node_local_ap, dict):
            result.add_error(
                f"Node '{name}': local_ap must be a mapping, got {type(node_local_ap).__name__}"
            )

        node_gateway = node.get("gateway", {})
        if not isinstance(node_gateway, dict):
            result.add_error(
                f"Node '{name}': gateway must be a mapping, got {type(node_gateway).__name__}"
            )
        if isinstance(manifest.defaults, dict):
            resolved = resolve_node_model(manifest, name)
            _validate_local_ap(result, name, resolved.local_ap)
            if resolved.gateway.enabled and role == "gate":
                uplink = resolved.gateway.uplink_interface
                if not uplink:
                    result.add_warning(
                        f"Node '{name}': gate role without gateway.uplink_interface set"
                    )
            _validate_gateway_wifi(result, name, resolved.gateway)

    ssh_keys = management.get("ssh_authorized_keys", [])
    if not ssh_keys:
        result.add_warning("No SSH authorized keys provided in defaults.management.ssh_authorized_keys")
    elif not isinstance(ssh_keys, list):
        result.add_error(
            f"defaults.management.ssh_authorized_keys must be a list, got {type(ssh_keys).__name__}"
        )
    else:
        for key in ssh_keys:
            if not isinstance(key, str):
                result.add_error(
                    f"defaults.management.ssh_authorized_keys entries must be strings, got {type(key).__name__}"
                )
                continue
            err = validate_ssh_key(key)
            if err:
                result.add_error(f"Invalid SSH key: {err}")

    root_pw_hash = management.get("root_password_hash", "")
    if not root_pw_hash:
        result.add_warning("root_password_hash is empty — root password will not be set")

    if node_name is not None:
        if node_name not in nodes:
            result.add_error(f"Selected node '{node_name}' does not exist in manifest")
        elif not isinstance(nodes[node_name], dict):
            result.add_error(
                f"Selected node '{node_name}' must be a mapping, got {type(nodes[node_name]).__name__}"
            )
        elif not isinstance(manifest.defaults, dict):
            pass

    return result


def _validate_target_mesh_settings(
    result: ValidationResult,
    mesh: object,
    defaults: dict,
    nodes: dict,
    node_name: Optional[str],
) -> None:
    if not isinstance(mesh, dict):
        return
    channel = _as_int(mesh.get("channel"))
    bandwidth = _as_int(mesh.get("bandwidth_mhz"))
    country = str(mesh.get("country", ""))
    if mesh.get("channel") is not None and channel is None:
        result.add_error(f"mesh.channel must be numeric, got '{mesh.get('channel')}'")
        return
    if mesh.get("bandwidth_mhz") is not None and bandwidth is None:
        message = f"mesh.bandwidth_mhz must be numeric, got '{mesh.get('bandwidth_mhz')}'"
        if message not in result.errors:
            result.add_error(message)
        return
    if channel is None or bandwidth is None or bandwidth not in VALID_BANDWIDTHS:
        return
    if not COUNTRY_PATTERN.match(country):
        return

    target_names = _targets_for_mesh_validation(defaults, nodes, node_name)
    if MM6108_TARGET not in target_names:
        return
    if country == "US" and (channel, bandwidth) not in MM6108_US_VALID_MESH:
        result.add_error(
            "mesh.channel/bandwidth_mhz for rpi4-mm6108-spi in US must be "
            "channel 42 with bandwidth_mhz 2; "
            f"got channel {channel} with bandwidth_mhz {bandwidth}"
        )


def _targets_for_mesh_validation(
    defaults: dict,
    nodes: dict,
    node_name: Optional[str],
) -> set[str]:
    names = [node_name] if node_name in nodes else list(nodes)
    targets: set[str] = set()
    for name in names:
        node = nodes.get(name)
        if not isinstance(node, dict):
            continue
        targets.add(str(node.get("target", defaults.get("target", ""))))
    return targets


def _validate_local_ap(
    result: ValidationResult,
    node_label: str,
    local_ap: LocalApConfig,
) -> None:
    if not local_ap.enabled:
        return
    password = local_ap.password
    if not password:
        result.add_error(
            f"Node '{node_label}': local_ap.enabled requires local_ap.password"
        )
    elif not isinstance(password, str):
        result.add_error(
            f"Node '{node_label}': local_ap.password must be a string"
        )
    elif len(password) < 8:
        result.add_error(
            f"Node '{node_label}': local_ap.password must be at least 8 characters"
        )


def _validate_gateway_wifi(
    result: ValidationResult,
    node_label: str,
    gateway: GatewayConfig,
) -> None:
    wifi = gateway.wifi
    if wifi is None or not wifi.enabled:
        return
    ssid = wifi.ssid
    password = wifi.password
    if not ssid:
        result.add_error(
            f"Node '{node_label}': gateway.wifi.enabled requires gateway.wifi.ssid"
        )
    if not password:
        result.add_error(
            f"Node '{node_label}': gateway.wifi.enabled requires gateway.wifi.password"
        )
    encryption = wifi.encryption
    if encryption is not None and encryption not in VALID_WIFI_ENCRYPTION:
        result.add_error(
            f"Node '{node_label}': gateway.wifi.encryption must be one of "
            f"{sorted(VALID_WIFI_ENCRYPTION)}, got '{encryption}'"
        )


def resolve_node(manifest: Manifest, node_name: str) -> dict:
    return resolve_node_model(manifest, node_name).to_dict()
