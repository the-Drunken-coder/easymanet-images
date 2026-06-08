from pathlib import Path


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
