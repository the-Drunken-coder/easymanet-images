"""macOS disk discovery and safety helpers."""

import os
import plistlib
import re
import subprocess
from typing import List, Optional, Tuple

from ._common import DISK_PARSE_ERRORS, DiskInfo, debug_note


def list_disks_macos(include_all: bool = False) -> List[DiskInfo]:
    if include_all:
        return _list_disks_macos_all()
    return _list_disks_macos_external()


def _list_disks_macos_external() -> List[DiskInfo]:
    disks: List[DiskInfo] = []
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist", "external"],
            timeout=15,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return disks

    try:
        data = plistlib.loads(output.encode())
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil external plist parse failed: {exc}")
        return disks

    all_disks_entries = data.get("AllDisksAndPartitions", [])
    all_mounts = _get_macos_all_mounts()

    for entry in all_disks_entries:
        disk = _diskinfo_from_macos_entry(entry, all_mounts)
        if disk and disk.virtual:
            continue
        if disk:
            disks.append(disk)

    return disks


def _list_disks_macos_all() -> List[DiskInfo]:
    disks: List[DiskInfo] = []
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist"],
            timeout=15,
        ).decode()
        data = plistlib.loads(output.encode())
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil list plist parse failed: {exc}")
        return disks

    all_mounts = _get_macos_all_mounts()
    seen = set()
    for entry in data.get("WholeDisks", []):
        dev_path = f"/dev/{entry}"
        if dev_path in seen:
            continue
        seen.add(dev_path)
        info_text = _get_diskutil_info_text(dev_path)
        if not info_text:
            continue
        size_bytes = _parse_macos_size(_parse_info_field(info_text, "Disk Size"))
        model = _parse_info_field(info_text, "Device / Media Name") or entry
        removable = _is_removable_from_info(info_text)
        virtual = _is_virtual_from_info(info_text)
        mounted = _find_mounts_for_disk(entry, all_mounts)
        is_system = _check_macos_system(mounted)
        disks.append(
            DiskInfo(
                device=dev_path,
                size_bytes=size_bytes,
                model=model,
                removable=removable,
                mounted=mounted,
                is_system=is_system,
                virtual=virtual,
            )
        )

    return sorted(disks, key=lambda d: d.size_bytes, reverse=True)


def _diskinfo_from_macos_entry(entry: dict, all_mounts: dict) -> Optional[DiskInfo]:
    dev_id = entry.get("DeviceIdentifier", "")
    if not dev_id:
        return None
    dev_path = f"/dev/{dev_id}"
    size_bytes = entry.get("Size", 0)
    mounted = _find_mounts_for_disk(dev_id, all_mounts)
    info_text = _get_diskutil_info_text(dev_path)
    model = _parse_info_field(info_text, "Device / Media Name") or dev_id
    removable = _is_removable_from_info(info_text)
    virtual = _is_virtual_from_info(info_text)
    is_system = _check_macos_system(mounted)
    return DiskInfo(
        device=dev_path,
        size_bytes=size_bytes,
        model=model,
        removable=removable,
        mounted=mounted,
        is_system=is_system,
        virtual=virtual,
    )


_MACOS_WHOLE_DISK_RE = re.compile(r"^/dev/r?disk\d+$")


def _canonical_macos_whole_disk(device: str) -> Optional[str]:
    if not _MACOS_WHOLE_DISK_RE.match(device):
        return None
    return device.replace("/dev/rdisk", "/dev/disk", 1)


def lookup_device_macos(device: str) -> Optional[DiskInfo]:
    dev_path = _canonical_macos_whole_disk(device)
    if dev_path is None:
        return None
    if not os.path.exists(device) and not os.path.exists(dev_path):
        return None
    info_text = _get_diskutil_info_text(dev_path)
    if not info_text:
        return None
    dev_id = dev_path.replace("/dev/", "")
    all_mounts = _get_macos_all_mounts()
    size_bytes = _parse_macos_size(_parse_info_field(info_text, "Disk Size"))
    model = _parse_info_field(info_text, "Device / Media Name") or dev_id
    removable = _is_removable_from_info(info_text)
    virtual = _is_virtual_from_info(info_text)
    mounted = _find_mounts_for_disk(dev_id, all_mounts)
    is_system = _check_macos_system(mounted)
    return DiskInfo(
        device=dev_path,
        size_bytes=size_bytes,
        model=model,
        removable=removable,
        mounted=mounted,
        is_system=is_system,
        virtual=virtual,
    )


def _parse_macos_size(size_str: str) -> int:
    if not size_str:
        return 0
    m = re.match(r"([\d.]+)\s*([KMGT]?B)", size_str.strip(), re.I)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    return int(num * multipliers.get(unit, 1))


def _get_macos_all_mounts() -> dict:
    mounts = {}
    try:
        output = subprocess.check_output(["diskutil", "list", "-plist"], timeout=15)
        data = plistlib.loads(output)
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil mount plist unavailable: {exc}")
        return mounts

    for entry in data.get("AllDisksAndPartitions", []):
        dev_id = entry.get("DeviceIdentifier", "")
        if not dev_id:
            continue
        for partition in entry.get("Partitions", []):
            mount_point = partition.get("MountPoint")
            part_id = partition.get("DeviceIdentifier", "")
            if mount_point is None and part_id:
                mount_point = _get_macos_mount_point(f"/dev/{part_id}")
            if mount_point:
                mounts.setdefault(dev_id, []).append(mount_point)
    return mounts


def _get_macos_mount_point(dev_path: str) -> str:
    try:
        output = subprocess.check_output(
            ["diskutil", "info", "-plist", dev_path],
            timeout=15,
        )
        data = plistlib.loads(output)
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil mount point lookup failed for {dev_path}: {exc}")
        return ""
    return data.get("MountPoint") or ""


def _find_mounts_for_disk(dev_id: str, all_mounts: dict) -> List[str]:
    return all_mounts.get(dev_id, [])


def _get_diskutil_info_text(dev_path: str) -> str:
    try:
        return subprocess.check_output(
            ["diskutil", "info", dev_path],
            timeout=120,
        ).decode()
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil info failed for {dev_path}: {exc}")
        return ""


def _parse_info_field(info_text: str, field: str) -> str:
    pattern = re.escape(field) + r":\s+(.+)"
    m = re.search(pattern, info_text)
    if m:
        return m.group(1).strip()
    return ""


def _is_removable_from_info(info_text: str) -> bool:
    removable = _parse_info_field(info_text, "Removable Media")
    location = _parse_info_field(info_text, "Device Location")
    if removable.lower() in ("yes", "removable", "true"):
        return True
    if location.lower() == "external":
        return True
    return False


def _is_virtual_from_info(info_text: str) -> bool:
    virtual = _parse_info_field(info_text, "Virtual")
    protocol = _parse_info_field(info_text, "Protocol")
    model = _parse_info_field(info_text, "Device / Media Name")
    if virtual.lower() in ("yes", "true"):
        return True
    if protocol.lower() == "disk image":
        return True
    return model.lower() == "disk image"


def _check_macos_system(mounts: List[str]) -> bool:
    sys_mounts = {"/", "/System/Volumes/Data"}
    for mp in mounts:
        if mp in sys_mounts:
            return True
    return False


def _macos_partition_index(partition: dict) -> int:
    dev_id = partition.get("DeviceIdentifier", "")
    match = re.search(r"s(\d+)$", dev_id)
    if match:
        return int(match.group(1))
    return 0


def _macos_partition_byte_offset(partition: dict) -> Optional[int]:
    """Byte offset of a partition on its parent disk."""
    offset = partition.get("PartitionOffset")
    if offset is not None:
        off = int(offset)
        if off > 0:
            return off

    dev_id = partition.get("DeviceIdentifier", "")
    if not dev_id:
        return None

    try:
        output = subprocess.check_output(
            ["diskutil", "info", "-plist", f"/dev/{dev_id}"],
            timeout=15,
        )
        info = plistlib.loads(output)
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil info failed for /dev/{dev_id}: {exc}")
        return None

    for key in ("PartitionMapPartitionOffset", "PartitionOffset"):
        value = info.get(key)
        if value is not None:
            off = int(value)
            if off > 0:
                return off
    return None


def get_macos_partitions(device: str) -> List[str]:
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist", device],
            timeout=15,
        ).decode()
        data = plistlib.loads(output.encode())
        partitions = []
        for a in data.get("AllDisksAndPartitions", []):
            for p in a.get("Partitions", []):
                pid = p.get("DeviceIdentifier", "")
                if pid:
                    partitions.append(f"/dev/{pid}")
        return partitions
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"diskutil partition list failed for {device}: {exc}")
        return []


def _macos_partition2_wipe_range(device: str, max_wipe: int) -> Optional[Tuple[int, int]]:
    try:
        output = subprocess.check_output(
            ["diskutil", "list", "-plist", device],
            timeout=15,
        ).decode()
        data = plistlib.loads(output.encode())
    except DISK_PARSE_ERRORS as exc:
        debug_note(f"partition 2 wipe range lookup failed for {device}: {exc}")
        return None

    partitions = []
    for entry in data.get("AllDisksAndPartitions", []):
        for p in entry.get("Partitions", []):
            partitions.append(p)

    if len(partitions) < 2:
        return None

    part2 = sorted(partitions, key=_macos_partition_index)[1]
    start = _macos_partition_byte_offset(part2)
    size = int(part2.get("Size", 0) or 0)
    if start is None or start <= 0 or size <= 0:
        return None
    wipe_bytes = min(size, max_wipe)
    tail_start = start + size - wipe_bytes
    return (tail_start, wipe_bytes)


def unmount_disk_macos(device: str) -> None:
    result = subprocess.run(
        ["diskutil", "unmountDisk", device],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        force_result = subprocess.run(
            ["diskutil", "unmountDisk", "force", device],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if force_result.returncode != 0:
            detail = (force_result.stderr or force_result.stdout or "").strip()
            if not detail:
                detail = (result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"Failed to unmount {device}{suffix}")
