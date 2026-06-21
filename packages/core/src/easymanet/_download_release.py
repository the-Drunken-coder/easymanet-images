"""Release discovery and URL helpers for image downloads."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple, Optional
from urllib.parse import urlparse

from ._download_integrity import SHA256_PATTERN, normalize_sha256
from .release_trust import (
    CUSTOM_TRUST_STATUS,
    ReleaseTrust,
    custom_trust,
    trust_from_manifest,
    untrusted_release,
)

IMAGE_RELEASE_MANIFEST_ASSETS = {
    "easymanet-image-release.json",
    "easymanet-images.json",
}
CANDIDATE_TAG_RE = re.compile(r"^images-v\d+\.\d+\.\d+-candidate\.\d+$")

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


class ImageRef(NamedTuple):
    version: str
    url: str
    sha256: Optional[str] = None
    trust_status: str = CUSTOM_TRUST_STATUS
    source: str = "custom"
    channel: str = ""
    release_tag: str = ""
    image_status: str = "current"
    manifest_url: str = ""
    expected_repo: str = ""
    attestation_subject_digest: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def trust(self) -> dict[str, Any]:
        return {
            "status": self.trust_status,
            "source": self.source,
            "channel": self.channel,
            "release_tag": self.release_tag,
            "image_status": self.image_status,
            "manifest_url": self.manifest_url,
            "expected_repo": self.expected_repo,
            "attestation_subject_digest": self.attestation_subject_digest,
            "warnings": list(self.warnings),
        }


def _debug_note(message: str) -> None:
    print(f"easymanet: {message}", file=sys.stderr)


def _fetch_github_release(repo: str) -> Optional[dict]:
    try:
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        with _urlopen_with_retries(api_url, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"GitHub release lookup failed for {repo}: {exc}")
        return None


def _fetch_github_releases(repo: str) -> list[dict]:
    try:
        api_url = f"https://api.github.com/repos/{repo}/releases?per_page=30"
        with _urlopen_with_retries(api_url, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []
    except _GITHUB_API_ERRORS as exc:
        _debug_note(f"GitHub release list lookup failed for {repo}: {exc}")
        return []


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


def _check_github_release(repo: str, target: str, *, channel: str = "stable") -> Optional[ImageRef]:
    releases = _candidate_releases(repo) if channel == "candidate" else [_fetch_github_release(repo)]
    for release in releases:
        if not release:
            continue
        manifest_result = _pick_manifest_release_asset(release, target, expected_repo=repo)
        if manifest_result:
            if channel != "candidate" or manifest_result.channel == "candidate":
                return manifest_result
        result = _pick_release_asset(release, target)
        if result:
            return result._replace(
                source="official",
                trust_status="untrusted",
                warnings=("Official image release does not include a canonical trust manifest.",),
            )
        version = release.get("tag_name", "unknown")
        _debug_note(
            f"No matching sysupgrade image for target '{target}' in {repo} release {version}. "
            f"Expected asset like openmanet-{version}-{target}-squashfs-sysupgrade.img.gz"
        )
    return None


def _candidate_releases(repo: str) -> list[dict]:
    return [
        release
        for release in _fetch_github_releases(repo)
        if release.get("prerelease") is True
        and CANDIDATE_TAG_RE.match(str(release.get("tag_name") or ""))
        and release.get("draft") is not True
    ]


def _pick_manifest_release_asset(
    release: dict,
    target: str,
    *,
    expected_repo: str = "",
) -> Optional[ImageRef]:
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
            expected_repo=expected_repo,
            manifest_url=manifest_url,
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
    expected_repo: str = "",
    manifest_url: str = "",
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
            trust = _manifest_trust(
                manifest,
                assets,
                expected_repo=expected_repo,
                target=target,
                release_tag=version,
                manifest_url=manifest_url,
            )
            return _image_ref(
                version,
                asset["browser_download_url"],
                sha256,
                trust=trust,
            )
    return None


def _manifest_trust(
    manifest: dict,
    assets: list[dict],
    *,
    expected_repo: str,
    target: str,
    release_tag: str,
    manifest_url: str,
) -> ReleaseTrust:
    if not expected_repo:
        return custom_trust(channel=str(manifest.get("channel") or ""), release_tag=release_tag)
    try:
        return trust_from_manifest(
            manifest,
            assets=assets,
            expected_repo=expected_repo,
            target=target,
            release_tag=release_tag,
            manifest_url=manifest_url,
        )
    except Exception as exc:  # noqa: BLE001 - trust failures are returned as data.
        return untrusted_release(str(exc), manifest_url=manifest_url, release_tag=release_tag)


def _image_ref(version: str, url: str, sha256: str, *, trust: ReleaseTrust) -> ImageRef:
    return ImageRef(
        version,
        url,
        sha256,
        trust_status=trust.status,
        source=trust.source,
        channel=trust.channel,
        release_tag=trust.release_tag,
        image_status=trust.image_status,
        manifest_url=trust.manifest_url,
        expected_repo=trust.expected_repo,
        attestation_subject_digest=trust.attestation_subject_digest,
        warnings=trust.warnings,
    )


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
