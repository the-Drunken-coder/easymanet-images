"""Behavior tests for first-boot provisioning shell scripts."""

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "images" / "openmanet" / "provisioning" / "openwrt-overlay"
PROVISION_LIB = OVERLAY / "usr" / "lib" / "easymanet" / "provision-lib.sh"
PROVISION_SCRIPT = OVERLAY / "usr" / "lib" / "easymanet" / "provision.sh"
NETWORK_SCRIPT = OVERLAY / "usr" / "lib" / "easymanet" / "network.sh"
HARNESS = Path(__file__).resolve().parent / "shell_harness"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openwrt"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _harness_env(uci_state: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{HARNESS}:{env.get('PATH', '')}"
    env["UCI_STATE_FILE"] = str(uci_state)
    if extra:
        env.update(extra)
    return env


def _seed_wireless_radios(uci_state: Path) -> None:
    uci_state.write_text(_fixture_text("uci-wireless-radios.txt"))


def _run_sh(script_body: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", "-c", script_body],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_find_morse_radio_prefers_morse_type(tmp_path):
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    env = _harness_env(uci_state)
    result = _run_sh(
        f'. "{PROVISION_LIB}"; find_morse_radio',
        env,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "radio2"


def test_find_local_ap_radio_prefers_mmc_mac80211(tmp_path):
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    env = _harness_env(uci_state)
    result = _run_sh(
        f'. "{PROVISION_LIB}"; find_local_ap_radio',
        env,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "radio3"


def test_json_bool(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(
        json.dumps(
            {
                "management": {"ssh_enabled": True},
                "node": {"gateway": {"wifi": {"enabled": False}}},
            }
        )
    )
    env = _harness_env(tmp_path / "uci-unused")
    env["PROVISION_JSON"] = str(provision_json)
    script = f'''
. "{PROVISION_LIB}"
if json_bool management ssh_enabled; then echo ssh_on; else echo ssh_off; fi
if json_bool node gateway wifi enabled; then echo wifi_on; else echo wifi_off; fi
'''
    result = _run_sh(script, env)
    assert result.returncode == 0
    assert "ssh_on" in result.stdout
    assert "wifi_off" in result.stdout


def test_jsonfilter_rejects_malformed_array_expression(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps({"items": ["one"]}))

    result = subprocess.run(
        [str(HARNESS / "jsonfilter"), "-i", str(provision_json), "-e", "items[*]"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""


def test_uci_harness_escapes_backslashes_in_keys(tmp_path):
    uci_state = tmp_path / "uci-state"
    env = _harness_env(uci_state)
    result = _run_sh(
        r'''
uci set 'wireless.foo\bar=value1'
uci set 'wireless.fooxbar=value2'
uci -q get 'wireless.foo\bar'
''',
        env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "value1"


def test_uci_harness_missing_get_fails(tmp_path):
    uci_state = tmp_path / "uci-state"
    env = _harness_env(uci_state)

    result = subprocess.run(
        ["uci", "-q", "get", "network.missing.option"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""


def _gate_provision_json() -> dict:
    return {
        "version": 1,
        "mesh": {
            "id": "test-mesh",
            "password": "mesh-password-123",
            "channel": 42,
            "bandwidth_mhz": 2,
            "country": "US",
        },
        "node": {
            "name": "gate01",
            "hostname": "gate01",
            "role": "gate",
            "target": "rpi4-mm6108-spi",
            "ip": "10.41.1.1",
            "local_ap": {"enabled": False},
            "gateway": {"enabled": True, "uplink_interface": "eth0"},
        },
        "management": {
            "root_password_hash": "",
            "ssh_authorized_keys": [],
            "ssh_enabled": True,
        },
    }


def _wifi_gate_provision_json() -> dict:
    data = _gate_provision_json()
    data["node"]["gateway"] = {
        "enabled": True,
        "uplink_interface": "wifi",
        "wifi": {
            "enabled": True,
            "ssid": "upstream",
            "password": "upstream-password",
            "encryption": "psk2",
        },
    }
    return data


def _point_provision_json() -> dict:
    data = _gate_provision_json()
    data["node"] = {
        "name": "point01",
        "hostname": "point01",
        "role": "point",
        "target": "rpi4-mm6108-spi",
        "ip": "10.41.2.1",
        "local_ap": {"enabled": False},
        "gateway": {"enabled": False},
    }
    data["management"] = {
        "root_password_hash": "",
        "ssh_authorized_keys": [],
        "ssh_enabled": False,
    }
    return data


def _copy_api_overlay(prefix: Path) -> None:
    for relative in (
        "usr/lib/easymanet/api-lib.sh",
        "usr/lib/easymanet/api.sh",
        "usr/lib/easymanet/provision-lib.sh",
        "usr/lib/easymanet/status-lib.sh",
        "www/easymanet-api/v1/identity",
        "www/easymanet-api/v1/neighbors",
        "www/easymanet-api/v1/status",
        "www/easymanet-api/v1/topology",
    ):
        source = OVERLAY / relative
        target = prefix / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _write_dropbear_stub(prefix: Path) -> None:
    init_dir = prefix / "etc" / "init.d"
    init_dir.mkdir(parents=True, exist_ok=True)
    state_file = prefix / "var" / "dropbear-state"
    stub = init_dir / "dropbear"
    stub.write_text(
        f"""#!/bin/sh
state_file="{state_file}"
case "$1" in
  enable) echo enabled >> "$state_file" ;;
  restart) echo restarted >> "$state_file" ;;
  start) echo started >> "$state_file" ;;
  disable) echo disabled >> "$state_file" ;;
  stop) echo stopped >> "$state_file" ;;
esac
"""
    )
    stub.chmod(0o755)


def _write_uhttpd_stub(prefix: Path) -> None:
    init_dir = prefix / "etc" / "init.d"
    init_dir.mkdir(parents=True, exist_ok=True)
    state_file = prefix / "var" / "uhttpd-state"
    stub = init_dir / "uhttpd"
    stub.write_text(
        f"""#!/bin/sh
state_file="{state_file}"
case "$1" in
  enable) echo enabled >> "$state_file" ;;
  restart) echo restarted >> "$state_file" ;;
  start) echo started >> "$state_file" ;;
esac
"""
    )
    stub.chmod(0o755)


def _write_led_status_stub(prefix: Path) -> None:
    init_dir = prefix / "etc" / "init.d"
    init_dir.mkdir(parents=True, exist_ok=True)
    state_file = prefix / "var" / "led-status-state"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    stub = init_dir / "easymanet-led-status"
    stub.write_text(
        f"""#!/bin/sh
state_file="{state_file}"
case "$1" in
  enable) echo enabled >> "$state_file" ;;
  restart) echo restarted >> "$state_file" ;;
  start) echo started >> "$state_file" ;;
esac
"""
    )
    stub.chmod(0o755)


def _write_display_status_stub(prefix: Path) -> None:
    init_dir = prefix / "etc" / "init.d"
    init_dir.mkdir(parents=True, exist_ok=True)
    state_file = prefix / "var" / "display-status-state"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    stub = init_dir / "easymanet-display-status"
    stub.write_text(
        f"""#!/bin/sh
state_file="{state_file}"
case "$1" in
  enable) echo enabled >> "$state_file" ;;
  restart) echo restarted >> "$state_file" ;;
  start) echo started >> "$state_file" ;;
esac
"""
    )
    stub.chmod(0o755)


def _write_network_channel_rewrite_stub(prefix: Path, channel: int) -> None:
    init_dir = prefix / "etc" / "init.d"
    init_dir.mkdir(parents=True, exist_ok=True)
    stub = init_dir / "network"
    stub.write_text(
        f"""#!/bin/sh
case "$1" in
  enable) ;;
  restart) uci set wireless.radio2.channel="{channel}" ;;
esac
"""
    )
    stub.chmod(0o755)


def _write_openmanetd_config_stub(prefix: Path) -> Path:
    config = prefix / "etc" / "openmanetd" / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("meshNetInterface: \"br-ahwlan\"\n")
    return config


def _uci_get(uci_state: Path, key: str, env: dict) -> str:
    result = subprocess.run(
        ["uci", "-q", "get", key],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _bridge_ports(uci_state: Path, bridge_name: str, env: dict) -> set[str]:
    pattern = rf"^network\.([^.=]+)\.name='{re.escape(bridge_name)}'$"
    match = re.search(pattern, uci_state.read_text(), re.MULTILINE)
    assert match, f"missing bridge device {bridge_name} in {uci_state.read_text()}"
    ports = _uci_get(uci_state, f"network.{match.group(1)}.ports", env)
    return set(ports.split())


def _run_provision(
    prefix: Path,
    provision_data: dict,
    uci_state: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    boot_json = prefix / "boot" / "easymanet" / "provision.json"
    boot_json.parent.mkdir(parents=True, exist_ok=True)
    boot_json.write_text(json.dumps(provision_data, indent=2))

    _write_dropbear_stub(prefix)
    network_stub = HARNESS / "network-stub.sh"
    env = _harness_env(uci_state)
    env["EASYMANET_PREFIX"] = str(prefix)
    env["EASYMANET_NETWORK_HELPERS"] = str(network_stub)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["sh", str(PROVISION_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_provision_gate_node_smoke(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.mesh0.device", env) == "radio2"
    assert _uci_get(uci_state, "wireless.mesh0.mesh_id", env) == "test-mesh"
    assert _uci_get(uci_state, "network.bat0.gw_mode", env) == "server"
    assert _uci_get(uci_state, "network.ahwlan.device", env) == "br-ahwlan"
    assert _uci_get(uci_state, "network.ahwlan.ipaddr", env) == "10.41.1.1"
    assert _uci_get(uci_state, "network.ahwlan.dns", env) == "1.1.1.1 8.8.8.8"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0"}
    assert _uci_get(uci_state, "network.meship.ipaddr", env) == ""
    assert _uci_get(uci_state, "network.lan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "eth0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "eth0"
    assert _uci_get(uci_state, "dhcp.ahwlan.interface", env) == "ahwlan"
    assert _uci_get(uci_state, "dhcp.ahwlan.start", env) == "351"
    assert _uci_get(uci_state, "dhcp.ahwlan.limit", env) == "16"
    assert _uci_get(uci_state, "dhcp.ahwlan.leasetime", env) == "12h"
    assert _uci_get(uci_state, "dhcp.ahwlan.ignore", env) == ""
    assert _uci_get(uci_state, "mesh11sd.mesh_params.mesh_gate_announcements", env) == "1"
    assert _uci_get(uci_state, "firewall.mesh_zone.network", env) == "ahwlan"
    assert _uci_get(uci_state, "firewall.mesh_wan_forwarding.src", env) == "mesh"
    assert _uci_get(uci_state, "firewall.mesh_wan_forwarding.dest", env) == "wan"

    dropbear_state = (prefix / "var" / "dropbear-state").read_text()
    assert "enabled" in dropbear_state
    assert "restarted" in dropbear_state

    provisioned = (prefix / "etc" / "easymanet" / "provisioned").read_text()
    assert "hostname: gate01" in provisioned
    assert "role: gate" in provisioned


def test_provision_point_node_disables_ssh(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.bat0.gw_mode", env) == "client"
    assert _uci_get(uci_state, "network.ahwlan.device", env) == "br-ahwlan"
    assert _uci_get(uci_state, "network.ahwlan.ipaddr", env) == "10.41.2.1"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}
    assert _uci_get(uci_state, "dhcp.ahwlan.interface", env) == "ahwlan"
    assert _uci_get(uci_state, "dhcp.ahwlan.ignore", env) == "1"
    assert _uci_get(uci_state, "dhcp.ahwlan.start", env) == ""
    assert _uci_get(uci_state, "dhcp.ahwlan.limit", env) == ""
    assert _uci_get(uci_state, "dhcp.ahwlan.leasetime", env) == ""
    assert _uci_get(uci_state, "mesh11sd.mesh_params.mesh_gate_announcements", env) == "0"
    assert _uci_get(uci_state, "firewall.mesh_zone.network", env) == "ahwlan"
    assert _uci_get(uci_state, "firewall.mesh_wan_forwarding.src", env) == ""

    dropbear_state = (prefix / "var" / "dropbear-state").read_text()
    assert "disabled" in dropbear_state


def test_provision_point_node_disables_stale_mesh_dhcp_pool(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("dhcp.ahwlan=dhcp\n")
        state.write("dhcp.ahwlan.interface='ahwlan'\n")
        state.write("dhcp.ahwlan.start='351'\n")
        state.write("dhcp.ahwlan.limit='16'\n")
        state.write("dhcp.ahwlan.leasetime='12h'\n")

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "dhcp.ahwlan.interface", env) == "ahwlan"
    assert _uci_get(uci_state, "dhcp.ahwlan.ignore", env) == "1"
    assert _uci_get(uci_state, "dhcp.ahwlan.start", env) == ""
    assert _uci_get(uci_state, "dhcp.ahwlan.limit", env) == ""
    assert _uci_get(uci_state, "dhcp.ahwlan.leasetime", env) == ""


def test_provision_local_ap_attaches_to_openmanet_mesh_bridge(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _point_provision_json()
    provision_data["node"]["local_ap"] = {
        "enabled": True,
        "ssid": "point01-local",
        "password": "local-password",
    }

    result = _run_provision(prefix, provision_data, uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.ap0.network", env) == "ahwlan"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}


def test_provision_gate_node_starts_led_status_when_present(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _write_led_status_stub(prefix)

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    led_state = (prefix / "var" / "led-status-state").read_text()
    assert "enabled" in led_state
    assert "restarted" in led_state


def test_provision_point_node_starts_led_status_when_present(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _write_led_status_stub(prefix)

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    led_state = (prefix / "var" / "led-status-state").read_text()
    assert "enabled" in led_state
    assert "restarted" in led_state


def test_provision_missing_led_status_service_is_nonfatal(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "EasyMANET LED status init script not found" in (
        prefix / "var" / "log" / "easymanet.log"
    ).read_text()
    assert "EasyMANET display status init script not found" in (
        prefix / "var" / "log" / "easymanet.log"
    ).read_text()


def test_provision_gate_node_starts_display_status_when_present(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _write_display_status_stub(prefix)

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    display_state = (prefix / "var" / "display-status-state").read_text()
    assert "enabled" in display_state
    assert "restarted" in display_state


def test_provision_non_wifi_gate_exposes_topology_api_on_mesh_only(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _copy_api_overlay(prefix)
    _write_uhttpd_stub(prefix)

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "uhttpd.easymanet_api.home", env).endswith(
        "/www/easymanet-api"
    )
    assert _uci_get(uci_state, "uhttpd.easymanet_api.cgi_prefix", env) == "/v1"
    assert _uci_get(uci_state, "uhttpd.easymanet_api.listen_http", env) == "10.41.1.1:10411"
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.src", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.name", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.proto", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.dest_port", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.target", env) == ""
    assert "restarted" in (prefix / "var" / "uhttpd-state").read_text()


def test_provision_wifi_gate_exposes_topology_api_on_wan(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _copy_api_overlay(prefix)
    _write_uhttpd_stub(prefix)

    result = _run_provision(prefix, _wifi_gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "uhttpd.easymanet_api.listen_http", env) == "0.0.0.0:10411"
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.src", env) == "wan"
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.proto", env) == "tcp"
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.dest_port", env) == "10411"
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.target", env) == "ACCEPT"
    assert "restarted" in (prefix / "var" / "uhttpd-state").read_text()


def test_provision_clears_wifi_gate_wan_api_on_non_wifi_rerun(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _copy_api_overlay(prefix)
    _write_uhttpd_stub(prefix)

    first = _run_provision(prefix, _wifi_gate_provision_json(), uci_state)
    assert first.returncode == 0, first.stderr + first.stdout

    (prefix / "etc" / "easymanet" / "provisioned").unlink()
    second = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert second.returncode == 0, second.stderr + second.stdout

    env = _harness_env(uci_state)
    assert (
        _uci_get(uci_state, "uhttpd.easymanet_api.listen_http", env)
        == "10.41.1.1:10411"
    )
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.src", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.name", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.proto", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.dest_port", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.target", env) == ""


def test_provision_rerun_from_wifi_gate_to_point_clears_stale_wan_state(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    first = _run_provision(prefix, _wifi_gate_provision_json(), uci_state)
    assert first.returncode == 0, first.stderr + first.stdout

    with uci_state.open("a") as state:
        state.write("network.wan6=interface\n")
        state.write("network.wan6.proto='dhcpv6'\n")
        state.write("network.wan6.device='wan0'\n")
        state.write("network.wan6.ifname='wan0'\n")

    (prefix / "etc" / "easymanet" / "provisioned").unlink()
    second = _run_provision(prefix, _point_provision_json(), uci_state)
    assert second.returncode == 0, second.stderr + second.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.wan0.network", env) == ""
    assert _uci_get(uci_state, "wireless.wan0.mode", env) == ""
    assert _uci_get(uci_state, "network.wan.proto", env) == ""
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""
    assert _uci_get(uci_state, "network.wan6.proto", env) == ""
    assert _uci_get(uci_state, "network.wan6.device", env) == ""
    assert _uci_get(uci_state, "network.wan6.ifname", env) == ""
    assert _uci_get(uci_state, "firewall.allow_ssh_wan.src", env) == ""
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.src", env) == ""
    assert _uci_get(uci_state, "dhcp.ahwlan.ignore", env) == "1"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}


@pytest.mark.parametrize(
    "mesh_side_wan",
    ["eth0", "br-lan", "br-ahwlan", "bat0", "mesh", "wlan0"],
)
def test_provision_point_clears_mesh_side_wan_aliases(tmp_path, mesh_side_wan):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("network.wan=interface\n")
        state.write("network.wan.proto='dhcp'\n")
        state.write(f"network.wan.device='{mesh_side_wan}'\n")
        state.write(f"network.wan.ifname='{mesh_side_wan}'\n")
        state.write("network.wan6=interface\n")
        state.write("network.wan6.proto='dhcpv6'\n")
        state.write(f"network.wan6.device='{mesh_side_wan}'\n")
        state.write(f"network.wan6.ifname='{mesh_side_wan}'\n")

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.wan.proto", env) == ""
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""
    assert _uci_get(uci_state, "network.wan6.proto", env) == ""
    assert _uci_get(uci_state, "network.wan6.device", env) == ""
    assert _uci_get(uci_state, "network.wan6.ifname", env) == ""


@pytest.mark.parametrize(
    "mesh_side_wan6",
    ["eth0", "br-lan", "br-ahwlan", "bat0", "mesh", "wlan0"],
)
def test_provision_point_clears_mesh_side_wan6_without_wan(tmp_path, mesh_side_wan6):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("network.wan6=interface\n")
        state.write("network.wan6.proto='dhcpv6'\n")
        state.write(f"network.wan6.device='{mesh_side_wan6}'\n")
        state.write(f"network.wan6.ifname='{mesh_side_wan6}'\n")

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.wan.proto", env) == ""
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""
    assert _uci_get(uci_state, "network.wan6.proto", env) == ""
    assert _uci_get(uci_state, "network.wan6.device", env) == ""
    assert _uci_get(uci_state, "network.wan6.ifname", env) == ""


def test_provision_point_preserves_non_mesh_wan_and_clears_mesh_side_wan6(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("network.wan=interface\n")
        state.write("network.wan.proto='dhcp'\n")
        state.write("network.wan.device='phy1-sta0'\n")
        state.write("network.wan.ifname='phy1-sta0'\n")
        state.write("network.wan6=interface\n")
        state.write("network.wan6.proto='dhcpv6'\n")
        state.write("network.wan6.device='eth0'\n")
        state.write("network.wan6.ifname='eth0'\n")

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "phy1-sta0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "phy1-sta0"
    assert _uci_get(uci_state, "network.wan6.proto", env) == ""
    assert _uci_get(uci_state, "network.wan6.device", env) == ""
    assert _uci_get(uci_state, "network.wan6.ifname", env) == ""


def test_provision_point_preserves_non_mesh_wan_state(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("network.wan=interface\n")
        state.write("network.wan.proto='dhcp'\n")
        state.write("network.wan.device='phy1-sta0'\n")
        state.write("network.wan.ifname='phy1-sta0'\n")
        state.write("network.wan6=interface\n")
        state.write("network.wan6.proto='dhcpv6'\n")
        state.write("network.wan6.device='phy1-sta0'\n")
        state.write("network.wan6.ifname='phy1-sta0'\n")

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "phy1-sta0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "phy1-sta0"
    assert _uci_get(uci_state, "network.wan6.proto", env) == "dhcpv6"
    assert _uci_get(uci_state, "network.wan6.device", env) == "phy1-sta0"
    assert _uci_get(uci_state, "network.wan6.ifname", env) == "phy1-sta0"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}


def test_provision_point_exposes_topology_api_only_on_mesh_ip(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _copy_api_overlay(prefix)
    _write_uhttpd_stub(prefix)

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert (
        _uci_get(uci_state, "uhttpd.easymanet_api.listen_http", env)
        == "10.41.2.1:10411"
    )
    assert _uci_get(uci_state, "firewall.allow_easymanet_api_wan.src", env) == ""


def test_provision_clears_stale_topology_api_when_assets_missing(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    commit_log = tmp_path / "uci-commits.log"
    _seed_wireless_radios(uci_state)
    with uci_state.open("a") as state:
        state.write("uhttpd.easymanet_api=uhttpd\n")
        state.write("uhttpd.easymanet_api.home='/old/easymanet-api'\n")
        state.write("uhttpd.easymanet_api.listen_http='0.0.0.0:10411'\n")
    _write_uhttpd_stub(prefix)

    result = _run_provision(
        prefix,
        _gate_provision_json(),
        uci_state,
        extra_env={"UCI_COMMIT_LOG": str(commit_log)},
    )
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "uhttpd.easymanet_api.home", env) == ""
    assert _uci_get(uci_state, "uhttpd.easymanet_api.listen_http", env) == ""
    assert "uhttpd" in commit_log.read_text().splitlines()
    assert not (prefix / "var" / "uhttpd-state").exists()
    assert "EasyMANET API endpoint wrappers are missing" in (
        prefix / "var" / "log" / "easymanet.log"
    ).read_text()


def test_topology_api_parses_batctl_neighbors_fixture():
    fixture = """
[B.A.T.M.A.N. adv 2023.1, MainIF/MAC: wlan0/c0:bf:be:ef:00:01 (bat0/aa:bb:cc:dd:ee:ff BATMAN_V)]
IF             Neighbor              last-seen
wlan0          bc:2a:33:96:af:68     0.430s (7.1)
"""

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh")],
        input=fixture,
        env={
            **os.environ,
            "EASYMANET_API_TEST_MODE": "parse-neighbors",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "wlan0\tbc:2a:33:96:af:68\t0.430s\t7.1"


def test_topology_api_parses_current_batctl_neighbors_fixture():
    fixture = _fixture_text("batctl-neighbors-current.txt")

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh")],
        input=fixture,
        env={
            **os.environ,
            "EASYMANET_API_TEST_MODE": "parse-neighbors",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "wlan0\tbc:2a:33:96:af:68\t0.090s\t7.2"


def test_topology_api_parses_batctl_originators_fixture():
    fixture = """
[B.A.T.M.A.N. adv 2023.1, MainIF/MAC: wlan0/c0:bf:be:ef:00:01 (bat0/aa:bb:cc:dd:ee:ff BATMAN_V)]
  Originator        last-seen (#/255) Nexthop           [outgoingIF]
  bc:2a:33:96:af:68   0.430s   (255) bc:2a:33:96:af:68 [wlan0]
"""

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh")],
        input=fixture,
        env={
            **os.environ,
            "EASYMANET_API_TEST_MODE": "parse-originators",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == "bc:2a:33:96:af:68\t0.430s\tbc:2a:33:96:af:68\twlan0"
    )


def test_topology_api_parses_current_batctl_originators_fixture():
    fixture = _fixture_text("batctl-originators-current.txt")

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh")],
        input=fixture,
        env={
            **os.environ,
            "EASYMANET_API_TEST_MODE": "parse-originators",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == "bc:2a:33:96:af:68\t0.700s\tbc:2a:33:96:af:68\twlan0"
    )


def _write_status_bins(bin_dir: Path, *, ping_ok: bool = True, neighbors: bool = True) -> None:
    bin_dir.mkdir(exist_ok=True)
    _write_executable(
        bin_dir / "ping",
        "#!/bin/sh\n" + ("exit 0\n" if ping_ok else "exit 1\n"),
    )
    neighbor_body = f"cat {shlex.quote(str(FIXTURES / 'batctl-neighbors-current.txt'))}"
    if not neighbors:
        neighbor_body = "true"
    originator_body = f"cat {shlex.quote(str(FIXTURES / 'batctl-originators-current.txt'))}"
    _write_executable(
        bin_dir / "batctl",
        f"""#!/bin/sh
case "$1" in
  n)
    {neighbor_body}
    ;;
  o)
    {originator_body}
    ;;
esac
""",
    )


def _write_boot_report_bins(bin_dir: Path) -> None:
    bin_dir.mkdir(exist_ok=True)

    def fixture(name: str) -> str:
        return shlex.quote(str(FIXTURES / name))

    _write_executable(bin_dir / "ping", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "dmesg",
        "#!/bin/sh\necho '[    1.0] EasyMANET sim boot'\n",
    )
    _write_executable(
        bin_dir / "logread",
        "#!/bin/sh\necho 'easymanet: provisioning complete'\n",
    )
    _write_executable(
        bin_dir / "ip",
        f"""#!/bin/sh
case "$1" in
  addr) cat {fixture("ip-addr.txt")} ;;
  link) cat {fixture("ip-link.txt")} ;;
  route) cat {fixture("ip-route.txt")} ;;
esac
""",
    )
    _write_executable(
        bin_dir / "brctl",
        f"""#!/bin/sh
case "$1" in
  show) cat {fixture("brctl-show.txt")} ;;
  addif) exit 0 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "batctl",
        f"""#!/bin/sh
case "$1" in
  n) cat {fixture("batctl-neighbors-current.txt")} ;;
  o) cat {fixture("batctl-originators-current.txt")} ;;
  if) echo 'wlan0: active' ;;
esac
""",
    )
    _write_executable(
        bin_dir / "mesh11sd",
        f"""#!/bin/sh
case "$1" in
  status) cat {fixture("mesh11sd-status.txt")} ;;
esac
""",
    )
    _write_executable(
        bin_dir / "wifi",
        f"""#!/bin/sh
case "$1" in
  status) cat {fixture("wifi-status.txt")} ;;
esac
""",
    )
    _write_executable(
        bin_dir / "iw",
        """#!/bin/sh
case "$*" in
  dev) echo 'phy#0'; echo 'Interface wlan0' ;;
  'dev wlan0 info') echo 'type mesh point' ;;
  'dev wlan0 station dump') echo 'Station bc:2a:33:96:af:68 (on wlan0)' ;;
  'dev wlan0 mpath dump') echo 'DEST ADDR         NEXT HOP' ;;
esac
""",
    )
    _write_executable(bin_dir / "ps", "#!/bin/sh\necho '1234 root openmanetd'\n")
    _write_executable(
        bin_dir / "mount",
        "#!/bin/sh\necho '/dev/root on / type squashfs (ro)'\n",
    )


def _status_env(tmp_path: Path, provision_data: dict, *, ping_ok: bool = True, neighbors: bool = True) -> dict:
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(provision_data))
    provisioned = tmp_path / "provisioned"
    provisioned.write_text("provisioned\n")
    bin_dir = tmp_path / "bin"
    _write_status_bins(bin_dir, ping_ok=ping_ok, neighbors=neighbors)
    api_home = tmp_path / "www" / "easymanet-api"
    (api_home / "v1").mkdir(parents=True)
    status_wrapper = api_home / "v1" / "status"
    status_wrapper.write_text("#!/bin/sh\n")
    status_wrapper.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{HARNESS}:{os.environ.get('PATH', '')}",
        "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        "EASYMANET_PROVISION_JSON": str(provision_json),
        "EASYMANET_PROVISIONED_FLAG": str(provisioned),
        "EASYMANET_API_HOME": str(api_home),
        "EASYMANET_API_SCRIPT": str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"),
        "UCI_STATE_FILE": str(tmp_path / "uci-state"),
    }


def _copy_status_lib_dir(tmp_path: Path) -> Path:
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    for name in ("provision-lib.sh", "api-lib.sh", "status-lib.sh"):
        shutil.copy2(OVERLAY / "usr" / "lib" / "easymanet" / name, lib_dir / name)
    return lib_dir


def test_status_api_reports_point_node_local_status(tmp_path):
    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "status"],
        env=_status_env(tmp_path, _point_provision_json()),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["support_code"] == "EM-OK"
    assert payload["node"]["name"] == "point01"
    assert payload["mesh"]["neighbor_count"] == 1
    assert payload["internet"]["ok"] is True
    assert payload["manageability"]["ok"] is True
    assert payload["fleet"] == []


def test_status_helper_failure_does_not_break_identity_endpoint(tmp_path):
    lib_dir = _copy_status_lib_dir(tmp_path)
    (lib_dir / "status-lib.sh").write_text("this is not valid shell syntax (\n")
    env = _status_env(tmp_path, _point_provision_json())
    env["EASYMANET_LIB_DIR"] = str(lib_dir)

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "identity"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["node"]["name"] == "point01"


def test_status_helper_failure_returns_status_fallback(tmp_path):
    lib_dir = _copy_status_lib_dir(tmp_path)
    (lib_dir / "status-lib.sh").write_text("this is not valid shell syntax (\n")
    env = _status_env(tmp_path, _point_provision_json())
    env["EASYMANET_LIB_DIR"] = str(lib_dir)

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["support_code"] == "EM-DIAG-PARTIAL"
    assert "status endpoint failed" in payload["warnings"][0]


def test_status_api_reports_public_internet_down(tmp_path):
    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "status"],
        env=_status_env(tmp_path, _point_provision_json(), ping_ok=False),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["support_code"] == "EM-INET-DOWN"
    assert payload["internet"]["ok"] is False


def test_status_api_reports_gateway_missing_fleet_node(tmp_path):
    provision_data = _gate_provision_json()
    provision_data["fleet"] = {
        "nodes": [
            {"name": "gate01", "hostname": "gate01", "role": "gate", "target": "rpi4-mm6108-spi", "ip": "10.41.1.1"},
            {"name": "point01", "hostname": "point01", "role": "point", "target": "rpi4-mm6108-spi", "ip": "10.41.2.1"},
        ]
    }
    bin_dir = tmp_path / "bin"
    env = _status_env(tmp_path, provision_data)
    (bin_dir / "uclient-fetch").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "uclient-fetch").chmod(0o755)

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["support_code"] == "EM-NODE-MISSING"
    assert {"name": "point01", "status": "MISSING"} in payload["fleet"]
    assert {"name": "gate01", "status": "OK"} in payload["fleet"]


def test_status_api_reports_gateway_unknown_fleet_when_topology_fails(tmp_path):
    provision_data = _gate_provision_json()
    provision_data["fleet"] = {
        "nodes": [
            {"name": "gate01", "hostname": "gate01", "role": "gate", "target": "rpi4-mm6108-spi", "ip": "10.41.1.1"},
            {"name": "point01", "hostname": "point01", "role": "point", "target": "rpi4-mm6108-spi", "ip": "10.41.2.1"},
        ]
    }
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(provision_data))
    script = f'''
PROVISION_JSON="{provision_json}"
SCRIPT_DIR="{OVERLAY / "usr" / "lib" / "easymanet"}"
. "$SCRIPT_DIR/provision-lib.sh"
. "$SCRIPT_DIR/api-lib.sh"
. "$SCRIPT_DIR/status-lib.sh"
is_gateway() {{ return 0; }}
topology_json_body() {{ printf '%s\\n' '{{"ok":false}}'; }}
status_fleet_json "{tmp_path / "missing.txt"}"
'''

    result = subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "PATH": f"{HARNESS}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert {"name": "gate01", "status": "UNKNOWN"} in payload
    assert {"name": "point01", "status": "UNKNOWN"} in payload


def test_display_status_renders_copyable_console_text(tmp_path):
    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "display-status.sh"), "--once"],
        env=_status_env(tmp_path, _point_provision_json()),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "point01" in result.stdout
    assert "MESH" in result.stdout
    assert "CODE" in result.stdout


def test_status_memory_text_formats_meminfo_kib_as_mib(tmp_path):
    result = subprocess.run(
        [
            "sh",
            "-c",
            f'. "{OVERLAY / "usr" / "lib" / "easymanet" / "status-lib.sh"}"; memory_text 1048576 262144',
        ],
        env=_status_env(tmp_path, _point_provision_json()),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "768/1024MiB (75%)"


def test_boot_report_status_generation_failure_is_nonfatal(tmp_path):
    lib_dir = _copy_status_lib_dir(tmp_path)
    (lib_dir / "status-lib.sh").write_text("this is not valid shell syntax (\n")
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_point_provision_json()))

    script = f'''
EASYMANET_LIB_DIR="{lib_dir}"
EASYMANET_PROVISION_JSON="{provision_json}"
. "{OVERLAY / "usr" / "lib" / "easymanet" / "boot-report.sh"}"
write_easymanet_status_json
'''
    result = subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "PATH": f"{HARNESS}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["support_code"] == "EM-DIAG-PARTIAL"
    assert "boot report collection continued" in payload["warnings"][0]


def test_boot_report_generates_openwrt_fixture_outputs(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_gate_provision_json()))
    provisioned = tmp_path / "provisioned"
    provisioned.write_text("provisioned\n")
    uci_state = tmp_path / "uci-state"
    uci_state.write_text(_fixture_text("uci-openwrt-state.txt"))

    api_home = tmp_path / "www" / "easymanet-api"
    (api_home / "v1").mkdir(parents=True)
    status_wrapper = api_home / "v1" / "status"
    status_wrapper.write_text("#!/bin/sh\n")
    status_wrapper.chmod(0o755)

    bin_dir = tmp_path / "bin"
    _write_boot_report_bins(bin_dir)
    report_dir = tmp_path / "boot" / "easymanet"
    report_dir.mkdir(parents=True)

    script = f'''
. "{OVERLAY / "usr" / "lib" / "easymanet" / "boot-report.sh"}"
find_boot_report_dir() {{
  printf '%s' "{report_dir}"
}}
write_easymanet_boot_report openwrt-sim
'''
    result = subprocess.run(
        ["sh", "-c", script],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{HARNESS}:{os.environ.get('PATH', '')}",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_PROVISIONED_FLAG": str(provisioned),
            "EASYMANET_API_HOME": str(api_home),
            "EASYMANET_API_SCRIPT": str(
                OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"
            ),
            "UCI_STATE_FILE": str(uci_state),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    latest = report_dir / "boot-report-latest"
    assert "reason=openwrt-sim" in (latest / "summary.txt").read_text()
    assert "br-ahwlan" in (latest / "ip-addr.txt").read_text()
    assert "bc:2a:33:96:af:68" in (latest / "batctl-neighbors.txt").read_text()
    assert "wlan0: active" in (latest / "batctl-ifaces.txt").read_text()
    assert "mesh11sd running" in (latest / "mesh11sd-status.txt").read_text()
    assert "priv_key_pwd='<redacted>'" in (latest / "uci-mesh11sd.txt").read_text()
    status = json.loads((latest / "status.json").read_text())
    assert status["support_code"] == "EM-OK"
    assert status["mesh"]["neighbor_count"] == 1


def test_topology_api_escapes_control_characters_in_json_string():
    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh")],
        input='gate"\n\t\r01',
        env={
            **os.environ,
            "EASYMANET_API_TEST_MODE": "json-escape",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == 'gate"\n\t\r01'


def test_topology_api_reports_scratch_dir_creation_failure(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_gate_provision_json()))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "mktemp").write_text(
        """#!/bin/sh
exit 1
"""
    )
    (bin_dir / "mktemp").chmod(0o755)

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "topology"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{HARNESS}:{os.environ.get('PATH', '')}",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "UCI_STATE_FILE": str(tmp_path / "uci-state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["code"] == "scratch_init_failed"
    assert "scratch directory" in payload["errors"][0]


def test_topology_api_bounds_offline_peer_probes(tmp_path):
    provision_data = _gate_provision_json()
    provision_data["fleet"] = {
        "nodes": [
            {
                "name": "gate01",
                "hostname": "gate01",
                "role": "gate",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.1.1",
            },
            {
                "name": "point01",
                "hostname": "point01",
                "role": "point",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.2.1",
            },
            {
                "name": "point02",
                "hostname": "point02",
                "role": "point",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.3.1",
            },
        ]
    }
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(provision_data))
    request_log = tmp_path / "requests.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uclient-fetch").write_text(
        f"""#!/bin/sh
echo "$*" >> "{request_log}"
exit 1
"""
    )
    (bin_dir / "uclient-fetch").chmod(0o755)

    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "topology"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{HARNESS}:{os.environ.get('PATH', '')}",
            "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_API_MAX_TOPOLOGY_PEER_PROBES": "1",
            "UCI_STATE_FILE": str(tmp_path / "uci-state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert request_log.read_text().count("/v1/identity") == 1
    assert "point01 did not answer topology API at 10.41.2.1" in payload["warnings"]
    assert "point02 skipped after topology probe limit (1)" in payload["warnings"]


def test_topology_api_does_not_skip_peer_after_self_neighbors(tmp_path):
    provision_data = _gate_provision_json()
    provision_data["fleet"] = {
        "nodes": [
            {
                "name": "gate01",
                "hostname": "gate01",
                "role": "gate",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.1.1",
            },
            {
                "name": "point01",
                "hostname": "point01",
                "role": "point",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.2.1",
            },
            {
                "name": "point02",
                "hostname": "point02",
                "role": "point",
                "target": "rpi4-mm6108-spi",
                "ip": "10.41.3.1",
            },
        ]
    }
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(provision_data))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uci").write_text(
        """#!/bin/sh
if [ "$1" = "-q" ] && [ "$2" = "get" ] && [ "$3" = "wireless.mesh0.ifname" ]; then
  echo wlan0
fi
"""
    )
    (bin_dir / "uci").chmod(0o755)
    (bin_dir / "batctl").write_text(
        """#!/bin/sh
case "$1" in
  n)
    cat <<'EOF'
[B.A.T.M.A.N. adv 2025.4-openwrt-2, MainIF/MAC: wlan0/bc:2a:33:96:af:2a (bat0/9a:55:24:91:92:4a BATMAN_V)]
         Neighbor   last-seen      speed           IF
bc:2a:33:96:af:68    0.090s (        7.2) [     wlan0]
EOF
    ;;
  o)
    cat <<'EOF'
[B.A.T.M.A.N. adv 2025.4-openwrt-2, MainIF/MAC: wlan0/bc:2a:33:96:af:2a (bat0/9a:55:24:91:92:4a BATMAN_V)]
   Originator        last-seen ( throughput)  Nexthop           [outgoingIF]
 * bc:2a:33:96:af:68    0.700s (        7.2)  bc:2a:33:96:af:68 [     wlan0]
EOF
    ;;
esac
"""
    )
    (bin_dir / "batctl").chmod(0o755)
    (bin_dir / "uclient-fetch").write_text(
        """#!/bin/sh
url=""
for arg in "$@"; do
  url="$arg"
done
case "$url" in
  http://10.41.2.1:10411/v1/identity)
    cat <<'EOF'
{"ok":true,"generated_at":"2026-06-13T19:44:38Z","node":{"name":"point01","hostname":"point01","role":"point","target":"rpi4-mm6108-spi","ip":"10.41.2.1"},"interfaces":{"bat0_mac":"ba:31:cf:08:e3:24","mesh_iface":"wlan0","mesh_mac":"bc:2a:33:96:af:68"},"api":{"version":1,"port":10411}}
EOF
    ;;
  http://10.41.2.1:10411/v1/neighbors)
    cat <<'EOF'
{"ok":true,"generated_at":"2026-06-13T19:44:47Z","node":{"name":"point01","hostname":"point01","role":"point","target":"rpi4-mm6108-spi","ip":"10.41.2.1"},"interfaces":{"bat0_mac":"ba:31:cf:08:e3:24","mesh_iface":"wlan0","mesh_mac":"bc:2a:33:96:af:68"},"neighbors":[{"iface":"bc:2a:33:96:af:2a","mac":"0.430s","last_seen":" 7.2 [ wlan0]","throughput":""}],"originators":[]}
EOF
    ;;
  *)
    exit 1
    ;;
esac
"""
    )
    (bin_dir / "uclient-fetch").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{HARNESS}:{os.environ.get('PATH', '')}",
        "EASYMANET_LIB_DIR": str(OVERLAY / "usr" / "lib" / "easymanet"),
        "EASYMANET_PROVISION_JSON": str(provision_json),
        "EASYMANET_API_FETCH_TIMEOUT": "1",
    }
    result = subprocess.run(
        ["sh", str(OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"), "topology"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [node["name"] for node in payload["nodes"]] == [
        "gate01",
        "point01",
        "point02",
    ]
    point01 = next(node for node in payload["nodes"] if node["name"] == "point01")
    point02 = next(node for node in payload["nodes"] if node["name"] == "point02")
    assert point01["status"] == "online"
    assert point01["mesh_mac"] == "bc:2a:33:96:af:68"
    assert point02["status"] == "offline"
    assert point02["mesh_mac"] == ""

    assert {
        "source": "gate01",
        "target": "point01",
        "source_mac": "",
        "target_mac": "bc:2a:33:96:af:68",
        "iface": "wlan0",
        "last_seen": "0.090s",
        "throughput": "7.2",
        "status": "resolved",
    } in payload["links"]
    assert all(link["target_mac"] != "0.430s" for link in payload["links"])
    assert "point02 did not answer topology API at 10.41.3.1" in payload["warnings"]


def test_provision_writes_valid_root_password_hash(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    provision_data["management"]["root_password_hash"] = "$6$abc/DEF.123"

    result = _run_provision(prefix, provision_data, uci_state)

    assert result.returncode == 0, result.stderr + result.stdout
    assert "root:$6$abc/DEF.123:19000:0:99999:7:::" in (
        prefix / "etc" / "shadow"
    ).read_text()


def test_provision_rejects_invalid_root_password_hash_characters(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    provision_data["management"]["root_password_hash"] = "$6$bad:shadow"

    result = _run_provision(prefix, provision_data, uci_state)

    assert result.returncode != 0
    assert "failed to set root password hash in /etc/shadow" in result.stdout
    assert "Invalid root password hash characters" in (
        prefix / "var" / "log" / "easymanet.log"
    ).read_text()


def test_provision_reapplies_mesh_channel_after_network_restart(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    _write_network_channel_rewrite_stub(prefix, 36)
    provision_data = _point_provision_json()

    result = _run_provision(prefix, provision_data, uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.radio2.channel", env) == "42"
    assert _uci_get(uci_state, "wireless.radio2.s1g_chanbw", env) == "2"


def test_provision_sets_openmanetd_mesh_interface_to_brahwlan(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    config = _write_openmanetd_config_stub(prefix)

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    text = config.read_text()
    assert 'meshNetInterface: "br-ahwlan"' in text
    assert 'role: "point"' in text
    assert 'ip: "10.41.2.1"' in text
    assert (config.stat().st_mode & 0o777) == 0o600


def test_late_management_lan_repair_sources_lib_from_explicit_dir(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_point_provision_json(), indent=2))
    uci_state = tmp_path / "uci-state"
    uci_state.write_text(
        "\n".join(
            [
                "network.@device[0].name='br-lan'",
                "network.@device[0].type='bridge'",
                "network.wan=interface",
                "network.wan.device='phy1-sta0'",
            ]
        )
        + "\n"
    )
    env = _harness_env(
        uci_state,
        {
            "EASYMANET_LIB_DIR": str(NETWORK_SCRIPT.parent),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_NETWORK_LOG": str(tmp_path / "network.log"),
        },
    )

    result = _run_sh(
        f'''
cd "{tmp_path}"
. "{NETWORK_SCRIPT}"
easymanet_repair_management_lan late-boot
''',
        env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _uci_get(uci_state, "network.ahwlan.device", env) == "br-ahwlan"
    assert _uci_get(uci_state, "network.ahwlan.ipaddr", env) == "10.41.2.1"
    assert _uci_get(uci_state, "network.lan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.device", env) == "phy1-sta0"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}
    assert "name='br-lan'" not in uci_state.read_text()


def test_late_management_lan_repair_repairs_stale_brlan_wan_to_eth0(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_gate_provision_json(), indent=2))
    uci_state = tmp_path / "uci-state"
    uci_state.write_text(
        "\n".join(
            [
                "network.@device[0].name='br-lan'",
                "network.@device[0].type='bridge'",
                "network.wan=interface",
                "network.wan.proto='dhcp'",
                "network.wan.device='br-lan'",
                "network.wan.ifname='br-lan'",
            ]
        )
        + "\n"
    )
    env = _harness_env(
        uci_state,
        {
            "EASYMANET_LIB_DIR": str(NETWORK_SCRIPT.parent),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_NETWORK_LOG": str(tmp_path / "network.log"),
        },
    )

    result = _run_sh(
        f'''
cd "{tmp_path}"
. "{NETWORK_SCRIPT}"
easymanet_repair_management_lan late-boot
''',
        env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _uci_get(uci_state, "network.wan.device", env) == "eth0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "eth0"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0"}
    assert "name='br-lan'" not in uci_state.read_text()


def test_late_management_lan_repair_restores_usb0_wan_from_stale_brlan_wan(tmp_path):
    provision_data = _gate_provision_json()
    provision_data["node"]["gateway"] = {
        "enabled": True,
        "uplink_interface": "usb0",
    }
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(provision_data, indent=2))
    uci_state = tmp_path / "uci-state"
    uci_state.write_text(
        "\n".join(
            [
                "network.@device[0].name='br-lan'",
                "network.@device[0].type='bridge'",
                "network.wan=interface",
                "network.wan.proto='dhcp'",
                "network.wan.device='br-lan'",
                "network.wan.ifname='br-lan'",
            ]
        )
        + "\n"
    )
    env = _harness_env(
        uci_state,
        {
            "EASYMANET_LIB_DIR": str(NETWORK_SCRIPT.parent),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_NETWORK_LOG": str(tmp_path / "network.log"),
        },
    )

    result = _run_sh(
        f'''
cd "{tmp_path}"
. "{NETWORK_SCRIPT}"
easymanet_repair_management_lan late-boot
''',
        env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "usb0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "usb0"
    assert _uci_get(uci_state, "network.wan.peerdns", env) == "0"
    assert _uci_get(uci_state, "network.wan.dns", env) == "1.1.1.1 8.8.8.8"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}
    assert "name='br-lan'" not in uci_state.read_text()


def test_late_management_lan_repair_restores_wifi_wan_from_stale_brlan_wan(tmp_path):
    provision_json = tmp_path / "provision.json"
    provision_json.write_text(json.dumps(_wifi_gate_provision_json(), indent=2))
    uci_state = tmp_path / "uci-state"
    uci_state.write_text(
        "\n".join(
            [
                "network.@device[0].name='br-lan'",
                "network.@device[0].type='bridge'",
                "network.wan=interface",
                "network.wan.proto='dhcp'",
                "network.wan.device='br-lan'",
                "network.wan.ifname='br-lan'",
            ]
        )
        + "\n"
    )
    env = _harness_env(
        uci_state,
        {
            "EASYMANET_LIB_DIR": str(NETWORK_SCRIPT.parent),
            "EASYMANET_PROVISION_JSON": str(provision_json),
            "EASYMANET_NETWORK_LOG": str(tmp_path / "network.log"),
        },
    )

    result = _run_sh(
        f'''
cd "{tmp_path}"
. "{NETWORK_SCRIPT}"
easymanet_repair_management_lan late-boot
''',
        env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""
    assert _uci_get(uci_state, "network.wan.peerdns", env) == "0"
    assert _uci_get(uci_state, "network.wan.dns", env) == "1.1.1.1 8.8.8.8"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}
    assert "name='br-lan'" not in uci_state.read_text()


def test_provision_non_eth0_uplink_configures_wan(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    provision_data["node"]["gateway"] = {
        "enabled": True,
        "uplink_interface": "usb0",
    }

    result = _run_provision(prefix, provision_data, uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "usb0"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "usb0"
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}


def test_provision_wifi_uplink_keeps_wan_on_wifi_sta_path(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    result = _run_provision(prefix, _wifi_gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.wan0.network", env) == "wan"
    assert _uci_get(uci_state, "wireless.wan0.mode", env) == "sta"
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""
    assert _bridge_ports(uci_state, "br-ahwlan", env) == {"bat0", "eth0"}


def test_provision_removes_boot_json_after_success(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    boot_json = prefix / "boot" / "easymanet" / "provision.json"

    result = _run_provision(prefix, _gate_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout
    assert not boot_json.exists()

    overlay_json = prefix / "etc" / "easymanet" / "provision.json"
    assert overlay_json.exists()
    assert (overlay_json.stat().st_mode & 0o777) == 0o600


def test_provision_requires_node_ip(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    del provision_data["node"]["ip"]

    result = _run_provision(prefix, provision_data, uci_state)

    assert result.returncode != 0
    assert "missing required provision.json fields: node.ip" in result.stdout


@pytest.mark.parametrize(
    ("field_path", "expected"),
    [
        (("version",), "version"),
        (("mesh", "id"), "mesh.id"),
        (("mesh", "password"), "mesh.password"),
        (("mesh", "channel"), "mesh.channel"),
        (("mesh", "bandwidth_mhz"), "mesh.bandwidth_mhz"),
        (("mesh", "country"), "mesh.country"),
        (("node", "hostname"), "node.hostname"),
        (("node", "role"), "node.role"),
        (("node", "ip"), "node.ip"),
        (("node", "target"), "node.target"),
    ],
)
def test_provision_reports_missing_required_fields(tmp_path, field_path, expected):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    _delete_nested(provision_data, field_path)

    result = _run_provision(prefix, provision_data, uci_state)

    assert result.returncode != 0
    assert "missing required provision.json fields:" in result.stdout
    assert expected in result.stdout


@pytest.mark.parametrize(
    ("field_path", "value", "expected"),
    [
        (("version",), 2, "unsupported provision.json version"),
        (("node", "role"), "relay", "unsupported node.role"),
        (("node", "target"), "rpi5", "unsupported node.target"),
        (("mesh", "bandwidth_mhz"), 3, "unsupported mesh.bandwidth_mhz"),
        (("mesh", "bandwidth_mhz"), 4, "rpi4-mm6108-spi in US requires"),
        (("mesh", "channel"), 36, "rpi4-mm6108-spi in US requires"),
        (("mesh", "channel"), 0, "rpi4-mm6108-spi in US requires"),
        (("mesh", "channel"), "abc", "mesh.channel must be numeric"),
    ],
)
def test_provision_rejects_invalid_required_values(tmp_path, field_path, value, expected):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    _set_nested(provision_data, field_path, value)

    result = _run_provision(prefix, provision_data, uci_state)

    assert result.returncode != 0
    assert expected in result.stdout


def _delete_nested(data: dict, field_path: tuple[str, ...]) -> None:
    current = data
    for part in field_path[:-1]:
        current = current[part]
    del current[field_path[-1]]


def _set_nested(data: dict, field_path: tuple[str, ...], value: object) -> None:
    current = data
    for part in field_path[:-1]:
        current = current[part]
    current[field_path[-1]] = value


def test_em_mesh_encryption_override_propagates_to_uci(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)

    result = _run_provision(
        prefix,
        _gate_provision_json(),
        uci_state,
        extra_env={"EM_MESH_ENCRYPTION": "psk2"},
    )
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.mesh0.encryption", env) == "psk2"
