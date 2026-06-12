"""Tests for config validation."""

import os
import tempfile

from easymanet.manifest import load_manifest
from easymanet.validate import resolve_node, validate


VALID_CONFIG = """
version: 1

mesh:
  id: test-mesh
  password: "test-password"
  channel: 42
  bandwidth_mhz: 2
  country: US

defaults:
  target: rpi4-mm6108-spi
  local_ap:
    enabled: true
    password: "ap-password"
  management:
    root_password_hash: ""
    ssh_authorized_keys:
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"

nodes:
  node01:
    role: gate
    hostname: node01
    ip: 10.41.1.1
    local_ap:
      ssid: node01-local
    gateway:
      enabled: true
      uplink_interface: eth0
  node02:
    role: point
    hostname: node02
    ip: 10.41.2.1
    local_ap:
      ssid: node02-local
"""


def _write_config(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".yml", prefix="easymanet_test_")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_valid_config():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    result = validate(m)
    assert result.valid
    os.unlink(path)


def test_missing_mesh_id():
    config = VALID_CONFIG.replace("id: test-mesh", "id:")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("mesh.id is required" in e for e in result.errors)
    os.unlink(path)


def test_missing_mesh_password():
    config = VALID_CONFIG.replace('password: "test-password"', 'password: ""')
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert any("mesh.password is required" in e for e in result.errors)
    os.unlink(path)


def test_invalid_bandwidth():
    config = VALID_CONFIG.replace("bandwidth_mhz: 2", "bandwidth_mhz: 3")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("bandwidth_mhz must be one of" in e for e in result.errors)
    os.unlink(path)


def test_invalid_bandwidth_5():
    config = VALID_CONFIG.replace("bandwidth_mhz: 2", "bandwidth_mhz: 5")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("bandwidth_mhz must be one of" in e for e in result.errors)
    os.unlink(path)


def test_mm6108_us_rejects_untested_channel_bandwidth_pair():
    config = VALID_CONFIG.replace("channel: 42", "channel: 36").replace(
        "bandwidth_mhz: 2",
        "bandwidth_mhz: 4",
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("rpi4-mm6108-spi in US" in e for e in result.errors)
    os.unlink(path)


def test_mesh_channel_must_be_numeric():
    config = VALID_CONFIG.replace("channel: 42", "channel: abc")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("mesh.channel must be numeric" in e for e in result.errors)
    os.unlink(path)


def test_duplicate_hostname():
    config = VALID_CONFIG.replace("hostname: node02", "hostname: node01")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Duplicate hostname" in e for e in result.errors)
    os.unlink(path)


def test_duplicate_ip():
    config = VALID_CONFIG.replace("ip: 10.41.2.1", "ip: 10.41.1.1")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Duplicate IP" in e for e in result.errors)
    os.unlink(path)


def test_missing_selected_node():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    result = validate(m, node_name="nonexistent")
    assert not result.valid
    assert any("does not exist" in e for e in result.errors)
    os.unlink(path)


def test_invalid_ip():
    config = VALID_CONFIG.replace("ip: 10.41.2.1", "ip: not-an-ip")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Invalid IP" in e for e in result.errors)
    os.unlink(path)


def test_ipv6_node_ip_is_invalid():
    config = VALID_CONFIG.replace("ip: 10.41.2.1", "ip: fd00::1")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Invalid IPv4 address" in e for e in result.errors)
    os.unlink(path)


def test_invalid_role():
    config = VALID_CONFIG.replace("role: point", "role: drone")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("role must be one of" in e for e in result.errors)
    os.unlink(path)


def test_invalid_target():
    config = VALID_CONFIG.replace("target: rpi4-mm6108-spi", "target: rpi5")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("target must be one of" in e for e in result.errors)
    os.unlink(path)


def test_warning_no_ssh_keys():
    config = VALID_CONFIG.replace(
        'ssh_authorized_keys:\n      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"',
        "ssh_authorized_keys: []"
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert result.valid
    assert any("No SSH authorized keys" in w for w in result.warnings)
    os.unlink(path)


def test_warning_empty_root_password():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    result = validate(m)
    assert result.valid
    assert any("root_password_hash is empty" in w for w in result.warnings)
    os.unlink(path)


def test_no_nodes():
    config = """
version: 1
mesh:
  id: test
  password: "pw"
  channel: 1
  bandwidth_mhz: 1
  country: US
defaults:
  target: rpi4-mm6108-spi
nodes: {}
"""
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("at least one node" in e for e in result.errors)
    os.unlink(path)


def test_valid_config_with_node():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    result = validate(m, node_name="node01")
    assert result.valid
    os.unlink(path)


def test_mesh_channel_zero_is_valid():
    config = VALID_CONFIG.replace("channel: 42", "channel: 0")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert result.valid
    os.unlink(path)


def test_invalid_country_code():
    config = VALID_CONFIG.replace("country: US", "country: usa")
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("mesh.country" in e for e in result.errors)
    os.unlink(path)


def test_defaults_local_ap_must_be_mapping():
    needle = (
        '  local_ap:\n'
        '    enabled: true\n'
        '    password: "ap-password"'
    )
    config = VALID_CONFIG.replace(
        needle,
        "  local_ap: not-a-mapping",
        1,
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("defaults.local_ap must be a mapping" in e for e in result.errors)
    os.unlink(path)


def test_defaults_local_ap_password_validated_when_inherited():
    config = VALID_CONFIG.replace('password: "ap-password"', 'password: "short"', 1)
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("local_ap.password must be at least 8 characters" in e for e in result.errors)
    os.unlink(path)


def test_local_ap_enabled_requires_password():
    config = VALID_CONFIG.replace('password: "ap-password"', "", 1)
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("local_ap.enabled requires local_ap.password" in e for e in result.errors)
    os.unlink(path)


def test_local_ap_password_must_be_string():
    config = VALID_CONFIG.replace('password: "ap-password"', "password: 12345678", 1)
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("local_ap.password must be a string" in e for e in result.errors)
    os.unlink(path)


def test_validate_ssh_key_accepts_sk_and_ecdsa_types():
    from easymanet.validate import validate_ssh_key

    assert validate_ssh_key(
        "sk-ssh-ed25519@openssh.com AAAAC3NzaC1lZDI1NTE5AAAAIComment"
    ) is None
    assert validate_ssh_key(
        "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTIybnRzdDI1NgIComment with spaces"
    ) is None
    assert validate_ssh_key("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIkm8 key comment") is None


def test_defaults_gateway_must_be_mapping():
    config = VALID_CONFIG.replace(
        "defaults:\n  target: rpi4-mm6108-spi",
        "defaults:\n  target: rpi4-mm6108-spi\n  gateway: not-a-mapping",
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("defaults.gateway must be a mapping" in e for e in result.errors)
    os.unlink(path)


def test_defaults_management_must_be_mapping():
    needle = (
        '  management:\n'
        '    root_password_hash: ""\n'
        '    ssh_authorized_keys:\n'
        '      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"'
    )
    config = VALID_CONFIG.replace(
        needle,
        "  management: not-a-mapping",
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("defaults.management must be a mapping" in e for e in result.errors)
    os.unlink(path)


def test_ssh_authorized_keys_must_be_list():
    config = VALID_CONFIG.replace(
        'ssh_authorized_keys:\n      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"',
        "ssh_authorized_keys: not-a-list",
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("ssh_authorized_keys must be a list" in e for e in result.errors)
    os.unlink(path)


def test_ssh_authorized_keys_entries_must_be_strings():
    config = VALID_CONFIG.replace(
        '      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"',
        "      - 123",
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("ssh_authorized_keys entries must be strings" in e for e in result.errors)
    os.unlink(path)


def test_node_names_must_be_strings():
    config = VALID_CONFIG.replace("  node02:", "  2:", 1)
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Node name must be a string" in e for e in result.errors)
    os.unlink(path)


def test_node_gateway_must_be_mapping():
    config = VALID_CONFIG.replace(
        "    gateway:\n      enabled: true\n      uplink_interface: eth0",
        '    gateway: "not-a-mapping"',
    )
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m)
    assert not result.valid
    assert any("Node 'node01': gateway must be a mapping" in e for e in result.errors)
    os.unlink(path)


def test_resolve_node_non_dict_local_ap_and_gateway():
    config = VALID_CONFIG.replace(
        "    local_ap:\n      ssid: node01-local",
        "    local_ap: true",
    ).replace(
        "    gateway:\n      enabled: true\n      uplink_interface: eth0",
        "    gateway: disabled",
    )
    path = _write_config(config)
    m = load_manifest(path)
    resolved = resolve_node(m, "node01")
    assert isinstance(resolved["local_ap"], dict)
    assert isinstance(resolved["gateway"], dict)
    assert resolved["local_ap"]["ssid"] == "node01-local"
    os.unlink(path)


def test_gateway_wifi_requires_ssid_and_password():
    config = VALID_CONFIG + """
  node03:
    role: gate
    hostname: node03
    ip: 10.41.3.1
    gateway:
      enabled: true
      wifi:
        enabled: true
"""
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m, node_name="node03")
    assert not result.valid
    assert any("gateway.wifi.ssid" in e for e in result.errors)
    assert any("gateway.wifi.password" in e for e in result.errors)
    os.unlink(path)


def test_gateway_wifi_validation_uses_preserved_shallow_merge():
    config = VALID_CONFIG.replace(
        "defaults:\n  target: rpi4-mm6108-spi",
        """
defaults:
  target: rpi4-mm6108-spi
  gateway:
    wifi:
      enabled: false
      ssid: default-uplink
      password: default-password
""".strip(),
    ) + """
  node03:
    role: gate
    hostname: node03
    ip: 10.41.3.1
    gateway:
      wifi:
        enabled: true
"""
    path = _write_config(config)
    m = load_manifest(path)
    result = validate(m, node_name="node03")
    assert not result.valid
    assert any("gateway.wifi.ssid" in e for e in result.errors)
    assert any("gateway.wifi.password" in e for e in result.errors)
    os.unlink(path)
