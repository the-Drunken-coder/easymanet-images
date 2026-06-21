"""Image download and cache management.

Downloads OpenMANET base images from configured URLs and caches them
locally. Checks for newer versions on each run.

Users configure download URLs in the EasyMANET workspace image manifest:

{
  "rpi4-mm6108-spi": {
    "url": "https://example.com/openmanet-rpi4-mm6108-spi.img.gz",
    "version": "2025.04",
    "github": "the-Drunken-coder/easymanet-images"
  }
}

Or pass --image-url to flash command.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from glob import escape as glob_escape
from pathlib import Path
from typing import Any, Callable, Optional

from ._download_integrity import (
    SHA256_PATTERN,
    image_sha256,
    normalize_sha256,
    valid_image_payload as _valid_image_payload,
    verify_image_sha256,
)
from ._download_release import (
    ImageRef,
    _GITHUB_API_ERRORS,
    _candidate_checksum_assets,
    _check_github_release,
    _checksum_filename_matches,
    _extract_sha256_from_checksum_text,
    _fetch_checksum_text,
    _fetch_github_release,
    _fetch_release_manifest,
    _image_ref_from_release_manifest,
    _pick_manifest_release_asset,
    _pick_release_asset,
    _sha256_for_release_asset,
    _sha256_from_asset_digest,
    _url_to_filename,
    _urlopen_with_retries,
    _validate_download_url,
)
from . import __version__
from .format import human_size
from .workspace import images_dir
from .release_trust import CUSTOM_TRUST_STATUS, OFFICIAL_TRUST_STATUS, PENDING_TRUST_STATUS

DownloadEventCallback = Callable[[dict[str, Any]], None]

DEFAULT_EASYMANET_GITHUB_REPO = "the-Drunken-coder/easymanet"
DEFAULT_IMAGE_GITHUB_REPO = "the-Drunken-coder/easymanet-images"
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _debug_note(message: str) -> None:
    print(f"easymanet: {message}", file=sys.stderr)


def cache_dir() -> Path:
    return images_dir()


def images_manifest_path() -> Path:
    return images_dir() / "images.json"


def version_file_path() -> Path:
    return images_dir() / "version.json"


def _emit_event(
    emit: DownloadEventCallback | None,
    event_type: str,
    message: str,
    **data: Any,
) -> None:
    if emit:
        emit({"type": event_type, "message": message, **data})


DEFAULT_IMAGES = {
    "rpi4-mm6108-spi": {
        "description": "OpenMANET for Raspberry Pi 4 + MM6108 SPI",
        "url": "",
        "version": "",
    },
}


def _ensure_cache_dir() -> Path:
    path = cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_images_manifest() -> dict:
    path = images_manifest_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_images_manifest(data: dict) -> None:
    path = images_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def get_image_config(target: str) -> Optional[dict]:
    manifest = _load_images_manifest()
    return manifest.get(target)


def set_image_config(
    target: str,
    url: str,
    version: str = "",
    description: str = "",
    sha256: Optional[str] = None,
) -> None:
    manifest = _load_images_manifest()
    entry = {"url": url, "version": version, "description": description}
    if sha256:
        entry["sha256"] = normalize_sha256(sha256)
    manifest[target] = entry
    _save_images_manifest(manifest)


def check_latest_version(target: str) -> Optional[ImageRef]:
    info = get_image_config(target) or {}
    if info.get("url"):
        sha256 = info.get("sha256")
        if sha256:
            try:
                sha256 = normalize_sha256(sha256)
            except ValueError as exc:
                _debug_note(f"invalid SHA-256 configured for {target}: {exc}")
                sha256 = None
        return ImageRef(
            info.get("version", "latest"),
            info["url"],
            sha256,
            trust_status=CUSTOM_TRUST_STATUS,
            source="custom",
            warnings=("Custom image is checksum-only and not an official EasyMANET release.",),
        )

    github_repo = info.get("github") or DEFAULT_IMAGE_GITHUB_REPO
    channel = str(info.get("channel") or "stable")
    return _check_github_release(github_repo, target, channel=channel)


def download_image(
    target: str,
    version: str,
    url: str,
    sha256: str,
    force: bool = False,
    emit: DownloadEventCallback | None = None,
    *,
    trust: dict[str, Any] | None = None,
) -> Path:
    _validate_download_url(url)
    expected_sha256 = normalize_sha256(sha256)
    cache = _ensure_cache_dir()
    filename = _url_to_filename(url)
    dest = cache / filename

    if dest.exists() and not force:
        if _valid_cached_image(dest) and _cached_image_matches_sha256(dest, expected_sha256):
            verified_trust = _verify_official_image_trust(dest, trust)
            _save_version(target, version, sha256=expected_sha256, url=url, trust=verified_trust)
            _prune_verified_cache(target, keep=dest, trust=verified_trust)
            return dest
        dest.unlink()

    _emit_event(
        emit,
        "download_started",
        f"Downloading {target} image ({version})...",
        target=target,
        version=version,
    )
    _emit_event(emit, "download_url", f"  URL: {url}", url=url)

    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp_path: Optional[Path] = None
    tmp_fd: Optional[int] = None
    try:
        tmp_fd, raw_tmp_path = tempfile.mkstemp(
            prefix=f".{dest.name}.",
            suffix=".part",
            dir=dest.parent,
        )
        tmp_path = Path(raw_tmp_path)

        with _urlopen_with_retries(url, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with os.fdopen(tmp_fd, "wb") as f:
                tmp_fd = None
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded / total * 100)
                        message = (
                            f"  Progress: {pct}% "
                            f"({human_size(downloaded)}/{human_size(total)})"
                        )
                        if emit:
                            emit(
                                {
                                    "type": "download_progress",
                                    "message": message,
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total,
                                    "percent": pct,
                                }
                            )
        if not _valid_image_payload(tmp_path, dest.name):
            raise OSError(f"Downloaded image failed integrity check: {dest.name}")
        verify_image_sha256(tmp_path, expected_sha256)
        verified_trust = _verify_official_image_trust(tmp_path, trust)
        os.replace(tmp_path, dest)
        tmp_path = None
    except urllib.error.URLError as e:
        raise OSError(f"Download failed: {e}") from e
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    _save_version(target, version, sha256=expected_sha256, url=url, trust=verified_trust)
    _prune_verified_cache(target, keep=dest, trust=verified_trust)
    _emit_event(emit, "download_completed", f"  Saved: {dest}", path=str(dest))
    return dest


def _verify_official_image_trust(path: Path, trust: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not trust or trust.get("source") != "official":
        return trust
    status = trust.get("status")
    if status == OFFICIAL_TRUST_STATUS:
        return trust
    if status != PENDING_TRUST_STATUS:
        raise OSError("Official image trust metadata is not verification-ready.")
    repo = str(trust.get("expected_repo") or "")
    if not repo:
        raise OSError("Official image trust metadata is missing the expected GitHub repo.")
    if shutil.which("gh") is None:
        raise OSError("GitHub CLI is required to verify official EasyMANET image attestations.")
    command = ["gh", "attestation", "verify", str(path), "--repo", repo]
    try:
        subprocess.run(command, check=True, text=True, capture_output=True, timeout=120)
    except FileNotFoundError as exc:
        raise OSError("GitHub CLI is required to verify official EasyMANET image attestations.") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError("Timed out verifying official EasyMANET image attestation.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = "Official EasyMANET image attestation verification failed."
        if detail:
            message = f"{message} {detail}"
        raise OSError(message) from exc
    verified = dict(trust)
    verified["status"] = OFFICIAL_TRUST_STATUS
    return verified


def get_cached_image(
    target: str,
    sha256: Optional[str] = None,
    url: Optional[str] = None,
) -> Optional[Path]:
    cache = _ensure_cache_dir()
    info = get_image_config(target)
    expected_sha256 = sha256 or (info or {}).get("sha256")
    if not expected_sha256:
        return None
    expected_sha256 = normalize_sha256(expected_sha256)

    source_url = url or (info or {}).get("url", "")
    if source_url:
        filename = _url_to_filename(source_url)
        if filename:
            cached = cache / filename
            if cached.exists() and _valid_cached_image(cached) and _cached_image_matches_sha256(cached, expected_sha256):
                return cached
            if cached.exists():
                cached.unlink()
    cached_images = sorted(cache.glob(f"*{glob_escape(target)}*"), key=_cache_mtime, reverse=True)
    for path in cached_images:
        if _valid_cached_image(path) and _cached_image_matches_sha256(path, expected_sha256):
            return path
    return None


def _cached_image_matches_sha256(path: Path, expected_sha256: str) -> bool:
    try:
        verify_image_sha256(path, expected_sha256)
    except OSError:
        return False
    return True


def _cache_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _valid_cached_image(path: Path) -> bool:
    return _valid_image_payload(path, path.name)


def _save_version(
    target: str,
    version: str,
    *,
    sha256: Optional[str] = None,
    url: str = "",
    trust: dict[str, Any] | None = None,
) -> None:
    path = version_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            pass
    entry: dict[str, Any] = {"version": version}
    if sha256:
        entry["sha256"] = normalize_sha256(sha256)
    if url:
        entry["url"] = url
    if trust:
        for key in ("status", "source", "channel", "release_tag", "image_status", "manifest_url", "expected_repo", "attestation_subject_digest"):
            value = trust.get(key)
            if isinstance(value, str):
                entry[f"trust_{key}" if key == "status" else key] = value
        warnings = trust.get("warnings")
        if isinstance(warnings, list):
            entry["warnings"] = [str(item) for item in warnings]
    data[target] = entry
    path.write_text(json.dumps(data, indent=2))


def _prune_verified_cache(target: str, *, keep: Path, trust: dict[str, Any] | None = None) -> None:
    if not trust or trust.get("status") != OFFICIAL_TRUST_STATUS or trust.get("source") != "official":
        return
    cache = cache_dir()
    try:
        candidates = sorted(cache.glob(f"*{glob_escape(target)}*"), key=_cache_mtime)
    except OSError:
        return
    for path in candidates:
        if path == keep or not path.is_file() or not path.name.endswith((".img", ".img.gz")):
            continue
        try:
            path.unlink()
        except OSError:
            pass


def easymanet_update_repo() -> str:
    return os.environ.get("EASYMANET_UPDATE_REPO", DEFAULT_EASYMANET_GITHUB_REPO).strip()


def check_easymanet_update() -> Optional[str]:
    repo = easymanet_update_repo()
    if not repo:
        return None
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != __version__:
            return latest
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"EasyMANET update check failed for {repo}: {exc}")
    return None
