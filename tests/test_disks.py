import plistlib

import pytest

from easymanet import disks
from easymanet.disks import linux as linux_disks
from easymanet.disks import macos


@pytest.fixture
def linux_platform(monkeypatch):
    monkeypatch.setattr(disks, "is_macos", lambda: False)
    monkeypatch.setattr(disks, "is_linux", lambda: True)


def test_linux_unmount_disk_unmounts_discovered_partitions_without_shell(monkeypatch, linux_platform):
    calls = []

    monkeypatch.setattr(
        disks.glob,
        "glob",
        lambda pattern: {
            "/dev/sdb[0-9]*": ["/dev/sdb2", "/dev/sdb1"],
            "/dev/sdbp[0-9]*": [],
        }.get(pattern, []),
    )

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(disks.subprocess, "run", fake_run)

    disks.unmount_disk("/dev/sdb")

    assert calls == [
        (["umount", "/dev/sdb1"], {"capture_output": True, "text": True, "timeout": 60}),
        (["umount", "/dev/sdb2"], {"capture_output": True, "text": True, "timeout": 60}),
    ]


def _macos_info_text(
    *,
    name: str = "USB DISK 3.0",
    size: str = "64 MB",
    protocol: str = "USB",
    virtual: str = "No",
) -> str:
    return "\n".join(
        [
            f"Device / Media Name: {name}",
            f"Disk Size: {size}",
            f"Protocol: {protocol}",
            "Device Location: External",
            "Removable Media: Removable",
            f"Virtual: {virtual}",
        ]
    )


def test_linux_unmount_disk_falls_back_to_device_when_no_partitions(monkeypatch, linux_platform):
    calls = []

    monkeypatch.setattr(disks.glob, "glob", lambda _pattern: [])
    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(disks.subprocess, "run", fake_run)

    disks.unmount_disk("/dev/mmcblk0")

    assert calls == [
        (["umount", "/dev/mmcblk0"], {"capture_output": True, "text": True, "timeout": 60}),
    ]


def test_linux_unmount_disk_ignores_not_mounted(monkeypatch, linux_platform):
    monkeypatch.setattr(disks.glob, "glob", lambda _pattern: [])

    def fake_run(cmd, **kwargs):
        return type(
            "Result",
            (),
            {"returncode": 32, "stderr": "umount: /dev/sdb: not mounted", "stdout": ""},
        )()

    monkeypatch.setattr(disks.subprocess, "run", fake_run)

    disks.unmount_disk("/dev/sdb")


def test_linux_unmount_disk_raises_on_failure(monkeypatch, linux_platform):
    monkeypatch.setattr(disks.glob, "glob", lambda _pattern: [])

    def fake_run(cmd, **kwargs):
        return type(
            "Result",
            (),
            {"returncode": 32, "stderr": "umount: /dev/sdb: target is busy", "stdout": ""},
        )()

    monkeypatch.setattr(disks.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="target is busy"):
        disks.unmount_disk("/dev/sdb")


def test_eject_disk_raises_on_failure(monkeypatch, linux_platform):
    def fake_run(cmd, **kwargs):
        assert cmd == ["eject", "/dev/sdb"]
        return type(
            "Result",
            (),
            {"returncode": 1, "stderr": "eject failed", "stdout": ""},
        )()

    monkeypatch.setattr(disks.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="eject failed"):
        disks.eject_disk("/dev/sdb")


def test_blocking_warnings_system_disk():
    disk = disks.DiskInfo(
        device="/dev/sda",
        size_bytes=500 * 1024**3,
        removable=False,
        is_system=True,
    )
    assert len(disk.blocking_warnings) == 1
    assert "system disk" in disk.blocking_warnings[0]


def test_blocking_warnings_large_fixed_disk():
    disk = disks.DiskInfo(
        device="/dev/nvme0n1",
        size_bytes=200 * 1024**3,
        removable=False,
        is_system=False,
    )
    assert any("Large fixed disk" in w for w in disk.blocking_warnings)


def test_blocking_warnings_not_in_default_list():
    disk = disks.DiskInfo(
        device="/dev/mmcblk0",
        size_bytes=32 * 1024**3,
        removable=True,
        not_in_default_list=True,
    )
    assert any("not in default disk list" in w for w in disk.blocking_warnings)


def test_blocking_warnings_virtual_disk_image():
    disk = disks.DiskInfo(
        device="/dev/disk5",
        size_bytes=64 * 1024**2,
        model="Disk Image",
        removable=True,
        virtual=True,
    )

    assert any("Virtual disk image" in w for w in disk.blocking_warnings)


def test_assert_flash_allowed_blocks_without_force(monkeypatch):
    disk = disks.DiskInfo(
        device="/dev/sda",
        size_bytes=500 * 1024**3,
        is_system=True,
    )

    monkeypatch.setattr(disks, "_is_block_device", lambda _d: True)
    monkeypatch.setattr(disks, "lookup_device", lambda _d: disk)

    with pytest.raises(ValueError, match="--force"):
        disks.assert_flash_allowed("/dev/sda", force=False)

    assert disks.assert_flash_allowed("/dev/sda", force=True) is disk


def test_linux_should_list_default_rm_or_tran():
    assert disks._linux_should_list_default({"type": "disk", "rm": "1"})
    assert disks._linux_should_list_default({"type": "disk", "rm": "0", "tran": "mmc"})
    assert disks._linux_should_list_default({"type": "disk", "rm": "0", "tran": "usb"})
    assert not disks._linux_should_list_default({"type": "disk", "rm": "0", "tran": "nvme"})


def test_linux_lsblk_data_returns_none_on_timeout(monkeypatch):
    def fail_check_output(*_args, **_kwargs):
        raise disks.subprocess.TimeoutExpired(["lsblk"], timeout=10)

    monkeypatch.setattr(linux_disks.subprocess, "check_output", fail_check_output)

    assert linux_disks._linux_lsblk_data() is None


def test_linux_disk_from_lsblk_marks_mmc_removable():
    dev = {"name": "mmcblk0", "type": "disk", "rm": "0", "tran": "mmc", "size": "32G"}
    disk = disks._linux_disk_from_lsblk(dev)
    assert disk.removable is True


def test_linux_root_block_devices_uses_findmnt(monkeypatch):
    calls = []

    def fake_findmnt(mount_point):
        calls.append(mount_point)
        return {
            "/": "/dev/nvme0n1p3",
            "/boot": None,
        }.get(mount_point)

    monkeypatch.setattr(disks, "_findmnt_source", fake_findmnt)
    monkeypatch.setattr(
        disks,
        "_linux_resolve_findmnt_source",
        lambda source: "/dev/nvme0n1" if source == "/dev/nvme0n1p3" else None,
    )
    monkeypatch.setattr(disks.os.path, "realpath", lambda path: path)
    monkeypatch.setattr(
        disks,
        "_linux_partitions_for_device",
        lambda device: ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3"]
        if device == "/dev/nvme0n1"
        else [],
    )

    related = disks._linux_root_block_devices()

    assert calls == sorted(disks.SYS_MOUNT_POINTS)
    assert "/dev/nvme0n1p3" in related
    assert "/dev/nvme0n1" in related


def test_linux_root_block_devices_includes_separate_home_disk(monkeypatch):
    def fake_findmnt(mount_point):
        return {
            "/": "/dev/nvme0n1p3",
            "/home": "/dev/sdb1",
        }.get(mount_point)

    def fake_resolve(source):
        if source == "/dev/nvme0n1p3":
            return "/dev/nvme0n1"
        if source == "/dev/sdb1":
            return "/dev/sdb"
        return None

    monkeypatch.setattr(disks, "_findmnt_source", fake_findmnt)
    monkeypatch.setattr(disks, "_linux_resolve_findmnt_source", fake_resolve)
    monkeypatch.setattr(disks.os.path, "realpath", lambda path: path)
    monkeypatch.setattr(
        disks,
        "_linux_partitions_for_device",
        lambda device: {
            "/dev/nvme0n1": ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3"],
            "/dev/sdb": ["/dev/sdb1"],
        }.get(device, []),
    )

    related = disks._linux_root_block_devices()

    assert "/dev/sdb" in related
    assert "/dev/sdb1" in related


def test_check_linux_system_disk_detects_separate_home_mount(monkeypatch):
    monkeypatch.setattr(
        disks,
        "_linux_root_block_devices",
        lambda: {"/dev/nvme0n1p3", "/dev/nvme0n1", "/dev/sdb", "/dev/sdb1"},
    )

    assert disks._check_linux_system_disk("/dev/sdb", ["/home"]) is True
    assert disks._check_linux_system_disk("/dev/sdb", []) is True


def test_check_linux_system_disk_detects_root_disk_without_mount_points(monkeypatch):
    monkeypatch.setattr(
        disks,
        "_linux_root_block_devices",
        lambda: {"/dev/nvme0n1p3", "/dev/nvme0n1"},
    )

    assert disks._check_linux_system_disk("/dev/nvme0n1", []) is True
    assert disks._check_linux_system_disk("/dev/sdb", []) is False


def test_check_linux_system_disk_treats_unknown_root_detection_as_system(monkeypatch):
    monkeypatch.setattr(disks, "_linux_root_block_devices", lambda: set())

    assert disks._check_linux_system_disk("/dev/sdb", []) is True


def test_linux_resolve_findmnt_source_handles_partuuid(monkeypatch):
    partuuid_path = "/dev/disk/by-partuuid/abc-01"

    monkeypatch.setattr(
        disks.os.path,
        "exists",
        lambda path: path == partuuid_path,
    )
    monkeypatch.setattr(
        disks.os.path,
        "realpath",
        lambda path: "/dev/nvme0n1p2" if path == partuuid_path else path,
    )

    assert disks._linux_resolve_findmnt_source("PARTUUID=abc-01") == "/dev/nvme0n1"


def test_linux_resolve_findmnt_source_maps_mapper_to_base_disk(monkeypatch):
    monkeypatch.setattr(
        disks.os.path,
        "realpath",
        lambda path: "/dev/dm-0" if path == "/dev/mapper/vg-root" else path,
    )
    monkeypatch.setattr(disks, "_linux_lsblk_pkname", lambda device: "/dev/nvme0n1" if device == "/dev/dm-0" else None)

    assert disks._linux_resolve_findmnt_source("/dev/mapper/vg-root") == "/dev/nvme0n1"


def test_check_linux_system_disk_detects_mapper_backed_root(monkeypatch):
    monkeypatch.setattr(
        disks,
        "_linux_root_block_devices",
        lambda: {"/dev/nvme0n1", "/dev/nvme0n1p3"},
    )

    assert disks._check_linux_system_disk("/dev/nvme0n1", []) is True


def test_lookup_device_lists_default_disks_once(monkeypatch, linux_platform):
    calls = {"count": 0}

    def fake_list(include_all=False):
        calls["count"] += 1
        assert include_all is False
        return [disks.DiskInfo(device="/dev/sdb", size_bytes=32 * 1024**3, removable=True)]

    monkeypatch.setattr(disks, "list_disks", fake_list)
    monkeypatch.setattr(
        disks,
        "lookup_device_linux",
        lambda device: disks.DiskInfo(device=device, size_bytes=32 * 1024**3, removable=True),
    )

    disks.lookup_device("/dev/sdc")

    assert calls["count"] == 1


def test_get_partition2_wipe_range_macos_without_list_offset(monkeypatch):
    list_plist = {
        "AllDisksAndPartitions": [
            {
                "DeviceIdentifier": "disk4",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk4s1",
                        "Size": 67108864,
                    },
                    {
                        "DeviceIdentifier": "disk4s2",
                        "Size": 4290772992,
                    },
                ],
            }
        ]
    }
    info_by_dev = {
        "disk4s1": {"PartitionMapPartitionOffset": 4194304},
        "disk4s2": {"PartitionMapPartitionOffset": 75497472},
    }

    monkeypatch.setattr(disks, "is_linux", lambda: False)
    monkeypatch.setattr(disks, "is_macos", lambda: True)

    def fake_check_output(cmd, timeout=15):
        if cmd[:3] == ["diskutil", "list", "-plist"]:
            import plistlib

            return plistlib.dumps(list_plist)
        if cmd[:3] == ["diskutil", "info", "-plist"]:
            import plistlib

            dev = cmd[3].replace("/dev/", "")
            return plistlib.dumps(info_by_dev[dev])
        raise AssertionError(cmd)

    monkeypatch.setattr(disks.subprocess, "check_output", fake_check_output)

    result = disks.get_partition2_wipe_range("/dev/disk4")
    assert result is not None
    start_bytes, wipe_bytes = result
    max_wipe = disks.OVERLAY_WIPE_BLOCK_MIB * disks.OVERLAY_WIPE_BLOCKS * 1024 * 1024
    part2_size = 4290772992
    assert wipe_bytes == min(part2_size, max_wipe)
    assert start_bytes == 75497472 + part2_size - wipe_bytes


def test_lookup_device_macos_rejects_partition_paths_before_diskutil(monkeypatch):
    calls = []

    monkeypatch.setattr(macos.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(
        macos,
        "_get_diskutil_info_text",
        lambda path: calls.append(path) or "Disk Size: 32 GB\n",
    )

    assert macos.lookup_device_macos("/dev/disk4s1") is None
    assert calls == []


def test_lookup_device_macos_accepts_raw_disk_and_canonicalizes(monkeypatch):
    seen = []

    monkeypatch.setattr(macos.os.path, "exists", lambda path: path in {"/dev/rdisk4", "/dev/disk4"})
    monkeypatch.setattr(macos, "_get_macos_all_mounts", lambda: {"disk4": ["/Volumes/BOOT"]})

    def fake_info(path):
        seen.append(path)
        return "\n".join(
            [
                "Device / Media Name: USB DISK 3.0",
                "Disk Size: 32 GB",
                "Removable Media: Removable",
            ]
        )

    monkeypatch.setattr(macos, "_get_diskutil_info_text", fake_info)

    disk = macos.lookup_device_macos("/dev/rdisk4")

    assert disk is not None
    assert disk.device == "/dev/disk4"
    assert disk.model == "USB DISK 3.0"
    assert disk.removable is True
    assert disk.mounted == ["/Volumes/BOOT"]
    assert seen == ["/dev/disk4"]


def test_list_disks_macos_external_filters_disk_images(monkeypatch):
    external_plist = {
        "AllDisksAndPartitions": [
            {"DeviceIdentifier": "disk4", "Size": 32 * 1024**3},
            {"DeviceIdentifier": "disk5", "Size": 64 * 1024**2},
        ]
    }
    all_plist = {"AllDisksAndPartitions": []}
    info_text = {
        "/dev/disk4": _macos_info_text(name="USB DISK 3.0", size="32 GB"),
        "/dev/disk5": _macos_info_text(name="Disk Image", protocol="Disk Image", virtual="Yes"),
    }

    def fake_check_output(cmd, timeout=15):
        if cmd == ["diskutil", "list", "-plist", "external"]:
            return plistlib.dumps(external_plist)
        if cmd == ["diskutil", "list", "-plist"]:
            return plistlib.dumps(all_plist)
        if cmd[:2] == ["diskutil", "info"]:
            return info_text[cmd[2]].encode()
        raise AssertionError(cmd)

    monkeypatch.setattr(macos.subprocess, "check_output", fake_check_output)

    listed = macos.list_disks_macos(include_all=False)

    assert [disk.device for disk in listed] == ["/dev/disk4"]
    assert listed[0].virtual is False


@pytest.mark.parametrize(
    ("info_text", "case"),
    [
        (_macos_info_text(virtual="Yes"), "virtual flag"),
        (_macos_info_text(protocol="Disk Image"), "protocol"),
        (_macos_info_text(name="Disk Image"), "media name"),
    ],
)
def test_list_disks_macos_external_filters_each_virtual_signal(monkeypatch, info_text, case):
    external_plist = {
        "AllDisksAndPartitions": [
            {"DeviceIdentifier": "disk4", "Size": 32 * 1024**3},
            {"DeviceIdentifier": "disk5", "Size": 64 * 1024**2},
        ]
    }
    all_plist = {"AllDisksAndPartitions": []}
    disk_info = {
        "/dev/disk4": _macos_info_text(name="USB DISK 3.0", protocol="USB", virtual="No"),
        "/dev/disk5": info_text,
    }

    def fake_check_output(cmd, timeout=15):
        if cmd == ["diskutil", "list", "-plist", "external"]:
            return plistlib.dumps(external_plist)
        if cmd == ["diskutil", "list", "-plist"]:
            return plistlib.dumps(all_plist)
        if cmd[:2] == ["diskutil", "info"]:
            return disk_info[cmd[2]].encode()
        raise AssertionError((case, cmd))

    monkeypatch.setattr(macos.subprocess, "check_output", fake_check_output)

    listed = macos.list_disks_macos(include_all=False)

    assert [disk.device for disk in listed] == ["/dev/disk4"]


def test_list_disks_macos_all_marks_disk_images_virtual(monkeypatch):
    all_plist = {
        "WholeDisks": ["disk4", "disk5"],
        "AllDisksAndPartitions": [],
    }
    info_text = {
        "/dev/disk4": _macos_info_text(name="USB DISK 3.0", size="32 GB"),
        "/dev/disk5": _macos_info_text(name="Disk Image", protocol="Disk Image", virtual="Yes"),
    }

    def fake_check_output(cmd, timeout=15):
        if cmd == ["diskutil", "list", "-plist"]:
            return plistlib.dumps(all_plist)
        if cmd[:2] == ["diskutil", "info"]:
            return info_text[cmd[2]].encode()
        raise AssertionError(cmd)

    monkeypatch.setattr(macos.subprocess, "check_output", fake_check_output)

    listed = macos.list_disks_macos(include_all=True)

    by_device = {disk.device: disk for disk in listed}
    assert by_device["/dev/disk5"].virtual is True
    assert any("Virtual disk image" in warning for warning in by_device["/dev/disk5"].warnings)


def test_lookup_device_macos_marks_disk_images_virtual(monkeypatch):
    monkeypatch.setattr(macos.os.path, "exists", lambda path: path == "/dev/disk5")
    monkeypatch.setattr(macos, "_get_macos_all_mounts", lambda: {"disk5": ["/Volumes/boot"]})
    monkeypatch.setattr(
        macos,
        "_get_diskutil_info_text",
        lambda _path: _macos_info_text(
            name="Disk Image",
            protocol="Disk Image",
            virtual="Yes",
        ),
    )

    disk = macos.lookup_device_macos("/dev/disk5")

    assert disk is not None
    assert disk.virtual is True
    assert disk.removable is True
    assert any("Virtual disk image" in warning for warning in disk.warnings)


@pytest.mark.parametrize(
    ("info_text", "case"),
    [
        (_macos_info_text(virtual="Yes"), "virtual flag"),
        (_macos_info_text(protocol="Disk Image"), "protocol"),
        (_macos_info_text(name="Disk Image"), "media name"),
    ],
)
def test_lookup_device_macos_marks_each_virtual_signal(monkeypatch, info_text, case):
    monkeypatch.setattr(macos.os.path, "exists", lambda path: path == "/dev/disk5")
    monkeypatch.setattr(macos, "_get_macos_all_mounts", lambda: {"disk5": ["/Volumes/boot"]})
    monkeypatch.setattr(macos, "_get_diskutil_info_text", lambda _path: info_text)

    disk = macos.lookup_device_macos("/dev/disk5")

    assert disk is not None, case
    assert disk.virtual is True
    assert any("Virtual disk image" in warning for warning in disk.warnings)


def test_get_macos_all_mounts_uses_structured_mount_points(monkeypatch):
    list_plist = {
        "AllDisksAndPartitions": [
            {
                "DeviceIdentifier": "disk4",
                "Partitions": [
                    {
                        "DeviceIdentifier": "disk4s1",
                        "MountPoint": "/Volumes/NO NAME",
                    },
                    {
                        "DeviceIdentifier": "disk4s2",
                    },
                ],
            }
        ]
    }
    info_plist = {"MountPoint": "/Volumes/DATA SET"}
    calls = []

    def fake_check_output(cmd, timeout=15):
        calls.append(cmd)
        if cmd == ["diskutil", "list", "-plist"]:
            return plistlib.dumps(list_plist)
        if cmd == ["diskutil", "info", "-plist", "/dev/disk4s2"]:
            return plistlib.dumps(info_plist)
        raise AssertionError(cmd)

    monkeypatch.setattr(macos.subprocess, "check_output", fake_check_output)

    assert macos._get_macos_all_mounts() == {
        "disk4": ["/Volumes/NO NAME", "/Volumes/DATA SET"]
    }
    assert calls == [
        ["diskutil", "list", "-plist"],
        ["diskutil", "info", "-plist", "/dev/disk4s2"],
    ]


def test_unmount_disk_macos_raises_when_force_unmount_fails(monkeypatch):
    calls = []
    results = [
        type("Result", (), {"returncode": 1, "stderr": "busy", "stdout": ""})(),
        type("Result", (), {"returncode": 1, "stderr": "still busy", "stdout": ""})(),
    ]

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return results.pop(0)

    monkeypatch.setattr(macos.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="still busy"):
        macos.unmount_disk_macos("/dev/disk4")

    assert calls == [
        (
            ["diskutil", "unmountDisk", "/dev/disk4"],
            {"capture_output": True, "text": True, "timeout": 60},
        ),
        (
            ["diskutil", "unmountDisk", "force", "/dev/disk4"],
            {"capture_output": True, "text": True, "timeout": 60},
        ),
    ]


def test_get_partition2_wipe_range_linux(monkeypatch, linux_platform):
    part2_start = 270336 * 512
    part2_size = 10_000_000_000
    max_wipe = disks.OVERLAY_WIPE_BLOCK_MIB * disks.OVERLAY_WIPE_BLOCKS * 1024 * 1024
    lsblk_output = {
        "blockdevices": [
            {
                "name": "sdb",
                "type": "disk",
                "children": [
                    {"name": "sdb1", "type": "part", "start": 2048, "size": 268435456},
                    {"name": "sdb2", "type": "part", "start": 270336, "size": part2_size},
                ],
            }
        ]
    }

    def fake_check_output(cmd, timeout=10):
        assert "-b" in cmd
        return __import__("json").dumps(lsblk_output).encode()

    monkeypatch.setattr(disks.subprocess, "check_output", fake_check_output)

    result = disks.get_partition2_wipe_range("/dev/sdb")
    assert result is not None
    start_bytes, wipe_bytes = result
    assert wipe_bytes == max_wipe
    assert start_bytes == part2_start + part2_size - max_wipe
    assert start_bytes > part2_start


def test_linux_partition2_wipe_range_uses_reported_logical_sector_size(monkeypatch):
    lsblk_output = {
        "blockdevices": [
            {
                "name": "sdb",
                "type": "disk",
                "log-sec": 4096,
                "children": [
                    {"name": "sdb1", "type": "part", "start": 1, "size": 1024},
                    {
                        "name": "sdb2",
                        "type": "part",
                        "start": 10,
                        "size": 4096,
                        "log-sec": 4096,
                    },
                ],
            }
        ]
    }

    def fake_check_output(cmd, timeout=10):
        assert "LOG-SEC" in cmd[4]
        return __import__("json").dumps(lsblk_output).encode()

    monkeypatch.setattr(linux_disks.subprocess, "check_output", fake_check_output)

    assert linux_disks._linux_partition2_wipe_range("/dev/sdb", 512) == (
        10 * 4096 + 4096 - 512,
        512,
    )
