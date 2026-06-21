"""Disk detail helpers for the flash workflow."""

from __future__ import annotations

from typing import Any, Callable

from .disks import lookup_device


def disk_details(device: str, lookup_device_fn: Callable[[str], Any] = lookup_device) -> dict[str, Any]:
    disk = lookup_device_fn(device)
    if not disk:
        return {}
    return {
        "device": disk.device,
        "model": disk.model,
        "size_human": disk.size_human,
        "removable": disk.removable,
        "mounted": disk.mounted,
        "warnings": disk.warnings,
        "blocking_warnings": disk.blocking_warnings,
    }
