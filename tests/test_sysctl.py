from pathlib import Path


def test_sysctl_drop_in_ships_tcp_tuning():
    path = (
        Path(__file__).resolve().parents[1]
        / "provisioning"
        / "openwrt-overlay"
        / "etc"
        / "sysctl.d"
        / "99-easymanet.conf"
    )
    assert path.exists(), f"missing {path}"
    text = path.read_text()
    assert "net.ipv4.tcp_no_metrics_save=1" in text
    assert "net.ipv4.tcp_mtu_probing=1" in text
