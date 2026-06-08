"""Disk detection and listing for macOS and Linux."""

import glob
import os
import subprocess

from ..platform import is_linux, is_macos
from ._common import (
    DISK_PARSE_ERRORS,
    DISK_SUSPICIOUS_SIZE_GB,
    DISK_WARN_THRESHOLD_GB,
    OVERLAY_WIPE_BLOCK_MIB,
    OVERLAY_WIPE_BLOCKS,
    SYS_MOUNT_POINTS,
    DiskInfo,
    debug_note,
)
from .core import (
    assert_flash_allowed,
    eject_disk,
    find_disk,
    get_partition2_wipe_range,
    list_disks,
    lookup_device,
    unmount_disk,
)
from .linux import (
    _check_linux_system_disk,
    _findmnt_source,
    _linux_base_block_device,
    _linux_disk_from_lsblk,
    _linux_lsblk_pkname,
    _linux_partition2_wipe_range,
    _linux_partitions_for_device,
    _linux_resolve_findmnt_source,
    _linux_root_block_devices,
    _linux_should_list_default,
    list_disks_linux,
    lookup_device_linux,
    unmount_disk_linux,
)
from .macos import (
    _macos_partition2_wipe_range,
    get_macos_partitions,
    list_disks_macos,
    lookup_device_macos,
    unmount_disk_macos,
)
from .core import _is_block_device

__all__ = [
    "DISK_PARSE_ERRORS",
    "DISK_SUSPICIOUS_SIZE_GB",
    "DISK_WARN_THRESHOLD_GB",
    "DiskInfo",
    "OVERLAY_WIPE_BLOCK_MIB",
    "OVERLAY_WIPE_BLOCKS",
    "SYS_MOUNT_POINTS",
    "assert_flash_allowed",
    "debug_note",
    "eject_disk",
    "find_disk",
    "get_macos_partitions",
    "get_partition2_wipe_range",
    "glob",
    "is_linux",
    "is_macos",
    "list_disks",
    "lookup_device",
    "os",
    "subprocess",
    "unmount_disk",
]
