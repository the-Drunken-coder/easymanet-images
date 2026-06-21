"""Tests for image validation before flashing."""

import gzip
import subprocess
import sys

import pytest

from easymanet.image import (
    FlashError,
    _check_image,
    _clear_stale_overlay,
    _dd_device_path,
    _reread_partition_table,
    _run_dd_with_progress,
    _unmount_or_raise,
    _write_gz_via_dd,
    _write_raw_via_dd,
    finish_flash,
    flash_image,
)

REAL_DD_TEST = pytest.mark.skipif(
    sys.platform != "linux",
    reason="spawns the host dd binary; command-line flags differ across platforms",
)


def test_check_image_accepts_valid_gzip(tmp_path):
    image = tmp_path / "openmanet.img.gz"
    with gzip.open(image, "wb") as f:
        f.write(b"image-bytes")

    path, written = _check_image(str(image))
    assert path == image
    assert written == len(b"image-bytes")


def test_check_image_accepts_openwrt_trailing_metadata(tmp_path):
    image = tmp_path / "openmanet.img.gz"
    with gzip.open(image, "wb") as f:
        f.write(b"image-bytes")
    with image.open("ab") as f:
        f.write(b'{"metadata": "openwrt sysupgrade trailer"}')

    path, written = _check_image(str(image))
    assert path == image
    assert written == len(b"image-bytes")


def test_clear_stale_overlay_skips_trailing_metadata_gzip_when_payload_covers_region(
    monkeypatch, tmp_path
):
    payload = b"x" * 4096
    image = tmp_path / "openmanet.img.gz"
    with gzip.open(image, "wb") as f:
        f.write(payload)
    with image.open("ab") as f:
        f.write(b'{"metadata": "openwrt sysupgrade trailer"}')

    _, written = _check_image(str(image))

    monkeypatch.setattr(
        "easymanet.image.get_partition2_wipe_range",
        lambda _d: (1024, 2048),
    )

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "dd":
            raise AssertionError("dd should not run")
        return subprocess_completed()

    monkeypatch.setattr("easymanet.image.subprocess.run", fake_run)
    monkeypatch.setattr("easymanet.image._reread_partition_table", lambda _d: None)

    events = []
    _clear_stale_overlay("/dev/disk4", written, emit=events.append)
    assert events[-1]["type"] == "overlay_wipe_skipped"
    assert "Skipping stale overlay wipe" in events[-1]["message"]


def subprocess_completed():
    class Result:
        returncode = 0

    return Result()


def test_unmount_or_raise_does_not_wrap_programming_errors(monkeypatch):
    def fail_unmount(_device):
        raise TypeError("bug in caller")

    monkeypatch.setattr("easymanet.image.unmount_disk", fail_unmount)

    with pytest.raises(TypeError, match="bug in caller"):
        _unmount_or_raise("/dev/disk4")


def test_reread_partition_table_surfaces_command_failure(monkeypatch):
    run_calls = []

    def fail_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))
        raise subprocess.CalledProcessError(1, cmd, stderr="device busy")

    monkeypatch.setattr("easymanet.image.is_linux", lambda: True)
    monkeypatch.setattr("easymanet.image.is_macos", lambda: False)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)
    monkeypatch.setattr("easymanet.image.subprocess.run", fail_run)

    with pytest.raises(FlashError, match="device busy"):
        _reread_partition_table("/dev/sdb")

    assert run_calls == [
        (
            ["blockdev", "--rereadpt", "/dev/sdb"],
            {"capture_output": True, "text": True, "timeout": 30, "check": True},
        ),
        (
            ["partprobe", "/dev/sdb"],
            {"capture_output": True, "text": True, "timeout": 30, "check": True},
        ),
    ]


def test_reread_partition_table_skips_partprobe_when_blockdev_succeeds(monkeypatch):
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append((cmd, kwargs))

    monkeypatch.setattr("easymanet.image.is_linux", lambda: True)
    monkeypatch.setattr("easymanet.image.is_macos", lambda: False)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)
    monkeypatch.setattr("easymanet.image.subprocess.run", fake_run)

    _reread_partition_table("/dev/sdb")

    assert run_calls == [
        (
            ["blockdev", "--rereadpt", "/dev/sdb"],
            {"capture_output": True, "text": True, "timeout": 30, "check": True},
        )
    ]


def test_check_image_rejects_corrupt_gzip(tmp_path):
    image = tmp_path / "openmanet.img.gz"
    with gzip.open(image, "wb") as f:
        f.write(b"image-bytes")
    data = image.read_bytes()
    image.write_bytes(data[:-8])

    with pytest.raises(FlashError, match="Invalid gzip-compressed image"):
        _check_image(str(image))


def test_clear_stale_overlay_uses_large_bulk_dd(monkeypatch, tmp_path):
    calls = []
    unmount_calls = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append((cmd, check))

    def fake_unmount(device):
        unmount_calls.append(device)

    tail_start = 138412032
    wipe_bytes = 4500000000
    written_bytes = 64

    def fake_wipe_range(device):
        assert device == "/dev/disk4"
        return (tail_start, wipe_bytes)

    monkeypatch.setattr("easymanet.image.subprocess.run", fake_run)
    monkeypatch.setattr("easymanet.image.unmount_disk", fake_unmount)
    monkeypatch.setattr("easymanet.image.get_partition2_wipe_range", fake_wipe_range)
    monkeypatch.setattr("easymanet.image._reread_partition_table", lambda _d: None)
    monkeypatch.setattr("easymanet.image.is_macos", lambda: True)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)

    _clear_stale_overlay("/dev/disk4", written_bytes)

    start_bytes = max(tail_start, written_bytes)
    adjusted_wipe = wipe_bytes - (start_bytes - tail_start)
    sector_bytes = 512
    bulk_bytes = 16 * 1024 * 1024
    expected_seek = (start_bytes + sector_bytes - 1) // sector_bytes
    aligned_start = expected_seek * sector_bytes
    span_bytes = adjusted_wipe + (aligned_start - start_bytes)
    expected_count = max(1, (span_bytes + sector_bytes - 1) // sector_bytes)
    expected_total_bytes = expected_count * sector_bytes
    expected_prefix_bytes = bulk_bytes - (aligned_start % bulk_bytes)
    expected_bulk_blocks = (expected_total_bytes - expected_prefix_bytes) // bulk_bytes
    expected_tail_bytes = expected_total_bytes - expected_prefix_bytes - (
        expected_bulk_blocks * bulk_bytes
    )
    expected_tail_seek = (
        aligned_start + expected_prefix_bytes + expected_bulk_blocks * bulk_bytes
    ) // sector_bytes

    dd_calls = [c for c in calls if c[0][0] == "dd"]
    assert len(dd_calls) == 3
    assert unmount_calls == ["/dev/disk4", "/dev/disk4", "/dev/disk4"]
    prefix, bulk, tail = dd_calls
    assert all(check is True for _, check in dd_calls)
    assert "if=/dev/zero" in prefix[0]
    assert "of=/dev/rdisk4" in prefix[0]
    assert f"bs={sector_bytes}" in prefix[0]
    assert f"seek={expected_seek}" in prefix[0]
    assert f"count={expected_prefix_bytes // sector_bytes}" in prefix[0]
    assert f"bs={bulk_bytes}" in bulk[0]
    assert f"seek={(aligned_start + expected_prefix_bytes) // bulk_bytes}" in bulk[0]
    assert f"count={expected_bulk_blocks}" in bulk[0]
    assert f"bs={sector_bytes}" in tail[0]
    assert f"seek={expected_tail_seek}" in tail[0]
    assert f"count={expected_tail_bytes // sector_bytes}" in tail[0]


def test_dd_device_path_uses_raw_disk_on_macos(monkeypatch):
    monkeypatch.setattr("easymanet.image.is_macos", lambda: True)

    assert _dd_device_path("/dev/disk4") == "/dev/rdisk4"
    assert _dd_device_path("/tmp/disk.img") == "/tmp/disk.img"


def test_dd_device_path_keeps_device_on_non_macos(monkeypatch):
    monkeypatch.setattr("easymanet.image.is_macos", lambda: False)

    assert _dd_device_path("/dev/disk4") == "/dev/disk4"


def test_clear_stale_overlay_skips_when_image_covers_region(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "easymanet.image.get_partition2_wipe_range",
        lambda _d: (1024, 2048),
    )
    monkeypatch.setattr("easymanet.image._reread_partition_table", lambda _d: None)

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "dd":
            raise AssertionError("dd should not run")
        return subprocess_completed()

    monkeypatch.setattr("easymanet.image.subprocess.run", fake_run)

    events = []
    _clear_stale_overlay("/dev/disk4", 4096, emit=events.append)
    assert events[-1]["type"] == "overlay_wipe_skipped"
    assert "Skipping stale overlay wipe" in events[-1]["message"]


def test_clear_stale_overlay_raises_when_no_partition_layout(monkeypatch, tmp_path):
    monkeypatch.setattr("easymanet.image.get_partition2_wipe_range", lambda _d: None)
    monkeypatch.setattr("easymanet.image._reread_partition_table", lambda _d: None)
    monkeypatch.setattr("easymanet.image.subprocess.run", lambda *a, **k: subprocess_completed())

    with pytest.raises(FlashError, match="stale OpenWrt overlay"):
        _clear_stale_overlay("/dev/disk4", 64)


def _patch_flash_safety(monkeypatch, tmp_path):
    device = tmp_path / "fake-disk"
    device.write_bytes(b"\x00" * 65536)

    monkeypatch.setattr("easymanet.image.assert_flash_allowed", lambda _d, force=False: None)
    monkeypatch.setattr("easymanet.image.lookup_device", lambda _d: None)
    monkeypatch.setattr("easymanet.image.unmount_disk", lambda _d: None)
    monkeypatch.setattr("easymanet.image.get_partition2_wipe_range", lambda _d: (8192, 4096))
    monkeypatch.setattr("easymanet.image._reread_partition_table", lambda _d: None)
    return device


@REAL_DD_TEST
def test_write_raw_via_dd_writes_payload(tmp_path):
    device = tmp_path / "disk.img"
    device.write_bytes(b"\x00" * 4096)
    image = tmp_path / "firmware.img"
    payload = b"EASYMANET-RAW-IMAGE" * 32
    image.write_bytes(payload)

    _write_raw_via_dd(str(image), str(device))

    written = device.read_bytes()
    assert written[: len(payload)] == payload


@REAL_DD_TEST
def test_write_gz_via_dd_writes_decompressed_payload(tmp_path):
    device = tmp_path / "disk.img"
    device.write_bytes(b"\x00" * 4096)
    image = tmp_path / "firmware.img.gz"
    payload = b"EASYMANET-GZ-IMAGE" * 32
    with gzip.open(image, "wb") as handle:
        handle.write(payload)

    _write_gz_via_dd(str(image), str(device))

    written = device.read_bytes()
    assert written[: len(payload)] == payload


@REAL_DD_TEST
def test_flash_image_writes_raw_file(monkeypatch, tmp_path):
    device = _patch_flash_safety(monkeypatch, tmp_path)
    image = tmp_path / "firmware.img"
    payload = b"FLASH-RAW" * 128
    image.write_bytes(payload)

    flash_image(str(device), str(image), force=True, skip_overlay_wipe=True)

    assert device.read_bytes()[: len(payload)] == payload


def test_flash_image_unmounts_again_before_overlay_wipe(monkeypatch, tmp_path):
    device = _patch_flash_safety(monkeypatch, tmp_path)
    image = tmp_path / "firmware.img"
    image.write_bytes(b"FLASH" * 64)
    unmount_calls = []

    monkeypatch.setattr(
        "easymanet.image.unmount_disk",
        lambda d: unmount_calls.append(d),
    )
    monkeypatch.setattr("easymanet.image._write_raw_via_dd", lambda *_a, **_k: None)
    monkeypatch.setattr("easymanet.image._clear_stale_overlay", lambda *_a, **_k: None)
    monkeypatch.setattr("os.sync", lambda: None)

    flash_image(str(device), str(image), force=True)

    assert unmount_calls == [str(device), str(device)]


def test_flash_image_wraps_initial_unmount_failure(monkeypatch, tmp_path):
    device = _patch_flash_safety(monkeypatch, tmp_path)
    image = tmp_path / "firmware.img"
    image.write_bytes(b"FLASH" * 64)

    def fail_unmount(_device):
        raise RuntimeError("target is busy")

    def unexpected_write(*_args):
        raise AssertionError("flash should not write after unmount failure")

    monkeypatch.setattr("easymanet.image.unmount_disk", fail_unmount)
    monkeypatch.setattr("easymanet.image._write_raw_via_dd", unexpected_write)

    with pytest.raises(FlashError, match="Failed to unmount"):
        flash_image(str(device), str(image), force=True, skip_overlay_wipe=True)


def test_finish_flash_warns_when_eject_fails(monkeypatch):
    monkeypatch.setattr("easymanet.image.os.sync", lambda: None)

    def fail_eject(_device):
        raise RuntimeError("eject failed")

    monkeypatch.setattr("easymanet.image.eject_disk", fail_eject)

    events = []
    result = finish_flash("/dev/disk4", emit=events.append)

    assert result is False
    messages = [event["message"] for event in events]
    assert "Warning: eject failed" in messages
    assert any("eject /dev/disk4 manually" in message for message in messages)
    assert "Safe to remove." not in messages


def test_finish_flash_warns_when_eject_times_out(monkeypatch):
    monkeypatch.setattr("easymanet.image.os.sync", lambda: None)

    def fail_eject(_device):
        raise subprocess.TimeoutExpired(["eject", "/dev/disk4"], timeout=30)

    monkeypatch.setattr("easymanet.image.eject_disk", fail_eject)

    events = []
    result = finish_flash("/dev/disk4", emit=events.append)

    assert result is False
    messages = [event["message"] for event in events]
    assert any("timed out" in message for message in messages)
    assert any("eject /dev/disk4 manually" in message for message in messages)
    assert "Safe to remove." not in messages


@REAL_DD_TEST
def test_flash_image_writes_gzip_file(monkeypatch, tmp_path):
    device = _patch_flash_safety(monkeypatch, tmp_path)
    image = tmp_path / "firmware.img.gz"
    payload = b"FLASH-GZ" * 128
    with gzip.open(image, "wb") as handle:
        handle.write(payload)

    flash_image(str(device), str(image), force=True, skip_overlay_wipe=True)

    assert device.read_bytes()[: len(payload)] == payload


def test_write_gz_via_dd_accepts_gzip_exit_code_2(monkeypatch, tmp_path):
    device = tmp_path / "disk.img"
    image = tmp_path / "firmware.img.gz"
    with gzip.open(image, "wb") as handle:
        handle.write(b"payload")
    with image.open("ab") as handle:
        handle.write(b'{"metadata": "trailer"}')

    import io

    class FakeProc:
        def __init__(self, returncode, stdout=None):
            self.returncode = returncode
            self.stdout = stdout

        def communicate(self):
            return ("", "")

        def wait(self):
            return self.returncode

    gzip_stdout = io.BytesIO(b"payload")
    procs = [FakeProc(2, gzip_stdout), FakeProc(0)]
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        if cmd[0] == "gzip":
            return procs[0]
        return procs[1]

    monkeypatch.setattr("easymanet.image.is_macos", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)

    _write_gz_via_dd(str(image), str(device))

    assert popen_calls == [
        (["gzip", "-dc", str(image)], {"stdout": subprocess.PIPE}),
        (
            ["dd", f"of={device}", "bs=16M", "status=progress"],
            {"stdin": gzip_stdout, "stderr": subprocess.PIPE, "text": True},
        ),
    ]
    assert gzip_stdout.closed is True


def test_write_gz_via_dd_uses_raw_padded_device_on_macos(monkeypatch, tmp_path):
    image = tmp_path / "firmware.img.gz"
    with gzip.open(image, "wb") as handle:
        handle.write(b"payload")

    import io

    class FakeProc:
        def __init__(self, returncode, stdout=None):
            self.returncode = returncode
            self.stdout = stdout

        def communicate(self):
            return ("", "")

        def wait(self):
            return self.returncode

    gzip_stdout = io.BytesIO(b"payload")
    procs = [FakeProc(0, gzip_stdout), FakeProc(0)]
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        if cmd[0] == "gzip":
            return procs[0]
        return procs[1]

    monkeypatch.setattr("easymanet.image.is_macos", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)

    _write_gz_via_dd(str(image), "/dev/disk4")

    assert [
        "dd",
        "of=/dev/rdisk4",
        "bs=1m",
        "status=progress",
    ] in popen_calls


def test_write_gz_via_dd_reports_dd_failure_before_gzip_sigpipe(monkeypatch, tmp_path):
    image = tmp_path / "firmware.img.gz"
    with gzip.open(image, "wb") as handle:
        handle.write(b"payload")

    import io

    class FakeProc:
        def __init__(self, returncode, stdout=None, stderr=None):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

        def communicate(self):
            stderr = self.stderr.getvalue() if self.stderr else ""
            return ("", stderr)

        def wait(self):
            return self.returncode

    gzip_stdout = io.BytesIO(b"payload")
    procs = [
        FakeProc(-13, stdout=gzip_stdout),
        FakeProc(1, stderr=io.StringIO("dd: /dev/rdisk4: Invalid argument\n")),
    ]

    def fake_popen(cmd, **_kwargs):
        if cmd[0] == "gzip":
            return procs[0]
        return procs[1]

    monkeypatch.setattr("easymanet.image.is_macos", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _write_gz_via_dd(str(image), "/dev/disk4")

    assert exc_info.value.cmd[0] == "dd"
    assert "iflag=fullblock" not in exc_info.value.cmd
    assert "conv=osync" not in exc_info.value.cmd
    assert "bs=1m" in exc_info.value.cmd
    assert "Invalid argument" in exc_info.value.stderr


def test_write_raw_via_dd_uses_macos_dd_block_suffix(monkeypatch, tmp_path):
    image = tmp_path / "firmware.img"
    image.write_bytes(b"payload")
    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)

    monkeypatch.setattr("easymanet.image.is_macos", lambda: True)
    monkeypatch.setattr("easymanet.image.subprocess.run", fake_run)
    monkeypatch.setattr("easymanet.image._tool_path", lambda name: name)

    _write_raw_via_dd(str(image), "/dev/disk4")

    assert ["dd", f"if={image}", "of=/dev/rdisk4", "bs=16m", "status=progress"] in run_calls


def test_run_dd_with_progress_emits_parsed_byte_count(monkeypatch):
    import io

    class FakeProc:
        stderr = io.StringIO("1048576 bytes transferred\n")

        def wait(self):
            return 0

    monkeypatch.setattr("easymanet.image.subprocess.Popen", lambda *_a, **_k: FakeProc())
    events = []

    _run_dd_with_progress(["dd", "if=image", "of=device"], emit=events.append)

    assert events == [
        {
            "type": "dd_progress",
            "message": "1048576 bytes transferred",
            "raw": "1048576 bytes transferred",
            "bytes": 1048576,
        }
    ]


def test_check_device_safety_requires_force_for_blocking_disk(monkeypatch):
    from easymanet import disks
    from easymanet.image import _check_device_safety

    disk = disks.DiskInfo(device="/dev/sda", is_system=True)

    def fake_assert(device, force=False):
        if not force:
            raise ValueError("Use --force to override.")
        return disk

    monkeypatch.setattr("easymanet.image.assert_flash_allowed", fake_assert)

    with pytest.raises(FlashError, match="--force"):
        _check_device_safety("/dev/sda", force=False)

    _check_device_safety("/dev/sda", force=True)
