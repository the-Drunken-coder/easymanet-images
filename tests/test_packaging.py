"""Packaging tests for installed overlay artifacts."""

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY_INSTALL_ROOT = Path("share/easymanet/images/openmanet/provisioning/openwrt-overlay")
EXECUTABLE_OVERLAY_FILES = [
    "etc/init.d/easymanet-boot-report",
    "etc/init.d/easymanet-management-lan",
    "etc/uci-defaults/97-easymanet-management-lan",
    "etc/uci-defaults/98-easymanet-boot-report",
    "etc/uci-defaults/99-easymanet",
    "usr/lib/easymanet/api.sh",
    "usr/lib/easymanet/boot-report.sh",
    "usr/lib/easymanet/network.sh",
    "usr/lib/easymanet/provision.sh",
    "www/easymanet-api/v1/identity",
    "www/easymanet-api/v1/neighbors",
    "www/easymanet-api/v1/topology",
]
PACKAGING_COMMAND_TIMEOUT = 180


def _load_release_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "release_smoke", ROOT / "tools" / "release_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
    release_smoke = _load_release_smoke_module()
    wheels = release_smoke.built_wheels(wheel_dir, ROOT)
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


def test_release_smoke_installs_wheel_in_temp_venv(tmp_path):
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    result = _run_packaging_command(
        [
            sys.executable,
            str(ROOT / "tools" / "release_smoke.py"),
            "--temp-root",
            str(tmp_path / "release-smoke"),
            "--skip-electron",
        ],
        env=env,
    )

    assert "Release smoke passed." in result.stdout


def test_release_smoke_run_passes_timeout_to_subprocess(monkeypatch):
    release_smoke = _load_release_smoke_module()
    captured = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release_smoke.subprocess, "run", fake_run)

    release_smoke.run(["echo", "ok"], timeout=7)

    assert captured["timeout"] == 7


def test_release_smoke_wheel_glob_uses_normalized_project_name(tmp_path):
    release_smoke = _load_release_smoke_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "easymanet-images"\nversion = "0.2.0"\n'
    )

    assert release_smoke.wheel_glob_pattern(repo) == "easymanet_images-*.whl"
