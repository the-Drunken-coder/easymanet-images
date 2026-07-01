"""Tests for boot-partition staging."""

import json
import plistlib
import stat

from easymanet.inject import (
    _cleanup_mount,
    _find_boot_mount,
    _find_boot_partition,
    inject,
    inject_dry_run_info,
)
from easymanet.manifest import load_manifest


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
"""


def _write_config(tmp_path, content: str) -> str:
    path = tmp_path / "easymanet_test.yml"
    path.write_text(content)
    return str(path)


def test_find_boot_partition_macos_uses_content_when_filesystem_type_missing(monkeypatch):
    list_plist = {
        "AllDisksAndPartitions": [
            {
                "DeviceIdentifier": "disk4",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk4s1",
                        "Content": "Windows_FAT_32",
                        "MountPoint": "/Volumes/boot",
                    },
                    {
                        "DeviceIdentifier": "disk4s2",
                        "Content": "Linux",
                    },
                ],
            }
        ]
    }

    monkeypatch.setattr("easymanet.inject.is_macos", lambda: True)
    monkeypatch.setattr("easymanet.inject.is_linux", lambda: False)

    def fake_check_output(cmd, timeout=15):
        assert cmd[:3] == ["diskutil", "list", "-plist"]
        return plistlib.dumps(list_plist)

    monkeypatch.setattr("easymanet.inject.subprocess.check_output", fake_check_output)

    assert _find_boot_partition("/dev/disk4") == "/dev/disk4s1"
    assert _find_boot_mount("/dev/disk4") == "/Volumes/boot"


def test_inject_dry_run_info_mentions_boot_partition(tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)

    info = inject_dry_run_info(manifest, "node01")

    assert "/easymanet/provision.json" in info
    assert "first-boot hooks" in info


def test_inject_writes_provision_json_to_boot_partition(monkeypatch, tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)
    boot_mount = tmp_path / "boot"
    boot_mount.mkdir()

    monkeypatch.setattr(
        "easymanet.inject._mount_boot_partition",
        lambda _device: (str(boot_mount), False),
    )
    monkeypatch.setattr(
        "easymanet.inject._cleanup_mount",
        lambda _device, _mount_point, _mounted_here: None,
    )

    results = inject("/dev/disk4", manifest, "node01")

    written = boot_mount / "easymanet" / "provision.json"
    assert written.exists()
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    data = json.loads(written.read_text())
    assert data["node"]["name"] == "node01"
    assert results[0] == ("/boot/easymanet/provision.json", True)


def test_inject_patches_rpi_boot_root_to_partuuid(monkeypatch, tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)
    boot_mount = tmp_path / "boot"
    boot_mount.mkdir()
    cmdline = boot_mount / "cmdline.txt"
    cmdline.write_text(
        "console=serial0 console=tty1 root=/dev/mmcblk0p2 rootfstype=squashfs,ext4 rootwait\n"
    )
    (boot_mount / "partuuid.txt").write_text("a7ad1f13\n")

    monkeypatch.setattr(
        "easymanet.inject._mount_boot_partition",
        lambda _device: (str(boot_mount), False),
    )
    monkeypatch.setattr(
        "easymanet.inject._cleanup_mount",
        lambda _device, _mount_point, _mounted_here: None,
    )

    results = inject("/dev/disk4", manifest, "node01")

    assert "root=PARTUUID=a7ad1f13-02" in cmdline.read_text()
    assert "root=/dev/mmcblk0p2" not in cmdline.read_text()
    assert (boot_mount / "cmdline.txt.easymanet.bak").read_text().startswith("console=serial0")
    assert results[-1] == ("/boot/cmdline.txt root=PARTUUID=a7ad1f13-02", True)


def test_inject_patches_usb_sda_root_to_partuuid(monkeypatch, tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)
    boot_mount = tmp_path / "boot"
    boot_mount.mkdir()
    cmdline = boot_mount / "cmdline.txt"
    cmdline.write_text(
        "console=ttyAMA0 root=/dev/sda2 rootfstype=squashfs rootwait\n"
    )
    (boot_mount / "partuuid.txt").write_text("b3c4d5e6\n")

    monkeypatch.setattr(
        "easymanet.inject._mount_boot_partition",
        lambda _device: (str(boot_mount), False),
    )
    monkeypatch.setattr(
        "easymanet.inject._cleanup_mount",
        lambda _device, _mount_point, _mounted_here: None,
    )

    results = inject("/dev/disk4", manifest, "node01")

    assert "root=PARTUUID=b3c4d5e6-02" in cmdline.read_text()
    assert "root=/dev/sda2" not in cmdline.read_text()
    assert (boot_mount / "cmdline.txt.easymanet.bak").read_text().startswith("console=ttyAMA0")
    assert results[-1] == ("/boot/cmdline.txt root=PARTUUID=b3c4d5e6-02", True)


def test_inject_patches_nvme_root_to_partuuid(monkeypatch, tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)
    boot_mount = tmp_path / "boot"
    boot_mount.mkdir()
    cmdline = boot_mount / "cmdline.txt"
    cmdline.write_text(
        "console=ttyAMA0 root=/dev/nvme0n1p2 rootfstype=squashfs rootwait\n"
    )
    (boot_mount / "partuuid.txt").write_text("c8d9e0f1\n")

    monkeypatch.setattr(
        "easymanet.inject._mount_boot_partition",
        lambda _device: (str(boot_mount), False),
    )
    monkeypatch.setattr(
        "easymanet.inject._cleanup_mount",
        lambda _device, _mount_point, _mounted_here: None,
    )

    results = inject("/dev/disk4", manifest, "node01")

    assert "root=PARTUUID=c8d9e0f1-02" in cmdline.read_text()
    assert "root=/dev/nvme0n1p2" not in cmdline.read_text()
    assert (boot_mount / "cmdline.txt.easymanet.bak").read_text().startswith("console=ttyAMA0")
    assert results[-1] == ("/boot/cmdline.txt root=PARTUUID=c8d9e0f1-02", True)


def test_inject_leaves_existing_boot_root_alone(monkeypatch, tmp_path):
    path = _write_config(tmp_path, VALID_CONFIG)
    manifest = load_manifest(path)
    boot_mount = tmp_path / "boot"
    boot_mount.mkdir()
    cmdline = boot_mount / "cmdline.txt"
    original = "console=serial0 console=tty1 root=PARTUUID=a7ad1f13-02 rootwait\n"
    cmdline.write_text(original)
    (boot_mount / "partuuid.txt").write_text("a7ad1f13\n")

    monkeypatch.setattr(
        "easymanet.inject._mount_boot_partition",
        lambda _device: (str(boot_mount), False),
    )
    monkeypatch.setattr(
        "easymanet.inject._cleanup_mount",
        lambda _device, _mount_point, _mounted_here: None,
    )

    results = inject("/dev/disk4", manifest, "node01")

    assert cmdline.read_text() == original
    assert not (boot_mount / "cmdline.txt.easymanet.bak").exists()
    assert all("cmdline.txt" not in path for path, _ok in results)


def test_cleanup_mount_reports_failed_linux_unmount(monkeypatch, tmp_path, capsys):
    mount_point = tmp_path / "boot"
    mount_point.mkdir()

    class Result:
        returncode = 1
        stderr = "busy"

    monkeypatch.setattr("easymanet.inject.is_macos", lambda: False)
    monkeypatch.setattr("easymanet.inject.is_linux", lambda: True)
    monkeypatch.setattr("easymanet.inject.subprocess.run", lambda *_a, **_k: Result())

    _cleanup_mount("/dev/disk4", str(mount_point), True)

    captured = capsys.readouterr()
    assert "umount failed" in captured.err
    assert mount_point.exists()
