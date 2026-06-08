"""Release metadata for EasyMANET-flavored OpenMANET images."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from easymanet import __version__ as EASYMANET_VERSION
from easymanet.download import image_sha256

IMAGE_RELEASE_MANIFEST = "easymanet-image-release.json"
IMAGE_RELEASE_SCHEMA_VERSION = 1


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
) -> dict[str, Any]:
    artifact = artifact.expanduser().resolve()
    if not artifact.exists():
        raise FileNotFoundError(f"Image artifact not found: {artifact}")

    return {
        "schema_version": IMAGE_RELEASE_SCHEMA_VERSION,
        "product": "easymanet-openmanet-image",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "target": target,
        "board": board,
        "openmanet_version": openmanet_version,
        "easymanet_version": EASYMANET_VERSION,
        "artifact": {
            "filename": artifact.name,
            "size_bytes": artifact.stat().st_size,
            "sha256": image_sha256(artifact),
        },
        "provenance": {
            "monorepo_source": source_ref or _git_ref(),
            "public_repo_commit": public_repo_commit or "",
            "workflow_run": workflow_run or _workflow_run_url(),
        },
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
