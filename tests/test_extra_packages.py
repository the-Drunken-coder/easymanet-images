from pathlib import Path


def _read_packages():
    path = Path(__file__).resolve().parents[1] / "provisioning" / "extra-packages.txt"
    assert path.exists(), f"extra-packages.txt missing at {path}"
    pkgs = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            pkgs.append(line)
    return pkgs


def test_extra_packages_includes_iperf3():
    assert "iperf3" in _read_packages()


def test_extra_packages_referenced_by_build_workflow():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "build-openmanet-image.yml").read_text()
    assert "image build" in text
    assert "easymanet" in text
    assert "extra-packages.txt" in text
