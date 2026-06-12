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
import hashlib
import os
import re
import sys
import tempfile
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse
import zlib
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from . import __version__
from .format import human_size
from .workspace import images_dir

DownloadEventCallback = Callable[[dict[str, Any]], None]

DEFAULT_EASYMANET_GITHUB_REPO = "the-Drunken-coder/easymanet"
DEFAULT_IMAGE_GITHUB_REPO = "the-Drunken-coder/easymanet-images"
IMAGE_RELEASE_MANIFEST_ASSETS = {
    "easymanet-image-release.json",
    "easymanet-images.json",
}

_GITHUB_API_ERRORS = (
    urllib.error.URLError,
    json.JSONDecodeError,
    OSError,
    TimeoutError,
    ValueError,
)
_URL_RETRY_ERRORS = (urllib.error.URLError, OSError, TimeoutError)
_URL_RETRY_ATTEMPTS = 3
_URL_RETRY_BACKOFF_SECONDS = 0.25
_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024

SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


class ImageRef(NamedTuple):
    version: str
    url: str
    sha256: Optional[str] = None


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
        return ImageRef(info.get("version", "latest"), info["url"], sha256)

    github_repo = info.get("github") or DEFAULT_IMAGE_GITHUB_REPO
    return _check_github_release(github_repo, target)


def _fetch_github_release(repo: str) -> Optional[dict]:
    try:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        with _urlopen_with_retries(api_url, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"GitHub release lookup failed for {repo}: {exc}")
        return None


def _pick_release_asset(release: dict, target: str) -> Optional[ImageRef]:
    version = release.get("tag_name", "")
    if not version:
        return None

    assets = release.get("assets", [])
    exact = f"openmanet-{version}-{target}-squashfs-sysupgrade.img.gz"
    for asset in assets:
        if asset.get("name") == exact:
            sha256 = _sha256_for_release_asset(asset, assets)
            return ImageRef(version, asset["browser_download_url"], sha256)

    for asset in assets:
        name = asset.get("name", "")
        if (
            target in name
            and "sysupgrade" in name
            and name.endswith(".img.gz")
        ):
            _debug_note(f"Using release asset: {name}")
            sha256 = _sha256_for_release_asset(asset, assets)
            return ImageRef(version, asset["browser_download_url"], sha256)

    return None


def _check_github_release(repo: str, target: str) -> Optional[ImageRef]:
    release = _fetch_github_release(repo)
    if not release:
        return None
    manifest_result = _pick_manifest_release_asset(release, target)
    if manifest_result:
        return manifest_result
    result = _pick_release_asset(release, target)
    if not result:
        version = release.get("tag_name", "unknown")
        _debug_note(
            f"No matching sysupgrade image for target '{target}' in {repo} release {version}. "
            f"Expected asset like openmanet-{version}-{target}-squashfs-sysupgrade.img.gz"
        )
    return result


def _pick_manifest_release_asset(release: dict, target: str) -> Optional[ImageRef]:
    assets = release.get("assets", [])
    for asset in assets:
        if asset.get("name") not in IMAGE_RELEASE_MANIFEST_ASSETS:
            continue
        manifest_url = asset.get("browser_download_url")
        if not manifest_url:
            continue
        manifest = _fetch_release_manifest(manifest_url)
        if not manifest:
            continue
        ref = _image_ref_from_release_manifest(
            manifest,
            assets,
            target,
            release_version=str(release.get("tag_name", "") or ""),
        )
        if ref:
            return ref
    return None


def _fetch_release_manifest(url: str) -> Optional[dict]:
    try:
        _validate_download_url(url)
        with _urlopen_with_retries(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"image release manifest lookup failed for {url}: {exc}")
        return None


def _image_ref_from_release_manifest(
    manifest: dict,
    assets: list[dict],
    target: str,
    release_version: str = "",
) -> Optional[ImageRef]:
    if manifest.get("target") != target:
        return None
    artifact = manifest.get("artifact", {})
    filename = artifact.get("filename", "")
    sha256 = artifact.get("sha256", "")
    if not filename or not sha256:
        return None
    try:
        sha256 = normalize_sha256(sha256)
    except ValueError:
        return None

    for asset in assets:
        if asset.get("name") == filename and asset.get("browser_download_url"):
            version = (
                release_version
                or manifest.get("openmanet_version")
                or manifest.get("channel")
                or "latest"
            )
            return ImageRef(version, asset["browser_download_url"], sha256)
    return None


def normalize_sha256(value: str) -> str:
    digest = value.strip()
    if digest.lower().startswith("sha256:"):
        digest = digest.split(":", 1)[1].strip()
    if not SHA256_PATTERN.match(digest):
        raise ValueError("SHA-256 checksum must be 64 hexadecimal characters")
    return digest.lower()


def _sha256_for_release_asset(image_asset: dict, assets: list[dict]) -> Optional[str]:
    digest = _sha256_from_asset_digest(image_asset.get("digest", ""))
    if digest:
        return digest

    image_name = image_asset.get("name", "")
    if not image_name:
        return None

    checksum_assets = _candidate_checksum_assets(image_name, assets)
    for checksum_asset in checksum_assets:
        checksum_url = checksum_asset.get("browser_download_url")
        if not checksum_url:
            continue
        text = _fetch_checksum_text(checksum_url)
        if not text:
            continue
        digest = _extract_sha256_from_checksum_text(text, image_name)
        if digest:
            return digest
    return None


def _sha256_from_asset_digest(value: str) -> Optional[str]:
    if not value:
        return None
    try:
        return normalize_sha256(value)
    except ValueError:
        return None


def _candidate_checksum_assets(image_name: str, assets: list[dict]) -> list[dict]:
    exact_names = {
        f"{image_name}.sha256",
        f"{image_name}.sha256sum",
        f"{image_name}.sha256.txt",
    }
    bundle_names = {
        "SHA256SUMS",
        "SHA256SUMS.txt",
        "sha256sums",
        "sha256sums.txt",
        "checksums.txt",
    }
    exact = []
    bundled = []
    for asset in assets:
        name = asset.get("name", "")
        if name in exact_names:
            exact.append(asset)
        elif name in bundle_names:
            bundled.append(asset)
    return exact + bundled


def _fetch_checksum_text(url: str) -> str:
    try:
        _validate_download_url(url)
        with _urlopen_with_retries(url, timeout=30) as resp:
            return resp.read().decode()
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"checksum lookup failed for {url}: {exc}")
        return ""


def _extract_sha256_from_checksum_text(text: str, image_name: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        parts = lines[0].split()
        if len(parts) == 1 and SHA256_PATTERN.match(parts[0]):
            return normalize_sha256(parts[0])

    for line in lines:
        parts = line.split()
        for index, part in enumerate(parts):
            candidate = part.lstrip("*")
            if not SHA256_PATTERN.match(candidate):
                continue
            filename_tokens = parts[:index] + parts[index + 1:]
            if any(
                _checksum_filename_matches(token, image_name)
                for token in filename_tokens
            ):
                return normalize_sha256(candidate)
    return None


def _checksum_filename_matches(token: str, image_name: str) -> bool:
    filename = token.lstrip("*")
    return filename == image_name or Path(filename).name == image_name


def _url_to_filename(url: str) -> str:
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else "image.img.gz"


def _validate_download_url(url: str) -> None:
    scheme = urlparse(url).scheme.lower()
    if scheme == "http":
        raise OSError("Image downloads require HTTPS URLs")
    if scheme != "https":
        raise OSError(f"Unsupported image URL scheme: {scheme or '<none>'}")


def _urlopen_with_retries(url: str, *, timeout: int):
    last_error: Optional[BaseException] = None
    for attempt in range(1, _URL_RETRY_ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(url, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRYABLE_HTTP_STATUS_CODES or attempt == _URL_RETRY_ATTEMPTS:
                raise
            last_error = exc
        except _URL_RETRY_ERRORS as exc:
            if attempt == _URL_RETRY_ATTEMPTS:
                raise
            last_error = exc
        time.sleep(_URL_RETRY_BACKOFF_SECONDS * attempt)
    assert last_error is not None
    raise last_error


def image_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_image_sha256(path: Path, expected_sha256: str) -> None:
    expected = normalize_sha256(expected_sha256)
    actual = image_sha256(path)
    if actual != expected:
        raise OSError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def download_image(
    target: str,
    version: str,
    url: str,
    sha256: str,
    force: bool = False,
    emit: DownloadEventCallback | None = None,
) -> Path:
    _validate_download_url(url)
    expected_sha256 = normalize_sha256(sha256)
    cache = _ensure_cache_dir()
    filename = _url_to_filename(url)
    dest = cache / filename

    if dest.exists() and not force:
        if _valid_cached_image(dest) and _cached_image_matches_sha256(dest, expected_sha256):
            _save_version(target, version, sha256=expected_sha256, url=url)
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
        os.replace(tmp_path, dest)
        tmp_path = None
    except urllib.error.URLError as e:
        raise OSError(f"Download failed: {e}") from e
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    _save_version(target, version, sha256=expected_sha256, url=url)
    _emit_event(emit, "download_completed", f"  Saved: {dest}", path=str(dest))
    return dest


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
    cached_images = sorted(cache.glob(f"*{target}*"), key=_cache_mtime, reverse=True)
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


def _valid_image_payload(path: Path, filename: str) -> bool:
    named_path = Path(filename)
    suffix = named_path.suffix.lower()
    if suffix == ".img":
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    if suffix != ".gz" or not named_path.stem.lower().endswith(".img"):
        return False
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        total = 0
        with path.open("rb") as f:
            while not decompressor.eof:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(decompressor.decompress(chunk))
    except (OSError, zlib.error):
        return False
    return decompressor.eof and total > 0


def _save_version(
    target: str,
    version: str,
    *,
    sha256: Optional[str] = None,
    url: str = "",
) -> None:
    path = version_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    entry: dict[str, str] = {"version": version}
    if sha256:
        entry["sha256"] = normalize_sha256(sha256)
    if url:
        entry["url"] = url
    data[target] = entry
    path.write_text(json.dumps(data, indent=2))


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
