"""Packaging tests for installed overlay artifacts."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY_INSTALL_ROOT = Path("share/easymanet/provisioning/openwrt-overlay")
EXECUTABLE_OVERLAY_FILES = [
    "etc/init.d/easymanet-boot-report",
    "etc/init.d/easymanet-management-lan",
    "etc/uci-defaults/97-easymanet-management-lan",
    "etc/uci-defaults/98-easymanet-boot-report",
    "etc/uci-defaults/99-easymanet",
    "usr/lib/easymanet/boot-report.sh",
    "usr/lib/easymanet/network.sh",
    "usr/lib/easymanet/provision.sh",
]
PACKAGING_COMMAND_TIMEOUT = 180


def _run_packaging_command(args, env):
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=PACKAGING_COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        pytest.fail(
            f"packaging command timed out after {e.timeout}s: {' '.join(args)}\n"
            f"stdout:\n{e.output or ''}\n"
            f"stderr:\n{e.stderr or ''}"
        )


def test_installed_wheel_preserves_overlay_executable_modes(tmp_path):
    wheel_dir = tmp_path / "wheelhouse"
    install_dir = tmp_path / "install"
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    _run_packaging_command(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(ROOT),
        ],
        env=env,
    )
    wheels = sorted(wheel_dir.glob("easymanet-*.whl"))
    assert len(wheels) == 1

    _run_packaging_command(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(install_dir),
            str(wheels[0]),
        ],
        env=env,
    )

    for rel_path in EXECUTABLE_OVERLAY_FILES:
        installed = install_dir / OVERLAY_INSTALL_ROOT / rel_path
        assert installed.exists(), rel_path
        assert installed.stat().st_mode & stat.S_IXUSR, rel_path
