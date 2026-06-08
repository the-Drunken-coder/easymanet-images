"""Image flashing — write OpenMANET images to SD cards/USB drives.

Handles .img and .img.gz, streaming decompression, verify/sync,
and clean unmount/eject.
"""

import os
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Optional, Tuple

from .disks import (
    assert_flash_allowed,
    get_partition2_wipe_range,
    lookup_device,
    unmount_disk,
    eject_disk,
)
from .platform import is_linux, is_macos


class FlashError(Exception):
    pass


def _tool_path(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FlashError(f"Required tool not found on PATH: {name}")
    return path


def _check_device_safety(device: str, force: bool = False) -> None:
    try:
        assert_flash_allowed(device, force=force)
    except ValueError as e:
        raise FlashError(str(e)) from e


def _unmount_or_raise(device: str) -> None:
    try:
        unmount_disk(device)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        raise FlashError(f"Failed to unmount {device}: {e}") from e


def _gzip_decompressed_bytes(image_path: Path) -> int:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    with image_path.open("rb") as f:
        while not decompressor.eof:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            total += len(decompressor.decompress(chunk))

    if not decompressor.eof:
        raise zlib.error("compressed image ended before the gzip stream completed")
    return total


def _check_gzip_payload(image_path: Path) -> int:
    total = _gzip_decompressed_bytes(image_path)
    if total == 0:
        raise zlib.error("compressed image did not contain a disk image payload")
    return total


def _check_image(image_path: str) -> Tuple[Path, Optional[int]]:
    """Return (image path, decompressed byte count for .img.gz else None)."""
    p = Path(image_path)
    if not p.exists():
        raise FlashError(f"Base image not found: {image_path}")
    suffix = p.suffix.lower()
    if suffix == ".gz":
        if p.stem.lower().endswith(".img"):
            try:
                written_bytes = _check_gzip_payload(p)
            except (OSError, zlib.error) as e:
                raise FlashError(f"Invalid gzip-compressed image {image_path}: {e}") from e
            return p, written_bytes
        raise FlashError(f"Expected .img.gz file, got: {image_path}")
    if suffix == ".img":
        return p, None
    raise FlashError(f"Unsupported image format: {image_path}. Expected .img or .img.gz")


def _reread_partition_table(device: str) -> None:
    if is_linux():
        try:
            _run_reread_partition_command([_tool_path("blockdev"), "--rereadpt", device], device)
        except FlashError:
            _run_reread_partition_command([_tool_path("partprobe"), device], device)
    elif is_macos():
        _run_reread_partition_command([_tool_path("diskutil"), "list", device], device)


def _run_reread_partition_command(cmd: list[str], device: str) -> None:
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise FlashError(
            f"Failed to re-read partition table for {device} with {cmd[0]}{suffix}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise FlashError(
            f"Timed out re-reading partition table for {device} with {cmd[0]}"
        ) from e
    except OSError as e:
        raise FlashError(
            f"Failed to re-read partition table for {device} with {cmd[0]}: {e}"
        ) from e


def flash_image(
    device: str,
    image_path: str,
    dry_run: bool = False,
    force: bool = False,
    skip_overlay_wipe: bool = False,
) -> None:
    image, gzip_written_bytes = _check_image(image_path)
    _check_device_safety(device, force=force)

    if dry_run:
        return

    disk = lookup_device(device)

    if disk:
        mounted_str = ", ".join(disk.mounted) if disk.mounted else "none"
        print(f"Device: {disk.device}")
        print(f"Model: {disk.model}")
        print(f"Size: {disk.size_human}")
        print(f"Mounted: {mounted_str}")
        print(f"Removable: {'yes' if disk.removable else 'no'}")
        print()

    try:
        _unmount_or_raise(device)
        print(f"Writing {image.name} to {device}...")

        if image.suffix == ".gz":
            _write_gz_via_dd(str(image), device)
        else:
            _write_raw_via_dd(str(image), device)

        print("Syncing...")
        os.sync()
        print("Done writing.")

        if not skip_overlay_wipe:
            written_bytes = gzip_written_bytes if gzip_written_bytes is not None else image.stat().st_size
            # macOS (and sometimes Linux) auto-mounts partitions after dd; unmount before raw wipe.
            _unmount_or_raise(device)
            _clear_stale_overlay(device, written_bytes)

    except subprocess.CalledProcessError as e:
        raise FlashError(f"Flash failed: {e}") from e
    except FlashError:
        raise
    except (OSError, subprocess.SubprocessError) as e:
        raise FlashError(f"Flash failed: {e}") from e


def _write_gz_via_dd(image_path: str, device: str) -> None:
    gzip_cmd = [_tool_path("gzip"), "-dc", image_path]
    output_device = _dd_device_path(device)
    dd_cmd = [
        _tool_path("dd"),
        f"of={output_device}",
        _write_block_size_arg(),
        "status=progress",
    ]
    gzip_proc = subprocess.Popen(
        gzip_cmd,
        stdout=subprocess.PIPE,
    )
    assert gzip_proc.stdout is not None
    dd_proc = subprocess.Popen(
        dd_cmd,
        stdin=gzip_proc.stdout,
    )
    gzip_proc.stdout.close()

    dd_return = dd_proc.wait()
    gzip_return = gzip_proc.wait()

    # OpenWrt/OpenMANET sysupgrade metadata after the gzip stream can yield exit 2
    # ("trailing garbage ignored"); payload integrity is validated by _check_gzip_payload.
    if gzip_return not in (0, 2):
        raise subprocess.CalledProcessError(gzip_return, gzip_cmd)
    if dd_return != 0:
        raise subprocess.CalledProcessError(dd_return, dd_cmd)


_OVERLAY_WIPE_SECTOR_BYTES = 512
_OVERLAY_WIPE_BULK_BYTES = 16 * 1024 * 1024


def _dd_device_path(device: str) -> str:
    if is_macos() and device.startswith("/dev/disk"):
        return device.replace("/dev/disk", "/dev/rdisk", 1)
    return device


def _write_block_size_arg() -> str:
    return "bs=16m" if is_macos() else "bs=16M"


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _run_zero_dd(device: str, block_bytes: int, seek_blocks: int, count_blocks: int) -> None:
    if count_blocks <= 0:
        return

    # macOS may auto-mount partitions between wipe phases (especially after long writes).
    _unmount_or_raise(device)
    output_device = _dd_device_path(device)
    subprocess.run(
        [
            _tool_path("dd"),
            "if=/dev/zero",
            f"of={output_device}",
            f"bs={block_bytes}",
            f"seek={seek_blocks}",
            f"count={count_blocks}",
            "status=progress",
        ],
        check=True,
    )


def _clear_stale_overlay(device: str, written_bytes: int) -> None:
    _reread_partition_table(device)
    wipe_range = get_partition2_wipe_range(device)
    if not wipe_range:
        raise FlashError(
            f"Image was written to {device}, but the stale OpenWrt overlay area "
            f"could not be wiped (partition layout unknown).\n"
            f"Re-flash with a known-good image, manually zero partition 2, or re-run with "
            f"--skip-overlay-wipe only if you accept stale config on the drive."
        )

    tail_start, wipe_bytes = wipe_range
    start_bytes = max(tail_start, written_bytes)
    wipe_bytes = wipe_bytes - (start_bytes - tail_start)
    if wipe_bytes <= 0:
        print("Skipping stale overlay wipe; image covers the wipe region.")
        return

    sector_bytes = _OVERLAY_WIPE_SECTOR_BYTES
    bulk_bytes = _OVERLAY_WIPE_BULK_BYTES
    seek_sectors = _ceil_div(start_bytes, sector_bytes)
    aligned_start = seek_sectors * sector_bytes
    span_bytes = wipe_bytes + (aligned_start - start_bytes)
    count_sectors = max(1, _ceil_div(span_bytes, sector_bytes))
    total_mib = count_sectors * sector_bytes / (1024 * 1024)
    print(
        f"Clearing stale OpenWrt overlay area ({total_mib:.1f} MiB at offset {start_bytes} bytes)..."
    )

    total_bytes = count_sectors * sector_bytes
    cursor = aligned_start
    if cursor % bulk_bytes:
        prefix_bytes = min(total_bytes, bulk_bytes - (cursor % bulk_bytes))
        prefix_sectors = _ceil_div(prefix_bytes, sector_bytes)
        _run_zero_dd(device, sector_bytes, cursor // sector_bytes, prefix_sectors)
        prefix_written = prefix_sectors * sector_bytes
        cursor += prefix_written
        total_bytes -= prefix_written

    bulk_blocks = total_bytes // bulk_bytes
    _run_zero_dd(device, bulk_bytes, cursor // bulk_bytes, bulk_blocks)
    bulk_written = bulk_blocks * bulk_bytes
    cursor += bulk_written
    total_bytes -= bulk_written

    tail_sectors = total_bytes // sector_bytes
    _run_zero_dd(device, sector_bytes, cursor // sector_bytes, tail_sectors)


def _write_raw_via_dd(image_path: str, device: str) -> None:
    output_device = _dd_device_path(device)
    subprocess.run(
        [
            _tool_path("dd"),
            f"if={image_path}",
            f"of={output_device}",
            _write_block_size_arg(),
            "status=progress",
        ],
        check=True,
    )


def finish_flash(device: str, eject: bool = True) -> bool:
    os.sync()
    if eject:
        print(f"Ejecting {device}...")
        try:
            eject_disk(device)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            print(f"Warning: {e}")
            print(
                f"Image written and payload staged, but eject failed. "
                f"Run sync and eject {device} manually before removing it."
            )
            return False
    print("Safe to remove.")
    return True
