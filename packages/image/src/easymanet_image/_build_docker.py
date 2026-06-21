"""Docker command generation for OpenMANET firmware builds."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Optional

DEFAULT_BUILDER_IMAGE = "easymanet-openmanet-builder:ubuntu24.04"
DEFAULT_DOCKER_PLATFORM = "linux/amd64"
DEFAULT_CACHE_VOLUME = "easymanet-openmanet-firmware-cache"

APT_PACKAGES = [
    "build-essential",
    "ca-certificates",
    "clang",
    "curl",
    "flex",
    "g++",
    "g++-multilib",
    "gawk",
    "gcc-multilib",
    "gettext",
    "git",
    "alfred",
    "batctl",
    "golang-go",
    "iproute2",
    "libcap-dev",
    "libgps-dev",
    "libncurses5-dev",
    "libnl-3-dev",
    "libnl-route-3-dev",
    "libnl-genl-3-dev",
    "libopus-dev",
    "libopusfile-dev",
    "libpcre3",
    "libpcre3-dev",
    "libssl-dev",
    "net-tools",
    "portaudio19-dev",
    "pkg-config",
    "python3",
    "python3-setuptools",
    "rsync",
    "subversion",
    "swig",
    "unzip",
    "upx-ucl",
    "file",
    "wget",
    "zlib1g-dev",
    "zstd",
]

def _dockerfile_contents() -> str:
    packages = " ".join(APT_PACKAGES)
    return f"""FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \\
    && apt-get install -y --no-install-recommends {packages} \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
"""

def _docker_run_command(
    repo_url: str,
    openmanet_version: str,
    board: str,
    target: str,
    jobs: int,
    overlay_dir: Path,
    output_dir: Path,
    clean: bool,
    builder_image: str,
    cache_dir: Optional[Path] = None,
    extra_packages: Optional[list[str]] = None,
) -> list[str]:
    uid = getattr(os, "getuid", lambda: 0)()
    gid = getattr(os, "getgid", lambda: 0)()

    script = _container_script(
        repo_url=repo_url,
        openmanet_version=openmanet_version,
        board=board,
        target=target,
        jobs=jobs,
        clean=clean,
        extra_packages=extra_packages,
    )

    cache_mount = (
        f"type=bind,source={cache_dir},target=/cache"
        if cache_dir
        else f"type=volume,source={DEFAULT_CACHE_VOLUME},target=/cache"
    )

    return [
        "docker",
        "run",
        "--rm",
        "--platform",
        DEFAULT_DOCKER_PLATFORM,
        "-e",
        f"HOST_UID={uid}",
        "-e",
        f"HOST_GID={gid}",
        "--mount",
        cache_mount,
        "-v",
        f"{overlay_dir}:/overlay:ro",
        "-v",
        f"{output_dir}:/out",
        builder_image,
        "bash",
        "-lc",
        script,
    ]

def _container_script(
    repo_url: str,
    openmanet_version: str,
    board: str,
    target: str,
    jobs: int,
    clean: bool,
    extra_packages: Optional[list[str]] = None,
) -> str:
    repo_url_q = shlex.quote(repo_url)
    version_q = shlex.quote(openmanet_version)
    board_q = shlex.quote(board)
    target_q = shlex.quote(target)
    jobs_q = shlex.quote(str(jobs))
    clean_flag = "1" if clean else "0"
    extra_packages_payload = "\n".join(extra_packages or [])
    return f"""
set -euo pipefail

REPO_DIR=/cache/openmanet-firmware
TARGET={target_q}
HOST_UID="${{HOST_UID:-0}}"
HOST_GID="${{HOST_GID:-0}}"
export FORCE_UNSAFE_CONFIGURE=1
cleanup_cache_ownership() {{
  chown -R "$HOST_UID:$HOST_GID" \\
    /cache/openmanet-firmware/dl \\
    /cache/openmanet-firmware/staging_dir/host \\
    /cache/openmanet-firmware/staging_dir/hostpkg \\
    /cache/openmanet-firmware/staging_dir/toolchain-* \\
    /cache/openmanet-firmware/build_dir/host \\
    /cache/openmanet-firmware/build_dir/hostpkg \\
    2>/dev/null || true
}}
trap cleanup_cache_ownership EXIT

if [ "{clean_flag}" = "1" ]; then
  rm -rf "$REPO_DIR"
fi

patch_openmanetd_alfred_pkg_config() {{
  local makefile="$REPO_DIR/feeds/openmanet/openmanetd/Makefile"
  [ -f "$makefile" ] || return 0
  if grep -q 'PKG_CONFIG_LIBDIR=/usr/lib/x86_64-linux-gnu/pkgconfig' "$makefile"; then
    return 0
  fi
  sed -i 's#$(MAKE) -C $(PKG_BUILD_DIR)/internal/alfred/alfred#PKG_CONFIG=/usr/bin/pkg-config PKG_CONFIG_LIBDIR=/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig $(MAKE) -C $(PKG_BUILD_DIR)/internal/alfred/alfred#' "$makefile"
}}

preserve_openwrt_cache() {{
  local source_dir="$1"
  local snapshot_dir="$2"
  rm -rf "$snapshot_dir"
  mkdir -p "$snapshot_dir"
  for rel in dl staging_dir/host staging_dir/hostpkg build_dir/host build_dir/hostpkg; do
    if [ -e "$source_dir/$rel" ]; then
      mkdir -p "$snapshot_dir/$(dirname "$rel")"
      mv "$source_dir/$rel" "$snapshot_dir/$rel"
    fi
  done
  if [ -d "$source_dir/staging_dir" ]; then
    mkdir -p "$snapshot_dir/staging_dir"
    find "$source_dir/staging_dir" -maxdepth 1 -type d -name "toolchain-*" -exec mv {{}} "$snapshot_dir/staging_dir/" \\;
  fi
}}

restore_openwrt_cache() {{
  local snapshot_dir="$1"
  local target_dir="$2"
  [ -d "$snapshot_dir" ] || return 0
  find "$snapshot_dir" -mindepth 1 -maxdepth 1 -exec mv {{}} "$target_dir/" \\;
  rm -rf "$snapshot_dir"
}}

if [ ! -d "$REPO_DIR/.git" ]; then
  CACHE_SNAPSHOT=/cache/openmanet-cache-snapshot
  rm -rf "$CACHE_SNAPSHOT"
  if [ -d "$REPO_DIR" ]; then
    preserve_openwrt_cache "$REPO_DIR" "$CACHE_SNAPSHOT"
    rm -rf "$REPO_DIR"
  fi
  git clone {repo_url_q} "$REPO_DIR"
  restore_openwrt_cache "$CACHE_SNAPSHOT" "$REPO_DIR"
fi

cd "$REPO_DIR"
git fetch --tags origin
git checkout {version_q}
git submodule update --init --recursive

mkdir -p files
rm -rf \\
  files/etc/easymanet \\
  files/etc/init.d/easymanet-* \\
	  files/etc/uci-defaults/9[5-9]-easymanet* \\
  files/usr/lib/easymanet \\
  files/www/easymanet-api
mkdir -p files/etc files/etc/uci-defaults files/usr/lib
cp -R /overlay/* files/

./scripts/openmanet_setup.sh -i -b {board_q}
patch_openmanetd_alfred_pkg_config
cat > /tmp/easymanet-extra-packages.txt <<'EASYMANET_EXTRA_PACKAGES'
{extra_packages_payload}
EASYMANET_EXTRA_PACKAGES
if [ -s /tmp/easymanet-extra-packages.txt ]; then
  while IFS= read -r pkg; do
    [ -z "$pkg" ] && continue
    grep -q "^CONFIG_PACKAGE_${{pkg}}=y$" .config && continue
    sed -i "/^# CONFIG_PACKAGE_${{pkg}} /d" .config
    sed -i "/^CONFIG_PACKAGE_${{pkg}}=/d" .config
    echo "CONFIG_PACKAGE_${{pkg}}=y" >> .config
  done < /tmp/easymanet-extra-packages.txt
  make defconfig
fi
make download -j{jobs_q}
make -j{jobs_q} V=s

artifact="$(find bin/targets -type f -name "openmanet-*-${{TARGET}}-squashfs-sysupgrade.img.gz" | sort | tail -n1)"
if [ -z "$artifact" ]; then
  echo "No artifact found for target $TARGET" >&2
  exit 1
fi
cp "$artifact" /out/
chown "$HOST_UID:$HOST_GID" /out/*.img.gz 2>/dev/null || true
"""
