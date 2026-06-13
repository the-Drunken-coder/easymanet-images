from pathlib import Path


def _read_packages():
    path = (
        Path(__file__).resolve().parents[1]
        / "images"
        / "openmanet"
        / "provisioning"
        / "extra-packages.txt"
    )
    assert path.exists(), f"extra-packages.txt missing at {path}"
    pkgs = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            pkgs.append(line)
    return pkgs


def test_extra_packages_includes_iperf3():
    assert "iperf3" in _read_packages()


def test_extra_packages_include_topology_api_runtime():
    packages = _read_packages()
    assert "uhttpd" in packages
    assert "uclient-fetch" in packages
