"""Tests for Docker-backed image builds."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from easymanet import build


def test_dockerfile_contents_include_core_packages():
    dockerfile = build._dockerfile_contents()

    assert "FROM ubuntu:24.04" in dockerfile
    for package in [
        "alfred",
        "batctl",
        "g++-multilib",
        "gcc-multilib",
        "git",
        "golang-go",
        "iproute2",
        "libcap-dev",
        "libgps-dev",
        "libnl-3-dev",
        "libnl-genl-3-dev",
        "libnl-route-3-dev",
        "libopus-dev",
        "libopusfile-dev",
        "libpcre3",
        "libpcre3-dev",
        "net-tools",
        "pkg-config",
        "portaudio19-dev",
        "python3-setuptools",
        "subversion",
        "upx-ucl",
        "zstd",
    ]:
        assert package in dockerfile


def test_docker_run_command_mounts_overlay_and_output(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    output = tmp_path / "out"
    output.mkdir()

    import os

    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setattr(os, "getgid", lambda: 20)

    command = build._docker_run_command(
        repo_url=build.DEFAULT_OPENMANET_REPO,
        openmanet_version="1.6.5",
        board="ekh-bcm2711",
        target="rpi4-mm6108-spi",
        jobs=8,
        overlay_dir=overlay,
        output_dir=output,
        clean=False,
        builder_image="builder:test",
        extra_packages=["iperf3"],
    )

    assert command[:3] == ["docker", "run", "--rm"]
    assert "HOST_UID=501" in command
    assert "HOST_GID=20" in command
    assert f"type=volume,source={build.DEFAULT_CACHE_VOLUME},target=/cache" in command
    assert f"{overlay}:/overlay:ro" in command
    assert f"{output}:/out" in command
    assert "builder:test" in command


def test_docker_run_command_uses_bind_cache_dir(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()

    import os

    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setattr(os, "getgid", lambda: 20)

    command = build._docker_run_command(
        repo_url=build.DEFAULT_OPENMANET_REPO,
        openmanet_version="1.6.5",
        board="ekh-bcm2711",
        target="rpi4-mm6108-spi",
        jobs=8,
        overlay_dir=overlay,
        output_dir=output,
        clean=False,
        builder_image="builder:test",
        cache_dir=cache,
        extra_packages=[],
    )

    assert f"type=bind,source={cache},target=/cache" in command


def test_container_script_passes_bash_syntax_check():
    script = build._container_script(
        repo_url=build.DEFAULT_OPENMANET_REPO,
        openmanet_version=build.DEFAULT_OPENMANET_VERSION,
        board=build.DEFAULT_BOARD,
        target=build.DEFAULT_TARGET,
        jobs=4,
        clean=False,
        extra_packages=["iperf3"],
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script)
        path = Path(handle.name)

    try:
        subprocess.run(["bash", "-n", str(path)], check=True)
    finally:
        path.unlink(missing_ok=True)


def test_container_script_builds_expected_artifact():
    script = build._container_script(
        repo_url="https://github.com/OpenMANET/firmware.git",
        openmanet_version="1.6.5",
        board="ekh-bcm2711",
        target="rpi4-mm6108-spi",
        jobs=8,
        clean=False,
        extra_packages=["iperf3"],
    )

    assert "./scripts/openmanet_setup.sh -i -b ekh-bcm2711" in script
    assert "patch_openmanetd_alfred_pkg_config" in script
    assert "PKG_CONFIG_LIBDIR=/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig" in script
    assert "cleanup_cache_ownership" in script
    assert 'cp -R /overlay/* files/' in script
    assert "easymanet-extra-packages.txt" in script
    assert "iperf3" in script
    assert 'CONFIG_PACKAGE_${pkg}=y' in script
    assert "make defconfig" in script
    assert 'make download -j8' in script
    assert 'make -j8 V=s' in script
    assert 'openmanet-*-${TARGET}-squashfs-sysupgrade.img.gz' in script


def test_overlay_dir_falls_back_to_installed_data(monkeypatch, tmp_path):
    fake_site = tmp_path / "venv" / "lib" / "python3.11" / "site-packages"
    fake_package = fake_site / "easymanet"
    fake_package.mkdir(parents=True)
    fake_file = fake_package / "build.py"
    installed = tmp_path / "venv" / "share" / "easymanet" / "provisioning"

    monkeypatch.setattr(build, "__file__", str(fake_file))
    monkeypatch.setattr(build.sys, "prefix", str(tmp_path / "venv"))

    assert build._provisioning_dir() == installed
    assert build._overlay_dir() == installed / "openwrt-overlay"


def test_read_extra_packages_missing_file_returns_empty():
    assert build._read_extra_packages(Path("/nonexistent/extra-packages.txt")) == []


def test_read_extra_packages_strips_comments_and_blanks(tmp_path):
    package_file = tmp_path / "extra-packages.txt"
    package_file.write_text("\n# comment\niperf3\n tcpdump  # diagnostic\n")

    assert build._read_extra_packages(package_file) == ["iperf3", "tcpdump"]


def test_build_image_returns_expected_artifact(monkeypatch, tmp_path):
    output = tmp_path / "dist"
    overlay = tmp_path / "overlay"
    overlay.mkdir(parents=True)
    (overlay / "README.md").write_text("overlay")

    monkeypatch.setattr(build, "_overlay_dir", lambda: overlay)
    monkeypatch.setattr(build, "_ensure_builder_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(build, "_ensure_build_dirs", lambda: None)

    def fake_run(cmd, check=False, timeout=None, **kwargs):
        del check, timeout, kwargs
        if cmd[0:2] == ["docker", "run"]:
            output.mkdir(parents=True, exist_ok=True)
            artifact = output / "openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz"
            artifact.write_bytes(b"image")

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    artifact = build.build_image(output_dir=str(output))

    assert artifact == output / "openmanet-1.6.5-rpi4-mm6108-spi-squashfs-sysupgrade.img.gz"
    assert artifact.exists()


def test_build_image_rejects_non_positive_jobs(tmp_path):
    with pytest.raises(ValueError, match="jobs must be >= 1"):
        build.build_image(output_dir=str(tmp_path), jobs=0)


def test_ensure_builder_image_maps_missing_docker_to_build_error(monkeypatch):
    monkeypatch.setattr(build, "_ensure_build_dirs", lambda: None)

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    with pytest.raises(build.BuildError, match="Failed to execute Docker") as exc_info:
        build._ensure_builder_image("builder:test")
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_build_image_maps_missing_docker_to_build_error(monkeypatch, tmp_path):
    output = tmp_path / "dist"
    overlay = tmp_path / "overlay"
    overlay.mkdir(parents=True)

    monkeypatch.setattr(build, "_overlay_dir", lambda: overlay)
    monkeypatch.setattr(build, "_ensure_builder_image", lambda *args, **kwargs: None)
    monkeypatch.setattr(build, "_ensure_build_dirs", lambda: None)

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(build.subprocess, "run", fake_run)

    with pytest.raises(build.BuildError, match="Failed to execute Docker") as exc_info:
        build.build_image(output_dir=str(output))
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
