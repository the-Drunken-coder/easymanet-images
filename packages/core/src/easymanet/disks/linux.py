"""Linux disk discovery and safety helpers."""

import glob
import json
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

from ._common import DISK_PARSE_ERRORS, DiskInfo, debug_note

_FINDMNT_PARENT_RESOLUTION_MAX_DEPTH = 8
_LINUX_DEFAULT_LOGICAL_SECTOR_BYTES = 512


def _disks_module():
    return sys.modules[__package__]


def _parse_lsblk_size(size_str: str) -> int:
    try:
        return int(size_str)
    except ValueError:
        suffixes = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
        try:
            num = float(size_str[:-1])
            suffix = size_str[-1].upper()
            return int(num * suffixes.get(suffix, 1))
        except (ValueError, IndexError):
            return 0


def _get_linux_mounts(dev: dict) -> List[str]:
    mounts = []
    children = dev.get("children", [])
    for child in children:
        mp = child.get("mountpoint")
        if mp:
            mounts.append(mp)
    return mounts


def _linux_disk_from_lsblk(dev: dict) -> DiskInfo:
    dev_name = dev.get("name", "")
    dev_path = f"/dev/{dev_name}"
    model = (dev.get("model") or "").strip() or dev_name
    removable = dev.get("rm", "0") == "1"
    tran = (dev.get("tran") or "").lower()
    if tran in ("usb", "mmc"):
        removable = True
    size_bytes = _parse_lsblk_size(dev.get("size", "0"))
    mounted = _get_linux_mounts(dev)
    is_system = _check_linux_system_disk(dev_path, mounted)
    return DiskInfo(
        device=dev_path,
        size_bytes=size_bytes,
        model=model,
        removable=removable,
        mounted=mounted,
        is_system=is_system,
    )


def _linux_should_list_default(dev: dict) -> bool:
    if dev.get("type") != "disk":
        return False
    if dev.get("rm", "0") == "1":
        return True
    tran = (dev.get("tran") or "").lower()
    return tran in ("usb", "mmc")


def _linux_lsblk_data(device: Optional[str] = None) -> Optional[dict]:
    cmd = [
        "lsblk",
        "-J",
        "-o",
        "NAME,SIZE,TYPE,MOUNTPOINT,MODEL,RM,ROTA,TRAN",
    ]
    if device:
        cmd.extend(["-n", device])
    try:
        output = subprocess.check_output(cmd, timeout=10).decode()
        return json.loads(output)
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ):
        return None


def list_disks_linux(include_all: bool = False) -> List[DiskInfo]:
    data = _linux_lsblk_data()
    if not data:
        return []

    disks: List[DiskInfo] = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        if not include_all and not _linux_should_list_default(dev):
            continue
        disks.append(_linux_disk_from_lsblk(dev))

    return sorted(disks, key=lambda d: d.size_bytes, reverse=True)


def lookup_device_linux(device: str) -> Optional[DiskInfo]:
    data = _linux_lsblk_data(device)
    if not data:
        return None
    blockdevs = data.get("blockdevices", [])
    if not blockdevs:
        return None
    dev = blockdevs[0]
    if dev.get("type") != "disk":
        return None
    return _linux_disk_from_lsblk(dev)


def _findmnt_source(mount_point: str) -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", mount_point],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        return output.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _linux_base_block_device(source: str) -> Optional[str]:
    if not source.startswith("/dev/"):
        return None
    match = re.match(r"^(?P<base>/dev/(?:mmcblk\d+|nvme\d+n\d+))p\d+$", source)
    if match:
        return match.group("base")
    match = re.match(r"^(?P<base>/dev/[a-z]+)\d+$", source)
    if match:
        return match.group("base")
    if re.match(r"^/dev/(?:mmcblk\d+|nvme\d+n\d+|[a-z]+)$", source):
        return source
    return None


def _linux_lsblk_pkname(device: str) -> Optional[str]:
    try:
        output = subprocess.check_output(
            ["lsblk", "-no", "PKNAME", device],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
        name = output.strip()
        if name:
            return f"/dev/{name}"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


def _linux_resolve_findmnt_source(source: str) -> Optional[str]:
    disks_mod = _disks_module()
    device_path: Optional[str] = None
    if source.startswith("UUID="):
        uuid = source.split("=", 1)[1]
        by_uuid = f"/dev/disk/by-uuid/{uuid}"
        if disks_mod.os.path.exists(by_uuid):
            device_path = disks_mod.os.path.realpath(by_uuid)
    elif source.startswith("PARTUUID="):
        partuuid = source.split("=", 1)[1]
        by_partuuid = f"/dev/disk/by-partuuid/{partuuid}"
        if disks_mod.os.path.exists(by_partuuid):
            device_path = disks_mod.os.path.realpath(by_partuuid)
    elif source.startswith("/dev/"):
        device_path = disks_mod.os.path.realpath(source)
    else:
        return None

    if not device_path:
        return None

    current = device_path
    for _ in range(_FINDMNT_PARENT_RESOLUTION_MAX_DEPTH):
        base = disks_mod._linux_base_block_device(current)
        if base:
            return base
        parent = disks_mod._linux_lsblk_pkname(current)
        if not parent or parent == current:
            break
        current = parent
    return None


def _linux_partitions_for_device(device: str) -> List[str]:
    partitions = set()
    for pattern in (f"{device}[0-9]*", f"{device}p[0-9]*"):
        partitions.update(_disks_module().glob.glob(pattern))
    partitions.discard(device)
    return sorted(partitions)


def _linux_root_block_devices() -> set:
    disks_mod = _disks_module()
    related: set = set()
    for mount_point in sorted(disks_mod.SYS_MOUNT_POINTS):
        source = disks_mod._findmnt_source(mount_point)
        if not source:
            continue
        base = disks_mod._linux_resolve_findmnt_source(source)
        if not base:
            continue
        related.add(base)
        related.update(disks_mod._linux_partitions_for_device(base))
        if source.startswith("/dev/"):
            resolved = disks_mod.os.path.realpath(source)
            if resolved.startswith("/dev/"):
                related.add(resolved)
    return related


def _check_linux_system_disk(dev_path: str, mounts: List[str]) -> bool:
    disks_mod = _disks_module()
    if any(mp in disks_mod.SYS_MOUNT_POINTS for mp in mounts):
        return True

    root_related = disks_mod._linux_root_block_devices()
    if not root_related:
        debug_note(
            "could not resolve Linux root block devices; treating candidate as a system disk"
        )
        return True

    if dev_path in root_related:
        return True
    if set(disks_mod._linux_partitions_for_device(dev_path)) & root_related:
        return True
    for entry in root_related:
        if disks_mod._linux_base_block_device(entry) == dev_path:
            return True
    return False


def _linux_partition2_wipe_range(device: str, max_wipe: int) -> Optional[Tuple[int, int]]:
    cmd = ["lsblk", "-J", "-b", "-o", "NAME,START,SIZE,TYPE,LOG-SEC", "-n", device]
    try:
        output = subprocess.check_output(cmd, timeout=10).decode()
        data = json.loads(output)
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ):
        return None

    blockdevs = data.get("blockdevices", [])
    if not blockdevs:
        return None
    children = blockdevs[0].get("children", [])
    parts = [c for c in children if c.get("type") == "part"]
    if len(parts) < 2:
        return None
    part2 = sorted(parts, key=lambda p: int(p.get("start", 0) or 0))[1]
    start = int(part2.get("start", 0) or 0)
    size = int(part2.get("size", 0) or 0)
    if start <= 0 or size <= 0:
        return None
    sector_bytes = _linux_logical_sector_bytes(part2, blockdevs[0])
    start_bytes = start * sector_bytes
    wipe_bytes = min(size, max_wipe)
    tail_start = start_bytes + size - wipe_bytes
    return (tail_start, wipe_bytes)


def _linux_logical_sector_bytes(*devices: dict) -> int:
    for dev in devices:
        for key in ("log-sec", "log_sec", "LOG-SEC"):
            try:
                value = int(dev.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return _LINUX_DEFAULT_LOGICAL_SECTOR_BYTES


def unmount_disk_linux(device: str) -> None:
    targets = _linux_partitions_for_device(device) or [device]
    for target in targets:
        result = subprocess.run(
            ["umount", target],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            continue
        detail = (result.stderr or result.stdout or "").strip()
        if "not mounted" in detail.lower():
            continue
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Failed to unmount {target}{suffix}")
