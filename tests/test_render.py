"""Tests for config rendering (defaults merging + provision.json output)."""

import json
import os
import tempfile

import pytest

from easymanet.manifest import ManifestError, load_manifest
from easymanet.provision import ProvisionPayload, resolve_provision
from easymanet.render import render, render_dict


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


def test_render_valid_provision_json():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    output = render(m, "node02")
    data = json.loads(output)

    assert data["version"] == 1
    assert data["mesh"]["id"] == "test-mesh"
    assert data["mesh"]["password"] == "test-password"
    assert data["mesh"]["channel"] == 42
    assert data["mesh"]["bandwidth_mhz"] == 2
    assert data["mesh"]["country"] == "US"

    assert data["node"]["name"] == "node02"
    assert data["node"]["hostname"] == "node02"
    assert data["node"]["role"] == "point"
    assert data["node"]["target"] == "rpi4-mm6108-spi"
    assert data["node"]["ip"] == "10.41.2.1"

    assert data["node"]["local_ap"]["enabled"] is True
    assert data["node"]["local_ap"]["ssid"] == "node02-local"
    assert data["node"]["local_ap"]["password"] == "ap-password"

    assert data["node"]["gateway"]["enabled"] is False

    assert data["management"]["root_password_hash"] == ""
    assert len(data["management"]["ssh_authorized_keys"]) == 1

    os.unlink(path)


def test_render_includes_non_secret_fleet_inventory():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    data = render_dict(m, "node02")

    assert data["fleet"]["nodes"] == [
        {
            "name": "node01",
            "hostname": "node01",
            "role": "gate",
            "target": "rpi4-mm6108-spi",
            "ip": "10.41.1.1",
        },
        {
            "name": "node02",
            "hostname": "node02",
            "role": "point",
            "target": "rpi4-mm6108-spi",
            "ip": "10.41.2.1",
        },
    ]
    serialized = json.dumps(data["fleet"])
    assert "test-password" not in serialized
    assert "ap-password" not in serialized
    assert "ssh-ed25519" not in serialized
    os.unlink(path)


def test_render_gate_node():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    data = render_dict(m, "node01")

    assert data["node"]["role"] == "gate"
    assert data["node"]["gateway"]["enabled"] is True
    assert data["node"]["gateway"]["uplink_interface"] == "eth0"
    os.unlink(path)


def test_render_defaults_merge():
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
  local_ap:
    enabled: false
    password: "default-ap-pw"
  management:
    root_password_hash: "$6$hash"
    ssh_authorized_keys: []
nodes:
  n1:
    role: point
    hostname: n1
    ip: 10.0.0.1
    local_ap:
      enabled: true
      ssid: n1-custom
"""
    path = _write_config(config)
    m = load_manifest(path)
    data = render_dict(m, "n1")

    assert data["node"]["local_ap"]["enabled"] is True
    assert data["node"]["local_ap"]["ssid"] == "n1-custom"
    assert data["node"]["local_ap"]["password"] == "default-ap-pw"
    assert data["management"]["root_password_hash"] == "$6$hash"
    os.unlink(path)


def test_resolve_provision_returns_typed_payload_used_by_render():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)

    payload = resolve_provision(m, "node02", ssh_enabled=True)

    assert isinstance(payload, ProvisionPayload)
    assert payload.management.ssh_enabled is True
    assert payload.node.local_ap.enabled is True
    assert payload.node.local_ap.ssid == "node02-local"
    assert payload.node.local_ap.password == "ap-password"
    assert payload.node.gateway.enabled is False
    assert payload.node.gateway.wifi is None
    assert payload.to_dict() == render_dict(m, "node02", ssh_enabled=True)
    os.unlink(path)


def test_render_deep_merges_gateway_wifi_defaults():
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
  gateway:
    enabled: true
    wifi:
      enabled: false
      ssid: default-uplink
      password: default-password
  management:
    root_password_hash: ""
    ssh_authorized_keys: []
nodes:
  n1:
    role: gate
    hostname: n1
    ip: 10.0.0.1
    gateway:
      wifi:
        enabled: true
"""
    path = _write_config(config)
    m = load_manifest(path)
    payload = resolve_provision(m, "n1")
    data = render_dict(m, "n1")

    assert payload.node.gateway.enabled is True
    assert payload.node.gateway.wifi is not None
    assert payload.node.gateway.wifi.enabled is True
    assert data["node"]["gateway"]["wifi"] == {
        "enabled": True,
        "ssid": "default-uplink",
        "password": "default-password",
    }
    os.unlink(path)


def test_render_starter_gate_uses_wifi_uplink_shape():
    m = load_manifest("examples/three-node-field-mesh.yml")
    gate = render_dict(m, "gate01", ssh_enabled=True)

    assert gate["node"]["gateway"]["enabled"] is True
    assert gate["node"]["gateway"]["uplink_interface"] == "wifi"
    assert gate["node"]["gateway"]["wifi"]["enabled"] is True
    assert gate["node"]["gateway"]["wifi"]["ssid"]
    assert gate["node"]["gateway"]["wifi"]["password"]
    assert gate["management"]["ssh_enabled"] is True


def test_render_omits_ssh_enabled_when_unspecified():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    gate = render_dict(m, "node01")
    point = render_dict(m, "node02")

    assert "ssh_enabled" not in gate["management"]
    assert "ssh_enabled" not in point["management"]
    os.unlink(path)


def test_render_ssh_enabled_explicit_override():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)

    point_on = render_dict(m, "node02", ssh_enabled=True)
    assert point_on["management"]["ssh_enabled"] is True

    gate_off = render_dict(m, "node01", ssh_enabled=False)
    assert gate_off["management"]["ssh_enabled"] is False
    os.unlink(path)


def test_render_no_local_ap():
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
  management:
    root_password_hash: ""
    ssh_authorized_keys: []
nodes:
  n1:
    role: point
    hostname: n1
    ip: 10.0.0.1
"""
    path = _write_config(config)
    m = load_manifest(path)
    data = render_dict(m, "n1")

    assert data["node"]["local_ap"]["enabled"] is False
    os.unlink(path)


def test_render_rejects_malformed_management_defaults():
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
    with pytest.raises(ManifestError, match="defaults.management must be a mapping"):
        render(m, "node01")
    os.unlink(path)


def test_render_rejects_malformed_mesh_object():
    class BadManifest:
        mesh = "not-a-mapping"
        defaults = {}
        nodes = {"node01": {}}

        def get_node(self, name):
            return self.nodes[name]

    with pytest.raises(ManifestError, match="'mesh' must be a mapping"):
        render(BadManifest(), "node01")
