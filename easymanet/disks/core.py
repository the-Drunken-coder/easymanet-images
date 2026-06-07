"""Cross-platform disk listing and flash safety."""

import os
import stat
import subprocess
import sys
from typing import List, Optional, Tuple

from ._common import (
    OVERLAY_WIPE_BLOCK_MIB,
    OVERLAY_WIPE_BLOCKS,
    DiskInfo,
)


def _disks_module():
    return sys.modules[__package__]


def _is_block_device(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False


def list_disks(include_all: bool = False) -> List[DiskInfo]:
    disks_mod = _disks_module()
    if disks_mod.is_macos():
        disks = disks_mod.list_disks_macos(include_all=include_all)
    elif disks_mod.is_linux():
        disks = disks_mod.list_disks_linux(include_all=include_all)
    else:
        return []
    return sorted(disks, key=lambda d: d.size_bytes, reverse=True)


def lookup_device(
    device: str,
    default_disks: Optional[List[DiskInfo]] = None,
) -> Optional[DiskInfo]:
    disks_mod = _disks_module()
    if disks_mod.is_macos():
        disk = disks_mod.lookup_device_macos(device)
    elif disks_mod.is_linux():
        disk = disks_mod.lookup_device_linux(device)
    else:
        disk = None

    if disk is None:
        return None

    disks = (
        default_disks
        if default_disks is not None
        else disks_mod.list_disks(include_all=False)
    )
    if not any(d.device == device for d in disks):
        disk.not_in_default_list = True
    return disk


def find_disk(device: str) -> Optional[DiskInfo]:
    disks = list_disks()
    for disk in disks:
        if disk.device == device:
            return disk
    return lookup_device(device, default_disks=disks)


def assert_flash_allowed(device: str, force: bool = False) -> DiskInfo:
    if not _disks_module()._is_block_device(device):
        raise ValueError(
            f"Device {device} does not exist or is not a block device."
        )

    disk = _disks_module().lookup_device(device)
    if disk is None:
        raise ValueError(
            f"Could not read disk information for {device}."
        )

    blocking = disk.blocking_warnings
    if blocking and not force:
        lines = "\n".join(f"  {w}" for w in blocking)
        raise ValueError(
            f"Refusing to flash {device}:\n{lines}\n"
            f"  Model: {disk.model}\n"
            f"  Size: {disk.size_human}\n"
            f"  Mounted: {', '.join(disk.mounted) if disk.mounted else 'none'}\n"
            f"Use --force to override."
        )
    return disk


def unmount_disk(device: str) -> None:
    disks_mod = _disks_module()
    if disks_mod.is_macos():
        disks_mod.unmount_disk_macos(device)
    elif disks_mod.is_linux():
        disks_mod.unmount_disk_linux(device)


def get_partition2_wipe_range(device: str) -> Optional[Tuple[int, int]]:
    """Return (start_byte_offset, wipe_bytes) for partition 2 stale-overlay tail wipe."""
    max_wipe = OVERLAY_WIPE_BLOCK_MIB * OVERLAY_WIPE_BLOCKS * 1024 * 1024
    disks_mod = _disks_module()

    if disks_mod.is_linux():
        return disks_mod._linux_partition2_wipe_range(device, max_wipe)
    if disks_mod.is_macos():
        return disks_mod._macos_partition2_wipe_range(device, max_wipe)
    return None


def eject_disk(device: str) -> None:
    disks_mod = _disks_module()
    if disks_mod.is_macos():
        result = subprocess.run(
            ["diskutil", "eject", device],
            capture_output=True,
            text=True,
            timeout=30,
        )
    elif disks_mod.is_linux():
        result = subprocess.run(
            ["eject", device],
            capture_output=True,
            text=True,
            timeout=30,
        )
    else:
        return
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Failed to eject {device}{suffix}")
