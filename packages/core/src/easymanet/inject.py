"""Write node-specific provisioning payloads to the boot partition."""

import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .manifest import Manifest
from .platform import is_linux, is_macos
from .render import render

ROOT_BLOCK_DEVICE_PATTERN = re.compile(r"root=(/dev/[^\s]+)")

# diskutil list -plist often omits FilesystemType; Content is set on current macOS.
_MACOS_FAT_CONTENTS = frozenset(
    {
        "Windows_FAT_32",
        "EFI",
        "DOS_FAT_12",
        "DOS_FAT_16",
        "DOS_FAT_32",
    }
)
_MACOS_FAT_FILESYSTEMS = frozenset({"msdos", "vfat", "fat32", "exfat"})

_SUBPROCESS_ERRORS = (
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
    FileNotFoundError,
)
_BOOT_PARTITION_PARSE_ERRORS = (
    *_SUBPROCESS_ERRORS,
    OSError,
    ValueError,
    UnicodeDecodeError,
    plistlib.InvalidFileException,
)


def _debug_note(message: str) -> None:
    print(f"easymanet: {message}", file=sys.stderr)


class InjectError(Exception):
    pass


def inject(
    device: str,
    manifest: Manifest,
    node_name: str,
    dry_run: bool = False,
    *,
    ssh_enabled: Optional[bool] = None,
) -> List[Tuple[str, bool]]:
    if dry_run:
        render(manifest, node_name, ssh_enabled=ssh_enabled)
        return [
            ("/boot/easymanet/provision.json", True),
            ("Base image must already include EasyMANET first-boot hooks", True),
        ]

    mount_point, mounted_here = _mount_boot_partition(device)
    try:
        return stage_boot_payload(
            Path(mount_point),
            manifest,
            node_name,
            ssh_enabled=ssh_enabled,
        )
    except OSError as e:
        raise InjectError(f"Failed to write boot-partition provision.json: {e}") from e
    finally:
        _cleanup_mount(device, mount_point, mounted_here)


def stage_boot_payload(
    boot_root: Path,
    manifest: Manifest,
    node_name: str,
    *,
    ssh_enabled: Optional[bool] = None,
) -> List[Tuple[str, bool]]:
    provision_json = render(manifest, node_name, ssh_enabled=ssh_enabled)
    dest_dir = boot_root / "easymanet"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "provision.json"
    dest_path.write_text(provision_json)
    try:
        dest_path.chmod(0o600)
    except OSError:
        # FAT/exFAT boot media may not honor POSIX permissions.
        pass
    results = [
        ("/boot/easymanet/provision.json", True),
        ("Base image must already include EasyMANET first-boot hooks", True),
    ]
    cmdline_result = _fix_usb_boot_root(boot_root)
    if cmdline_result:
        results.append((cmdline_result, True))
    return results


def inject_dry_run_info(_manifest: Manifest, _node_name: str) -> str:
    lines = ["Files to place on the boot FAT partition:"]
    lines.append("  /easymanet/provision.json")
    lines.append("       (generated from fleet.yml for this node)")
    lines.append("")
    lines.append("Base image requirement:")
    lines.append("  Image must already include EasyMANET first-boot hooks:")
    lines.append("  /etc/uci-defaults/99-easymanet")
    lines.append("  and /usr/lib/easymanet/provision.sh via the firmware build.")
    return "\n".join(lines)


def _root_device_partuuid_suffix(dev_path: str) -> Optional[str]:
    match = re.match(r"/dev/(?:mmcblk\d+)p(\d+)$", dev_path)
    if match:
        return f"-{int(match.group(1)):02d}"
    match = re.match(r"/dev/(?:nvme\d+n\d+)p(\d+)$", dev_path)
    if match:
        return f"-{int(match.group(1)):02d}"
    match = re.match(r"/dev/[a-z]+(\d+)$", dev_path)
    if match:
        return f"-{int(match.group(1)):02d}"
    return None


def _fix_usb_boot_root(boot_root: Path) -> Optional[str]:
    cmdline_path = boot_root / "cmdline.txt"
    partuuid_path = boot_root / "partuuid.txt"
    if not cmdline_path.exists() or not partuuid_path.exists():
        return None

    cmdline = cmdline_path.read_text()
    if "root=PARTUUID=" in cmdline:
        return None

    match = ROOT_BLOCK_DEVICE_PATTERN.search(cmdline)
    if not match:
        return None

    root_device = match.group(1)
    partuuid = partuuid_path.read_text().strip()
    if not partuuid:
        return None

    part_suffix = _root_device_partuuid_suffix(root_device)
    if not part_suffix:
        return None

    root_partuuid = f"PARTUUID={partuuid}{part_suffix}"
    backup_path = boot_root / "cmdline.txt.easymanet.bak"
    if not backup_path.exists():
        backup_path.write_text(cmdline)

    updated = cmdline.replace(f"root={root_device}", f"root={root_partuuid}", 1)
    cmdline_path.write_text(updated)
    return f"/boot/cmdline.txt root={root_partuuid}"


def _mount_boot_partition(device: str) -> Tuple[str, bool]:
    existing = _find_boot_mount(device)
    if existing:
        return existing, False

    partition = _find_boot_partition(device)
    if not partition:
        raise InjectError(f"Could not find boot partition on {device}")

    if is_macos():
        return _mount_boot_partition_macos(partition)
    if is_linux():
        return _mount_boot_partition_linux(partition)
    raise InjectError("Unsupported platform for boot-partition mounting")


def _mount_boot_partition_macos(partition: str) -> Tuple[str, bool]:
    result = subprocess.run(
        ["diskutil", "mount", partition],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise InjectError(f"diskutil mount failed for {partition}: {result.stderr.strip()}")

    mount_point = _find_mount_for_partition(partition)
    if not mount_point:
        raise InjectError(f"Mounted {partition} but could not find its mount point")
    return mount_point, True


def _mount_boot_partition_linux(partition: str) -> Tuple[str, bool]:
    mount_point = tempfile.mkdtemp(prefix="easymanet_boot_")
    result = subprocess.run(
        ["mount", partition, mount_point],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        try:
            os.rmdir(mount_point)
        except OSError:
            pass
        raise InjectError(f"mount failed for {partition}: {result.stderr.strip()}")
    return mount_point, True


def _cleanup_mount(device: str, mount_point: str, mounted_here: bool) -> None:
    del device
    if not mounted_here:
        return

    if is_macos():
        result = subprocess.run(
            ["diskutil", "unmount", mount_point],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _debug_note(f"diskutil unmount failed for {mount_point}: {result.stderr.strip()}")
        return

    if is_linux():
        result = subprocess.run(
            ["umount", mount_point],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _debug_note(f"umount failed for {mount_point}: {result.stderr.strip()}")
            return
        try:
            os.rmdir(mount_point)
        except OSError:
            pass


def _macos_partition_index(partition: dict) -> int:
    dev_id = partition.get("DeviceIdentifier", "")
    match = re.search(r"s(\d+)$", dev_id)
    if match:
        return int(match.group(1))
    return 0


def _is_macos_fat_partition(partition: dict) -> bool:
    fs_type = (partition.get("FilesystemType") or "").lower()
    if fs_type in _MACOS_FAT_FILESYSTEMS:
        return True
    content = partition.get("Content") or ""
    if content in _MACOS_FAT_CONTENTS:
        return True
    return "FAT" in content.upper()


def _macos_partitions_for_device(device: str) -> List[dict]:
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist", device],
            timeout=15,
        )
        data = plistlib.loads(output)
    except _BOOT_PARTITION_PARSE_ERRORS as exc:
        _debug_note(f"boot partition lookup failed for {device}: {exc}")
        return []

    partitions: List[dict] = []
    for entry in data.get("AllDisksAndPartitions", []):
        partitions.extend(entry.get("Partitions", []))
    return sorted(partitions, key=_macos_partition_index)


def _find_boot_partition(device: str) -> Optional[str]:
    if is_macos():
        for partition in _macos_partitions_for_device(device):
            if not _is_macos_fat_partition(partition):
                continue
            dev_id = partition.get("DeviceIdentifier", "")
            if dev_id:
                return f"/dev/{dev_id}"
        return None

    if is_linux():
        for suffix in ["1", "p1"]:
            part = f"{device}{suffix}"
            if os.path.exists(part):
                return part
        return None

    return None


def _find_boot_mount(device: str) -> Optional[str]:
    if is_macos():
        for partition in _macos_partitions_for_device(device):
            if not _is_macos_fat_partition(partition):
                continue
            mount_point = partition.get("MountPoint")
            if mount_point:
                return mount_point
            dev_id = partition.get("DeviceIdentifier", "")
            if dev_id:
                found = _find_mount_for_partition(f"/dev/{dev_id}")
                if found:
                    return found
        return None

    if is_linux():
        partition = _find_boot_partition(device)
        if not partition:
            return None
        return _find_mount_for_partition(partition)

    return None


def _find_mount_for_partition(partition: str) -> Optional[str]:
    try:
        output = subprocess.check_output(["mount"], timeout=5).decode()
    except _BOOT_PARTITION_PARSE_ERRORS as exc:
        _debug_note(f"mount output unavailable: {exc}")
        return None

    real_partition = os.path.realpath(partition)
    for line in output.strip().split("\n"):
        parts = line.split()
        if len(parts) < 3:
            continue
        source = parts[0]
        mount_point = parts[2]
        if source == partition or os.path.realpath(source) == real_partition:
            return mount_point
    return None
