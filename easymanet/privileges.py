"""Privilege detection for disk writes.

On macOS, raw disk access typically requires sudo. On Linux, members of
the disk group may have write access to removable block devices without
running as root.
"""

import os

from .platform import is_linux


class PrivilegeError(Exception):
    pass


def is_running_as_root() -> bool:
    return os.geteuid() == 0


def can_write_block_device(device: str) -> bool:
    return os.access(device, os.W_OK)


def check_privileges(device: str) -> None:
    if is_running_as_root():
        return
    if is_linux() and can_write_block_device(device):
        return

    raise PrivilegeError(
        "Write access to the target block device is required.\n"
        "Run with sudo, for example:\n"
        "  sudo easymanet flash --config fleet.yml --node manet01 "
        "--device /dev/sdX --yes"
    )
