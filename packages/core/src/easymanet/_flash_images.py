"""Base image resolution helpers for the flash workflow."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Optional

from ._flash_types import FlashErrorCode, FlashWorkflowError
from .download import (
    check_latest_version,
    download_image,
    get_cached_image,
    normalize_sha256,
    set_image_config,
    verify_image_sha256,
)
from .release_trust import OFFICIAL_TRUST_STATUS, PENDING_TRUST_STATUS
from .manifest import load_manifest
from .provision import resolve_provision
from .validate import validate
from .workspace import resolve_fleet_config

CUSTOM_IMAGE_VERSION = "custom"


def resolve_base_image(
    target: str,
    base_image: Optional[str],
    image_sha256: Optional[str],
    image_url: Optional[str],
    download: bool,
    no_download: bool,
    dry_run: bool,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
    normalize_sha256_fn: Callable[[str], str] = normalize_sha256,
    verify_image_sha256_fn: Callable[[Path, str], None] = verify_image_sha256,
    set_image_config_fn: Callable[..., None] = set_image_config,
    get_cached_image_fn: Callable[..., Path | None] = get_cached_image,
    check_latest_version_fn: Callable[[str], Any] = check_latest_version,
    download_image_fn: Callable[..., Path] = download_image,
) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    normalized_sha256: Optional[str] = None
    if image_sha256:
        try:
            normalized_sha256 = normalize_sha256_fn(image_sha256)
        except ValueError as exc:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                f"Invalid --image-sha256: {exc}",
            ) from exc

    if base_image:
        base_image_path = Path(base_image)
        if not base_image_path.is_file():
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                f"Base image not found: {base_image}",
            )
        if normalized_sha256:
            try:
                verify_image_sha256_fn(base_image_path, normalized_sha256)
            except OSError as exc:
                raise FlashWorkflowError(
                    FlashErrorCode.IMAGE,
                    f"Base image checksum error: {exc}",
                ) from exc
        else:
            warnings.append("Warning: local --base-image was not verified with --image-sha256.")
        if normalized_sha256:
            warnings.append("Custom local image is checksum-only and not an official EasyMANET release.")
        return str(base_image_path), image_payload(path=str(base_image_path), sha256=normalized_sha256, trust_status="checksum-only", source="custom"), warnings

    if image_url:
        if not normalized_sha256:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                "--image-url requires --image-sha256 so downloaded firmware can be verified.",
            )
        set_image_config_fn(
            target,
            image_url,
            version=CUSTOM_IMAGE_VERSION,
            sha256=normalized_sha256,
        )
        if not download:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                "Image URL saved but not downloaded. Re-run with --download to fetch the image, "
                "or add --base-image to use a local file.",
            )

    if no_download:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            "--no-download requires --base-image. No base image provided.",
        )

    if dry_run:
        cached = get_cached_image_fn(target)
        if cached:
            return str(cached), image_payload(path=str(cached), cached_path=str(cached)), warnings
        return (
            f"<auto-download for {target}>",
            image_payload(path=f"<auto-download for {target}>"),
            warnings,
        )

    latest = check_latest_version_fn(target)
    if not latest:
        if download:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                f"No image URL configured for target '{target}'. "
                f"Configure one with --image-url or specify --base-image.\n"
                f"  easymanet flash --image-url https://example.com/image.img.gz "
                f"--image-sha256 <SHA256> ...",
            )
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"No image configured for target '{target}' and no --base-image given.\n"
            f"\n"
            f"Configure an image URL with:\n"
            f"  easymanet flash --image-url <URL> --image-sha256 <SHA256> ...\n"
            f"\n"
            f"Or download an image and pass it with:\n"
            f"  easymanet flash --base-image <path-to-image> --image-sha256 <SHA256> ...",
        )
    if not latest.sha256:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"No SHA-256 checksum configured or found for target '{target}'.",
        )
    latest_source = str(getattr(latest, "source", "custom"))
    latest_trust_status = str(getattr(latest, "trust_status", "checksum-only"))
    latest_channel = str(getattr(latest, "channel", ""))
    latest_release_tag = str(getattr(latest, "release_tag", ""))
    latest_image_status = str(getattr(latest, "image_status", "current"))
    latest_manifest_url = str(getattr(latest, "manifest_url", ""))
    latest_warnings = tuple(str(warning) for warning in getattr(latest, "warnings", ()))
    latest_trust = getattr(latest, "trust", {
        "status": latest_trust_status,
        "source": latest_source,
        "channel": latest_channel,
        "release_tag": latest_release_tag,
        "image_status": latest_image_status,
        "manifest_url": latest_manifest_url,
        "warnings": list(latest_warnings),
    })

    if latest_source == "official" and latest_trust_status not in {OFFICIAL_TRUST_STATUS, PENDING_TRUST_STATUS}:
        detail = "; ".join(latest_warnings) if latest_warnings else "official verification failed"
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"Official EasyMANET image could not be verified: {detail}",
        )
    warnings.extend(latest_warnings)

    if not download and not (latest_source == "official" and latest_trust_status == PENDING_TRUST_STATUS):
        cached = get_cached_image_fn(target, sha256=latest.sha256, url=latest.url)
        if cached:
            return (
                str(cached),
                image_payload(
                    path=str(cached),
                    cached_path=str(cached),
                    version=latest.version,
                    url=latest.url,
                    sha256=latest.sha256,
                    trust_status=latest_trust_status,
                    source=latest_source,
                    channel=latest_channel,
                    release_tag=latest_release_tag,
                    image_status=latest_image_status,
                    manifest_url=latest_manifest_url,
                ),
                warnings,
            )

    try:
        if _accepts_keyword(download_image_fn, "trust"):
            path = download_image_fn(
                target,
                latest.version,
                latest.url,
                latest.sha256,
                force=download,
                emit=emit,
                trust=latest_trust,
            )
        else:
            path = download_image_fn(
                target,
                latest.version,
                latest.url,
                latest.sha256,
                force=download,
                emit=emit,
            )
        if latest_source == "official" and latest_trust_status == PENDING_TRUST_STATUS:
            latest_trust_status = OFFICIAL_TRUST_STATUS
    except OSError as exc:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"Image download error: {exc}",
        ) from exc
    return (
        str(path),
        image_payload(
            path=str(path),
            cached_path=str(path),
            version=latest.version,
            url=latest.url,
            sha256=latest.sha256,
            trust_status=latest_trust_status,
            source=latest_source,
            channel=latest_channel,
            release_tag=latest_release_tag,
            image_status=latest_image_status,
            manifest_url=latest_manifest_url,
        ),
        warnings,
    )


def _accepts_keyword(fn: Callable[..., Any], keyword: str) -> bool:
    """Return whether a callable accepts a keyword argument.

    If introspection fails, return True so security-relevant trust metadata is
    attempted and any incompatibility is surfaced by the callable itself.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return keyword in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def flash_image_details(*, config: str, node: str) -> dict[str, Any]:
    config_path = resolve_fleet_config(config)
    manifest = load_manifest(str(config_path))
    validation = validate(manifest, node_name=node)
    if validation.errors:
        return {"config_path": str(config_path), "errors": validation.errors}

    provision = resolve_provision(manifest, node)
    target = str(provision.node.target)
    details: dict[str, Any] = {
        "config_path": str(config_path),
        "node": node,
        "target": target,
        "version": "",
        "url": "",
        "sha256": "",
        "cached_path": "",
    }
    latest = check_latest_version(target)
    if latest:
        details.update(
            {
                "version": latest.version,
                "url": latest.url,
                "sha256": latest.sha256 or "",
                "trust_status": getattr(latest, "trust_status", "checksum-only"),
                "source": getattr(latest, "source", "custom"),
                "channel": getattr(latest, "channel", ""),
                "release_tag": getattr(latest, "release_tag", ""),
                "image_status": getattr(latest, "image_status", "current"),
                "manifest_url": getattr(latest, "manifest_url", ""),
                "warnings": list(getattr(latest, "warnings", ())),
            }
        )
        if latest.sha256:
            cached = get_cached_image(target, sha256=latest.sha256, url=latest.url)
            details["cached_path"] = str(cached) if cached else ""
    return details


def image_payload(
    *,
    path: str,
    cached_path: str = "",
    version: str = "",
    url: str = "",
    sha256: str | None = "",
    trust_status: str = "",
    source: str = "",
    channel: str = "",
    release_tag: str = "",
    image_status: str = "",
    manifest_url: str = "",
) -> dict[str, Any]:
    return {
        "path": path,
        "cached_path": cached_path,
        "version": version,
        "url": url,
        "sha256": sha256 or "",
        "trust_status": trust_status,
        "source": source,
        "channel": channel,
        "release_tag": release_tag,
        "image_status": image_status,
        "manifest_url": manifest_url,
    }
