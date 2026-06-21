"""Image flashing — write OpenMANET images to SD cards/USB drives.

Handles .img and .img.gz, streaming decompression, verify/sync,
and clean unmount/eject.
"""

import os
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from ._image_dd import (
    ceil_div as _ceil_div_value,
    command_error_message as _command_error_message,
    dd_device_path as _platform_dd_device_path,
    dd_progress_bytes,
    stream_dd_block_args as _platform_stream_dd_block_args,
    stream_dd_device_path as _platform_stream_dd_device_path,
    write_block_size_arg as _platform_write_block_size_arg,
)
from ._image_validation import (
    check_gzip_payload as _check_gzip_payload,
)
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


FlashEventCallback = Callable[[dict[str, Any]], None]


def _emit_event(
    emit: FlashEventCallback | None,
    event_type: str,
    message: str,
    **data: Any,
) -> None:
    if emit:
        emit({"type": event_type, "message": message, **data})


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
    emit: FlashEventCallback | None = None,
) -> None:
    image, gzip_written_bytes = _check_image(image_path)
    _check_device_safety(device, force=force)

    if dry_run:
        return

    disk = lookup_device(device)

    if disk:
        _emit_event(
            emit,
            "disk_details",
            f"Device: {disk.device}",
            device=disk.device,
            model=disk.model,
            size_human=disk.size_human,
            mounted=disk.mounted,
            removable=disk.removable,
        )

    try:
        _unmount_or_raise(device)
        _emit_event(
            emit,
            "write_started",
            f"Writing {image.name} to {device}...",
            image=image.name,
            device=device,
        )

        if image.suffix == ".gz":
            _write_gz_via_dd(str(image), device, emit=emit)
        else:
            _write_raw_via_dd(str(image), device, emit=emit)

        _emit_event(emit, "sync_started", "Syncing...")
        os.sync()
        _emit_event(emit, "write_completed", "Done writing.")

        if not skip_overlay_wipe:
            written_bytes = gzip_written_bytes if gzip_written_bytes is not None else image.stat().st_size
            # macOS (and sometimes Linux) auto-mounts partitions after dd; unmount before raw wipe.
            _unmount_or_raise(device)
            _clear_stale_overlay(device, written_bytes, emit=emit)

    except subprocess.CalledProcessError as e:
        raise FlashError(f"Flash failed: {_command_error_message(e)}") from e
    except FlashError:
        raise
    except (OSError, subprocess.SubprocessError) as e:
        raise FlashError(f"Flash failed: {e}") from e


def _write_gz_via_dd(
    image_path: str,
    device: str,
    *,
    emit: FlashEventCallback | None = None,
) -> None:
    gzip_cmd = [_tool_path("gzip"), "-dc", image_path]
    output_device = _stream_dd_device_path(device)
    dd_cmd = [
        _tool_path("dd"),
        f"of={output_device}",
        *_stream_dd_block_args(),
        "status=progress",
    ]
    gzip_proc = subprocess.Popen(
        gzip_cmd,
        stdout=subprocess.PIPE,
    )
    assert gzip_proc.stdout is not None
    dd_kwargs: dict[str, Any] = {"stdin": gzip_proc.stdout, "stderr": subprocess.PIPE, "text": True}
    dd_proc = subprocess.Popen(dd_cmd, **dd_kwargs)
    gzip_proc.stdout.close()

    if emit is None:
        _dd_stdout, dd_stderr = dd_proc.communicate()
    else:
        dd_stderr = _drain_dd_progress(dd_proc, emit)
    dd_return = dd_proc.wait()
    gzip_return = gzip_proc.wait()

    if dd_return != 0:
        raise subprocess.CalledProcessError(dd_return, dd_cmd, stderr=dd_stderr)

    # OpenWrt/OpenMANET sysupgrade metadata after the gzip stream can yield exit 2
    # ("trailing garbage ignored"); payload integrity is validated by _check_gzip_payload.
    if gzip_return not in (0, 2):
        raise subprocess.CalledProcessError(gzip_return, gzip_cmd)


_OVERLAY_WIPE_SECTOR_BYTES = 512
_OVERLAY_WIPE_BULK_BYTES = 16 * 1024 * 1024


def _dd_device_path(device: str) -> str:
    return _platform_dd_device_path(device, macos=is_macos())


def _stream_dd_device_path(device: str) -> str:
    return _platform_stream_dd_device_path(device, macos=is_macos())


def _stream_dd_block_args() -> list[str]:
    return _platform_stream_dd_block_args(macos=is_macos())


def _write_block_size_arg() -> str:
    return _platform_write_block_size_arg(macos=is_macos())


def _ceil_div(numerator: int, denominator: int) -> int:
    return _ceil_div_value(numerator, denominator)


def _run_zero_dd(
    device: str,
    block_bytes: int,
    seek_blocks: int,
    count_blocks: int,
    *,
    emit: FlashEventCallback | None = None,
) -> None:
    if count_blocks <= 0:
        return

    # macOS may auto-mount partitions between wipe phases (especially after long writes).
    _unmount_or_raise(device)
    output_device = _dd_device_path(device)
    _run_dd_with_progress(
        [
            _tool_path("dd"),
            "if=/dev/zero",
            f"of={output_device}",
            f"bs={block_bytes}",
            f"seek={seek_blocks}",
            f"count={count_blocks}",
            "status=progress",
        ],
        emit=emit,
    )


def _clear_stale_overlay(
    device: str,
    written_bytes: int,
    *,
    emit: FlashEventCallback | None = None,
) -> None:
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
        _emit_event(
            emit,
            "overlay_wipe_skipped",
            "Skipping stale overlay wipe; image covers the wipe region.",
        )
        return

    sector_bytes = _OVERLAY_WIPE_SECTOR_BYTES
    bulk_bytes = _OVERLAY_WIPE_BULK_BYTES
    seek_sectors = _ceil_div(start_bytes, sector_bytes)
    aligned_start = seek_sectors * sector_bytes
    span_bytes = wipe_bytes + (aligned_start - start_bytes)
    count_sectors = max(1, _ceil_div(span_bytes, sector_bytes))
    total_mib = count_sectors * sector_bytes / (1024 * 1024)
    _emit_event(
        emit,
        "overlay_wipe_started",
        f"Clearing stale OpenWrt overlay area ({total_mib:.1f} MiB at offset {start_bytes} bytes)...",
        total_mib=total_mib,
        start_bytes=start_bytes,
    )

    total_bytes = count_sectors * sector_bytes
    cursor = aligned_start
    if cursor % bulk_bytes:
        prefix_bytes = min(total_bytes, bulk_bytes - (cursor % bulk_bytes))
        prefix_sectors = _ceil_div(prefix_bytes, sector_bytes)
        _run_zero_dd(device, sector_bytes, cursor // sector_bytes, prefix_sectors, emit=emit)
        prefix_written = prefix_sectors * sector_bytes
        cursor += prefix_written
        total_bytes -= prefix_written

    bulk_blocks = total_bytes // bulk_bytes
    _run_zero_dd(device, bulk_bytes, cursor // bulk_bytes, bulk_blocks, emit=emit)
    bulk_written = bulk_blocks * bulk_bytes
    cursor += bulk_written
    total_bytes -= bulk_written

    tail_sectors = total_bytes // sector_bytes
    _run_zero_dd(device, sector_bytes, cursor // sector_bytes, tail_sectors, emit=emit)


def _write_raw_via_dd(
    image_path: str,
    device: str,
    *,
    emit: FlashEventCallback | None = None,
) -> None:
    output_device = _dd_device_path(device)
    _run_dd_with_progress(
        [
            _tool_path("dd"),
            f"if={image_path}",
            f"of={output_device}",
            _write_block_size_arg(),
            "status=progress",
        ],
        emit=emit,
    )


def _run_dd_with_progress(
    cmd: list[str],
    *,
    emit: FlashEventCallback | None = None,
) -> None:
    if emit is None:
        subprocess.run(cmd, check=True)
        return

    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    stderr = _drain_dd_progress(proc, emit)
    return_code = proc.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, cmd, stderr=stderr)


def _drain_dd_progress(
    proc: subprocess.Popen,
    emit: FlashEventCallback | None,
) -> str:
    stderr_stream = getattr(proc, "stderr", None)
    if stderr_stream is None:
        return ""
    captured = []
    buffer = []
    while True:
        char = stderr_stream.read(1)
        if not char:
            break
        captured.append(char)
        if char in "\r\n":
            _emit_dd_progress("".join(buffer).strip(), emit)
            buffer = []
        else:
            buffer.append(char)
    if buffer:
        _emit_dd_progress("".join(buffer).strip(), emit)
    return "".join(captured)


def _emit_dd_progress(text: str, emit: FlashEventCallback | None) -> None:
    if not text:
        return
    data: dict[str, Any] = {"raw": text}
    bytes_written = dd_progress_bytes(text)
    if bytes_written is not None:
        data["bytes"] = bytes_written
    if emit:
        emit({"type": "dd_progress", "message": text, **data})


def finish_flash(
    device: str,
    eject: bool = True,
    *,
    emit: FlashEventCallback | None = None,
) -> bool:
    os.sync()
    if eject:
        _emit_event(emit, "eject_started", f"Ejecting {device}...", device=device)
        try:
            eject_disk(device)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            _emit_event(emit, "warning", f"Warning: {e}", level="warning")
            _emit_event(
                emit,
                "eject_failed",
                f"Image written and payload staged, but eject failed. "
                f"Run sync and eject {device} manually before removing it.",
                level="warning",
            )
            return False
    _emit_event(emit, "safe_to_remove", "Safe to remove.", device=device)
    return True
