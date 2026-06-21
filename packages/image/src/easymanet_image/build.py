"""Docker-backed OpenMANET firmware builds."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from ._build_docker import (
    DEFAULT_BUILDER_IMAGE,
    DEFAULT_CACHE_VOLUME,
    DEFAULT_DOCKER_PLATFORM,
    _container_script,
    _docker_run_command,
    _dockerfile_contents,
)


DEFAULT_OPENMANET_REPO = "https://github.com/OpenMANET/firmware.git"
DEFAULT_OPENMANET_VERSION = "1.6.5"
DEFAULT_BOARD = "ekh-bcm2711"
DEFAULT_TARGET = "rpi4-mm6108-spi"
BUILD_ROOT = Path.home() / ".easymanet" / "build"
DOCKER_CONTEXT_DIR = BUILD_ROOT / "docker"


class BuildError(Exception):
    pass


def build_image(
    output_dir: str,
    openmanet_version: str = DEFAULT_OPENMANET_VERSION,
    board: str = DEFAULT_BOARD,
    target: str = DEFAULT_TARGET,
    repo_url: str = DEFAULT_OPENMANET_REPO,
    jobs: Optional[int] = None,
    clean: bool = False,
    rebuild_builder: bool = False,
    builder_image: str = DEFAULT_BUILDER_IMAGE,
    cache_dir: Optional[str] = None,
) -> Path:
    if jobs is None:
        jobs = max(os.cpu_count() or 1, 1)
    elif jobs < 1:
        raise ValueError(f"jobs must be >= 1, got {jobs}")
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    provisioning_dir = _provisioning_dir()
    overlay_dir = provisioning_dir / "openwrt-overlay"
    if not overlay_dir.exists():
        raise BuildError(f"OpenWrt overlay not found: {overlay_dir}")
    extra_packages = _read_extra_packages(provisioning_dir / "extra-packages.txt")

    _ensure_builder_image(builder_image, force=rebuild_builder)
    _ensure_build_dirs()
    cache_path = Path(cache_dir).expanduser().resolve() if cache_dir else None
    if cache_path:
        cache_path.mkdir(parents=True, exist_ok=True)

    command = _docker_run_command(
        repo_url=repo_url,
        openmanet_version=openmanet_version,
        board=board,
        target=target,
        jobs=jobs,
        overlay_dir=overlay_dir,
        output_dir=output_path,
        clean=clean,
        builder_image=builder_image,
        cache_dir=cache_path,
        extra_packages=extra_packages,
    )

    try:
        _run_docker(command, check=True, timeout=None)
    except subprocess.CalledProcessError as e:
        raise BuildError(f"Docker build failed: {e}") from e

    artifact = output_path / f"openmanet-{openmanet_version}-{target}-squashfs-sysupgrade.img.gz"
    if artifact.exists():
        return artifact

    built = sorted(output_path.glob(f"openmanet-*-{target}-squashfs-sysupgrade.img.gz"))
    if not built:
        raise BuildError(
            f"No built image found in {output_path} for target {target}. "
            "Check the Docker build logs for the OpenMANET artifact path."
        )
    return built[-1]


def _ensure_build_dirs() -> None:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    DOCKER_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)


def _provisioning_dir() -> Path:
    configured = os.environ.get("EASYMANET_PROVISIONING_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        source_tree_dir = parent / "images" / "openmanet" / "provisioning"
        if source_tree_dir.exists():
            return source_tree_dir

    return Path(sys.prefix) / "share" / "easymanet" / "images" / "openmanet" / "provisioning"


def _overlay_dir() -> Path:
    """Return the OpenWrt overlay directory (source tree or installed data)."""
    return _provisioning_dir() / "openwrt-overlay"


def _read_extra_packages(path: Path) -> list[str]:
    if not path.exists():
        return []
    packages = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            packages.append(line)
    return packages


def _run_docker(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, **kwargs)
    except OSError as e:
        raise BuildError(
            "Failed to execute Docker. Ensure Docker is installed and on PATH."
        ) from e


def _ensure_builder_image(image_name: str, force: bool = False) -> None:
    _ensure_build_dirs()
    if force:
        _run_docker(["docker", "image", "rm", "-f", image_name], capture_output=True)

    exists = _run_docker(
        ["docker", "image", "inspect", image_name],
        capture_output=True,
        timeout=30,
    )
    if exists.returncode == 0:
        return

    dockerfile = _dockerfile_contents()
    context_dir = Path(tempfile.mkdtemp(prefix="easymanet_docker_", dir=DOCKER_CONTEXT_DIR))
    try:
        dockerfile_path = context_dir / "Dockerfile"
        dockerfile_path.write_text(dockerfile)
        _run_docker(
            ["docker", "build", "--platform", DEFAULT_DOCKER_PLATFORM, "-t", image_name, str(context_dir)],
            check=True,
            timeout=None,
        )
    except subprocess.CalledProcessError as e:
        raise BuildError(f"Failed to build Docker builder image {image_name}: {e}") from e
    finally:
        _remove_tree(context_dir)

def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink(missing_ok=True)
        else:
            try:
                child.rmdir()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass
