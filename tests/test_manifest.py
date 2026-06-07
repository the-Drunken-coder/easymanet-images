"""Tests for manifest loading and parsing."""

import os
import tempfile

import pytest
import yaml

from easymanet.manifest import load_manifest, ManifestError


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


def test_load_valid_config():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    assert m.version == 1
    assert m.mesh["id"] == "test-mesh"
    assert len(m.nodes) == 2
    assert "node01" in m.nodes
    assert "node02" in m.nodes
    os.unlink(path)


def test_load_missing_file():
    with pytest.raises(ManifestError, match="not found"):
        load_manifest("/nonexistent/fleet.yml")


def test_load_read_error_is_manifest_error(tmp_path):
    with pytest.raises(ManifestError, match="Could not read config file") as exc_info:
        load_manifest(str(tmp_path))
    assert isinstance(exc_info.value.__cause__, OSError)


def test_load_invalid_yaml():
    path = _write_config(": invalid: yaml: [")
    with pytest.raises(ManifestError, match="Invalid YAML") as exc_info:
        load_manifest(path)
    assert isinstance(exc_info.value.__cause__, yaml.YAMLError)
    os.unlink(path)


def test_load_non_mapping_root():
    path = _write_config("not a mapping\n")
    with pytest.raises(ManifestError, match="root must be a mapping"):
        load_manifest(path)
    os.unlink(path)


def test_load_non_mapping_section():
    path = _write_config("version: 1\nmesh: not-a-mapping\n")
    with pytest.raises(ManifestError, match="'mesh' must be a mapping"):
        load_manifest(path)
    os.unlink(path)


def test_load_nodes_must_be_mapping():
    path = _write_config("version: 1\nnodes:\n  - node01\n")
    with pytest.raises(ManifestError, match="'nodes' must be a mapping"):
        load_manifest(path)
    os.unlink(path)


def test_load_defaults_must_be_mapping():
    path = _write_config("version: 1\ndefaults: not-a-mapping\n")
    with pytest.raises(ManifestError, match="'defaults' must be a mapping"):
        load_manifest(path)
    os.unlink(path)


def test_load_node_entry_must_be_mapping():
    path = _write_config("version: 1\nnodes:\n  node01: not-a-mapping\n")
    with pytest.raises(ManifestError, match="node 'node01' must be a mapping"):
        load_manifest(path)
    os.unlink(path)


def test_get_node():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    node = m.get_node("node01")
    assert node["role"] == "gate"
    assert node["ip"] == "10.41.1.1"
    os.unlink(path)


def test_get_node_missing():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    with pytest.raises(ManifestError, match="not found"):
        m.get_node("nonexistent")
    os.unlink(path)


def test_node_names():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    names = m.node_names()
    assert set(names) == {"node01", "node02"}
    os.unlink(path)


def test_defaults_access():
    path = _write_config(VALID_CONFIG)
    m = load_manifest(path)
    assert m.get_default("target") == "rpi4-mm6108-spi"
    assert m.get_default("nonexistent", "fallback") == "fallback"
    os.unlink(path)
