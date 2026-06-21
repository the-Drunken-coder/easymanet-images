"""Release metadata for EasyMANET-flavored OpenMANET images."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from easymanet import __version__ as EASYMANET_VERSION
from easymanet._download_integrity import image_sha256
from easymanet.release_trust import IMAGE_RELEASE_PRODUCT as _IMAGE_RELEASE_PRODUCT

IMAGE_RELEASE_MANIFEST = "easymanet-image-release.json"
IMAGE_RELEASE_SCHEMA_VERSION = 2
IMAGE_RELEASE_PRODUCT = _IMAGE_RELEASE_PRODUCT
DEFAULT_PUBLIC_IMAGE_REPO = "the-Drunken-coder/easymanet-images"


def build_release_manifest(
    *,
    artifact: Path,
    target: str,
    openmanet_version: str,
    board: str,
    channel: str,
    source_ref: Optional[str] = None,
    public_repo_commit: Optional[str] = None,
    workflow_run: Optional[str] = None,
    release_tag: str = "",
    artifact_url: str = "",
    source_repo: str = "",
    source_sha: str = "",
    public_repo: str = "",
    workflow_name: str = "",
    run_id: str = "",
    status: str = "current",
    signature_assets: Optional[list[str]] = None,
    attestation_subject_digest: str = "",
) -> dict[str, Any]:
    artifact = artifact.expanduser().resolve()
    if not artifact.exists():
        raise FileNotFoundError(f"Image artifact not found: {artifact}")
    digest = image_sha256(artifact)
    public_repo_name = public_repo or os.environ.get("GITHUB_REPOSITORY", "") or DEFAULT_PUBLIC_IMAGE_REPO
    run = workflow_run or _workflow_run_url()
    public_sha = public_repo_commit or os.environ.get("GITHUB_SHA", "")
    source_commit = source_sha or source_ref or _git_ref()
    signatures = signature_assets or [
        f"{artifact.name}.sha256",
        f"{IMAGE_RELEASE_MANIFEST}.sigstore.json",
    ]

    return {
        "schema_version": IMAGE_RELEASE_SCHEMA_VERSION,
        "product": IMAGE_RELEASE_PRODUCT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "release_tag": release_tag,
        "target": target,
        "board": board,
        "openmanet_version": openmanet_version,
        "easymanet_version": EASYMANET_VERSION,
        "artifact": {
            "filename": artifact.name,
            "size_bytes": artifact.stat().st_size,
            "sha256": digest,
            "url": artifact_url,
        },
        "provenance": {
            "source_repo": source_repo,
            "source_ref": source_ref or "",
            # Prefer source_sha in new consumers; keep monorepo_source through schema v2.
            "source_sha": source_commit,
            "monorepo_source": source_commit,
            "public_repo": public_repo_name,
            # Prefer public_repo_commit in new consumers; keep public_sha through schema v2.
            "public_repo_commit": public_sha,
            "public_sha": public_sha,
            "workflow_name": workflow_name or os.environ.get("GITHUB_WORKFLOW", ""),
            "workflow_run": run,
            "run_id": run_id or os.environ.get("GITHUB_RUN_ID", ""),
        },
        "trust": {
            "expected_github_repo": public_repo_name,
            "attestation_subject_digest": attestation_subject_digest or f"sha256:{digest}",
            "signature_assets": signatures,
        },
        "status": status,
    }


def write_release_manifest(
    *,
    artifact: Path,
    output_dir: Path,
    target: str,
    openmanet_version: str,
    board: str,
    channel: str = "stable",
    source_ref: Optional[str] = None,
    public_repo_commit: Optional[str] = None,
    workflow_run: Optional[str] = None,
    release_tag: str = "",
    artifact_url: str = "",
    source_repo: str = "",
    source_sha: str = "",
    public_repo: str = "",
    workflow_name: str = "",
    run_id: str = "",
    status: str = "current",
    signature_assets: Optional[list[str]] = None,
    attestation_subject_digest: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_release_manifest(
        artifact=artifact,
        target=target,
        openmanet_version=openmanet_version,
        board=board,
        channel=channel,
        source_ref=source_ref,
        public_repo_commit=public_repo_commit,
        workflow_run=workflow_run,
        release_tag=release_tag,
        artifact_url=artifact_url,
        source_repo=source_repo,
        source_sha=source_sha,
        public_repo=public_repo,
        workflow_name=workflow_name,
        run_id=run_id,
        status=status,
        signature_assets=signature_assets,
        attestation_subject_digest=attestation_subject_digest,
    )
    path = output_dir / IMAGE_RELEASE_MANIFEST
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def _workflow_run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _git_ref() -> str:
    env_ref = os.environ.get("GITHUB_SHA", "").strip()
    if env_ref:
        return env_ref
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()
