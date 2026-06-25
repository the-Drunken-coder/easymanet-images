"""Shared disk types and helpers."""

import plistlib
import subprocess
import sys
from typing import List, Optional

_SUBPROCESS_ERRORS = (
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
    FileNotFoundError,
)
DISK_PARSE_ERRORS = (
    *_SUBPROCESS_ERRORS,
    OSError,
    ValueError,
    UnicodeDecodeError,
    plistlib.InvalidFileException,
)

DISK_WARN_THRESHOLD_GB = 128
DISK_SUSPICIOUS_SIZE_GB = 256

SYS_MOUNT_POINTS = frozenset({"/", "/boot", "/boot/efi", "/home", "/var", "/usr"})

OVERLAY_WIPE_BLOCK_MIB = 16
OVERLAY_WIPE_BLOCKS = 288


def debug_note(message: str) -> None:
    print(f"easymanet: {message}", file=sys.stderr)


class DiskInfo:
    def __init__(
        self,
        device: str,
        size_bytes: int = 0,
        model: str = "",
        removable: bool = False,
        mounted: Optional[List[str]] = None,
        is_system: bool = False,
        not_in_default_list: bool = False,
        virtual: bool = False,
    ):
        self.device = device
        self.size_bytes = size_bytes
        self.model = model
        self.removable = removable
        self.mounted = mounted or []
        self.is_system = is_system
        self.not_in_default_list = not_in_default_list
        self.virtual = virtual

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

    @property
    def size_human(self) -> str:
        gb = self.size_gb
        if gb < 1:
            mb = self.size_bytes / (1024 ** 2)
            return f"{mb:.1f} MB"
        return f"{gb:.1f} GB"

    @property
    def blocking_warnings(self) -> List[str]:
        w: List[str] = []
        if self.virtual:
            w.append(
                "WARNING: Virtual disk image — use --force only for test fixtures"
            )
        if self.is_system:
            w.append(
                "WARNING: This appears to be a system disk — use --force to override"
            )
        elif not self.removable and self.size_gb > DISK_WARN_THRESHOLD_GB:
            w.append(
                "WARNING: Large fixed disk — use --force to proceed"
            )
        elif self.size_gb > DISK_SUSPICIOUS_SIZE_GB:
            w.append(
                "WARNING: Suspiciously large device — use --force to proceed"
            )
        if self.not_in_default_list:
            w.append(
                "WARNING: Device not in default disk list — use --force to proceed"
            )
        return w

    @property
    def warnings(self) -> List[str]:
        return self.blocking_warnings
