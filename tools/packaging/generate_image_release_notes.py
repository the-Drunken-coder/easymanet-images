#!/usr/bin/env python3
"""Generate image release notes with the OpenAI Responses API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gpt-5.4-mini"


def main() -> int:
    manifest_path = Path(os.environ.get("IMAGE_MANIFEST", "dist/easymanet-image-release.json"))
    output_path = Path(os.environ.get("RELEASE_NOTES", "dist/release-notes.md"))
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required to generate automated release notes")
    if not manifest_path.exists():
        raise SystemExit(f"Image manifest not found: {manifest_path}")

    context = build_context(manifest_path)
    notes = call_openai(api_key, os.environ.get("OPENAI_RELEASE_NOTES_MODEL", DEFAULT_MODEL), context)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(notes.rstrip() + "\n")
    return 0


def build_context(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    return {
        "task": "Write EasyMANET image release notes with a user summary and a technical appendix.",
        "new_manifest": manifest,
        "source_commit_summary": git_output(["log", "--oneline", "-20"]),
        "provisioning_overlay_diff_summary": git_output([
            "diff",
            "--stat",
            "HEAD~1..HEAD",
            "--",
            "images/openmanet/provisioning",
        ]),
        "test_status": "workflow unit tests completed before release note generation",
        "privacy": "Do not include secrets, credentials, tokens, or private support data.",
    }


def call_openai(api_key: str, model: str, context: dict[str, Any]) -> str:
    body = {
        "model": model or DEFAULT_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "You write concise EasyMANET firmware image release notes. "
                    "Use two Markdown sections exactly: User Summary and Technical Appendix. "
                    "Do not invent hardware support or include secrets."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, indent=2, sort_keys=True),
            },
        ],
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode(errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"OpenAI API error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"OpenAI API unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"OpenAI API returned invalid JSON: {exc}") from exc
    text = payload.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(str(content["text"]))
    if not chunks:
        raise SystemExit("OpenAI response did not include release note text")
    return "\n".join(chunks)


def git_output(args: list[str]) -> str:
    try:
        result = subprocess.run(["git", *args], text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
