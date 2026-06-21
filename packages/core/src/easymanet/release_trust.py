"""Image release manifest trust helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._download_integrity import normalize_sha256

IMAGE_RELEASE_PRODUCT = "easymanet-openmanet-image"
OFFICIAL_TRUST_STATUS = "verified"
PENDING_TRUST_STATUS = "verification-pending"
CUSTOM_TRUST_STATUS = "checksum-only"
UNTRUSTED_STATUS = "untrusted"
ALLOWED_CHANNELS = {"stable", "candidate"}
WARNING_IMAGE_STATUSES = {"superseded", "unsafe"}


@dataclass(frozen=True)
class ReleaseTrust:
    status: str
    source: str
    channel: str = ""
    release_tag: str = ""
    image_status: str = "current"
    manifest_url: str = ""
    manifest_schema_version: int = 0
    expected_repo: str = ""
    attestation_subject_digest: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "channel": self.channel,
            "release_tag": self.release_tag,
            "image_status": self.image_status,
            "manifest_url": self.manifest_url,
            "manifest_schema_version": self.manifest_schema_version,
            "expected_repo": self.expected_repo,
            "attestation_subject_digest": self.attestation_subject_digest,
            "warnings": list(self.warnings),
        }


def custom_trust(*, channel: str = "", release_tag: str = "") -> ReleaseTrust:
    return ReleaseTrust(
        status=CUSTOM_TRUST_STATUS,
        source="custom",
        channel=channel,
        release_tag=release_tag,
        warnings=("Custom image is checksum-only and not an official EasyMANET release.",),
    )


def untrusted_release(reason: str, *, manifest_url: str = "", release_tag: str = "") -> ReleaseTrust:
    return ReleaseTrust(
        status=UNTRUSTED_STATUS,
        source="official",
        release_tag=release_tag,
        manifest_url=manifest_url,
        warnings=(reason,),
    )


def trust_from_manifest(
    manifest: dict[str, Any],
    *,
    assets: list[dict[str, Any]],
    expected_repo: str,
    target: str,
    release_tag: str,
    manifest_url: str,
) -> ReleaseTrust:
    warnings: list[str] = []
    try:
        schema_version = int(manifest.get("schema_version") or 0)
    except (TypeError, ValueError):
        return untrusted_release("Image manifest schema_version is invalid.", manifest_url=manifest_url, release_tag=release_tag)
    channel = str(manifest.get("channel") or "")
    image_status = str(manifest.get("status") or "current")

    if schema_version < 2:
        return ReleaseTrust(
            status=CUSTOM_TRUST_STATUS,
            source="official",
            channel=channel,
            release_tag=release_tag,
            image_status=image_status,
            manifest_url=manifest_url,
            manifest_schema_version=schema_version,
            warnings=("Legacy image manifest is checksum-only; official verification requires schema v2.",),
        )

    if manifest.get("product") != IMAGE_RELEASE_PRODUCT:
        return untrusted_release("Image manifest product is not recognized.", manifest_url=manifest_url, release_tag=release_tag)
    if manifest.get("target") != target:
        return untrusted_release("Image manifest target does not match the requested target.", manifest_url=manifest_url, release_tag=release_tag)
    if channel not in ALLOWED_CHANNELS:
        return untrusted_release("Image manifest channel must be stable or candidate.", manifest_url=manifest_url, release_tag=release_tag)

    artifact = manifest.get("artifact", {})
    try:
        artifact_sha = normalize_sha256(str(artifact.get("sha256", "")))
    except ValueError:
        return untrusted_release("Image manifest artifact SHA-256 is invalid.", manifest_url=manifest_url, release_tag=release_tag)

    trust = manifest.get("trust", {})
    expected = str(trust.get("expected_github_repo") or "")
    if not expected:
        return untrusted_release("Official image manifest does not declare the expected GitHub repo.", manifest_url=manifest_url, release_tag=release_tag)
    if expected != expected_repo:
        return untrusted_release("Image manifest was not issued for the configured official image repo.", manifest_url=manifest_url, release_tag=release_tag)

    subject = str(trust.get("attestation_subject_digest") or "")
    if not subject:
        return untrusted_release("Official image manifest does not declare an attestation subject digest.", manifest_url=manifest_url, release_tag=release_tag)
    if subject.removeprefix("sha256:") != artifact_sha:
        return untrusted_release("Image attestation digest does not match the artifact SHA-256.", manifest_url=manifest_url, release_tag=release_tag)

    asset_names = {str(asset.get("name") or "") for asset in assets}
    raw_signature_assets = trust.get("signature_assets", [])
    if not isinstance(raw_signature_assets, list) or not all(isinstance(name, str) for name in raw_signature_assets):
        return untrusted_release("Official image manifest signature assets are malformed.", manifest_url=manifest_url, release_tag=release_tag)
    signature_assets = [name for name in raw_signature_assets if name]
    if not signature_assets:
        return untrusted_release("Official image manifest does not list signature assets.", manifest_url=manifest_url, release_tag=release_tag)
    missing = [name for name in signature_assets if name not in asset_names]
    if missing:
        return untrusted_release(
            f"Official image release is missing trust assets: {', '.join(missing)}.",
            manifest_url=manifest_url,
            release_tag=release_tag,
        )

    if image_status in WARNING_IMAGE_STATUSES:
        warnings.append(f"Image release is marked {image_status}; continuing with a warning.")

    return ReleaseTrust(
        status=PENDING_TRUST_STATUS,
        source="official",
        channel=channel,
        release_tag=str(manifest.get("release_tag") or release_tag),
        image_status=image_status,
        manifest_url=manifest_url,
        manifest_schema_version=schema_version,
        expected_repo=expected,
        attestation_subject_digest=subject,
        warnings=tuple(warnings),
    )


def image_trust_payload(trust: ReleaseTrust | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(trust, ReleaseTrust):
        return trust.to_dict()
    if isinstance(trust, dict):
        return dict(trust)
    return {}
