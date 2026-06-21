import json
from pathlib import Path

import yaml


def test_extra_packages_referenced_by_image_workflow():
    root = Path(__file__).resolve().parents[1]
    workflow_paths = (
        root / ".github" / "workflows" / "build-openmanet-image.yml",
        root / ".github" / "workflows" / "image-release.yml",
    )
    texts = [path.read_text(encoding="utf-8") for path in workflow_paths if path.exists()]

    assert texts, "image workflow missing from image-capable repo"
    text = "\n".join(texts)
    assert "image build" in text
    assert "easymanet" in text
    assert "extra-packages.txt" in text
    assert "easymanet-image-release.json" in text
    assert "easymanet-image-manifest.json" not in text


def test_public_image_readme_documents_workflow_steps_that_exist():
    root = Path(__file__).resolve().parents[1]
    workflow_path = root / "product_repos" / "templates" / "images" / ".github" / "workflows" / "image-release.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = {step.get("name"): step for step in workflow["jobs"]["build"]["steps"] if isinstance(step, dict)}
    readme = (root / "product_repos" / "templates" / "images" / "README.md").read_text(encoding="utf-8")

    expected_operations = (
        ("generates checksums", "Generate release manifest", (".sha256", "sha_path.write_text")),
        ("easymanet-image-release.json", "Generate release manifest", ("write_release_manifest",)),
        (
            "GitHub artifact attestations",
            "Attest release artifacts",
            ("actions/attest-build-provenance", "dist/easymanet-image-release.json"),
        ),
        ("Sigstore/cosign", "Sign release manifest", ("cosign sign-blob",)),
        ("generates release notes", "Generate release notes", ("generate_image_release_notes.py",)),
        ("creates the GitHub Release", "Create GitHub release", ("gh", "release create")),
    )

    for phrase, step_name, required_tokens in expected_operations:
        assert phrase in readme
        assert step_name in steps
        step_text = json.dumps(steps[step_name], sort_keys=True)
        for token in required_tokens:
            assert token in step_text

    upload = steps["Upload firmware artifacts"]
    upload_text = json.dumps(upload, sort_keys=True)
    for artifact in (
        "dist/*.img.gz",
        "dist/*.sha256",
        "dist/easymanet-image-release.json",
        "dist/easymanet-image-release.json.sigstore.json",
        "dist/release-notes.md",
    ):
        assert artifact in upload_text
