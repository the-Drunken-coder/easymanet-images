"""Pure dd/path helpers for block-device image writing."""

from __future__ import annotations

import re
import subprocess
from typing import Optional

DD_PROGRESS_PATTERN = re.compile(r"(?P<bytes>\d+)\s+bytes")


def command_error_message(error: subprocess.CalledProcessError) -> str:
    stderr = error.stderr
    stdout = error.stdout
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors="replace")
    detail = str(stderr or stdout or "").strip()
    return f"{error}: {detail}" if detail else str(error)


def dd_device_path(device: str, *, macos: bool) -> str:
    if macos and device.startswith("/dev/disk"):
        return device.replace("/dev/disk", "/dev/rdisk", 1)
    return device


def stream_dd_device_path(device: str, *, macos: bool) -> str:
    if macos:
        return device.replace("/dev/rdisk", "/dev/disk", 1)
    return device


def stream_dd_block_args(*, macos: bool) -> list[str]:
    if macos:
        return ["bs=1m"]
    return [write_block_size_arg(macos=macos)]


def write_block_size_arg(*, macos: bool) -> str:
    return "bs=16m" if macos else "bs=16M"


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def dd_progress_bytes(text: str) -> Optional[int]:
    match = DD_PROGRESS_PATTERN.search(text)
    if not match:
        return None
    return int(match.group("bytes"))
