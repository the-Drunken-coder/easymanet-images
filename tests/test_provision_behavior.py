"""Behavior tests for first-boot provisioning shell scripts."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "images" / "openmanet" / "provisioning" / "openwrt-overlay"
PROVISION_LIB = OVERLAY / "usr" / "lib" / "easymanet" / "provision-lib.sh"
PROVISION_SCRIPT = OVERLAY / "usr" / "lib" / "easymanet" / "provision.sh"
NETWORK_SCRIPT = OVERLAY / "usr" / "lib" / "easymanet" / "network.sh"
HARNESS = Path(__file__).resolve().parent / "shell_harness"


def _harness_env(uci_state: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{HARNESS}:{env.get('PATH', '')}"
    env["UCI_STATE_FILE"] = str(uci_state)
    if extra:
        env.update(extra)
    return env


def _seed_wireless_radios(uci_state: Path) -> None:
    lines = [
        "wireless.radio2.type='morse'",
        "wireless.radio0.type='mac80211'",
        "wireless.radio3.type='mac80211'",
        "wireless.radio3.path='platform/soc/fe300000.mmcnr/mmc_host/mmc1/mmc1:0001/mmc1:0001:1'",
        "wireless.radio3.band='2g'",
    ]
    uci_state.write_text("\n".join(lines) + "\n")


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
    assert _uci_get(uci_state, "network.meship.ipaddr", env) == "10.41.1.1"
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == "br-lan"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "br-lan"
    assert _uci_get(uci_state, "mesh11sd.mesh_params.mesh_gate_announcements", env) == "1"

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
    assert _uci_get(uci_state, "network.meship.ipaddr", env) == "10.41.2.1"
    assert _uci_get(uci_state, "mesh11sd.mesh_params.mesh_gate_announcements", env) == "0"

    dropbear_state = (prefix / "var" / "dropbear-state").read_text()
    assert "disabled" in dropbear_state


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
    _write_network_channel_rewrite_stub(prefix, 42)
    provision_data = _point_provision_json()
    provision_data["mesh"]["channel"] = 41
    provision_data["mesh"]["bandwidth_mhz"] = 1

    result = _run_provision(prefix, provision_data, uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.radio2.channel", env) == "41"
    assert _uci_get(uci_state, "wireless.radio2.s1g_chanbw", env) == "1"


def test_provision_sets_openmanetd_mesh_interface_to_bat0(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    config = _write_openmanetd_config_stub(prefix)

    result = _run_provision(prefix, _point_provision_json(), uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    text = config.read_text()
    assert 'meshNetInterface: "bat0"' in text
    assert 'role: "point"' in text
    assert 'ip: "10.41.2.1"' in text


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
    assert _uci_get(uci_state, "network.lan.device", env) == "br-lan"
    assert _uci_get(uci_state, "network.lan.ipaddr", env) == "10.41.254.1"
    assert "network.@device[0].ports='eth0'" in uci_state.read_text()


def test_late_management_lan_repair_preserves_shared_brlan_wan(tmp_path):
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
    assert _uci_get(uci_state, "network.wan.device", env) == "br-lan"
    assert _uci_get(uci_state, "network.wan.ifname", env) == "br-lan"
    assert "network.@device[0].ports='eth0'" in uci_state.read_text()


def test_provision_wifi_uplink_keeps_wan_on_wifi_sta_path(tmp_path):
    prefix = tmp_path / "root"
    uci_state = tmp_path / "uci-state"
    _seed_wireless_radios(uci_state)
    provision_data = _gate_provision_json()
    provision_data["node"]["gateway"] = {
        "enabled": True,
        "uplink_interface": "wifi",
        "wifi": {
            "enabled": True,
            "ssid": "upstream",
            "password": "upstream-password",
            "encryption": "psk2",
        },
    }

    result = _run_provision(prefix, provision_data, uci_state)
    assert result.returncode == 0, result.stderr + result.stdout

    env = _harness_env(uci_state)
    assert _uci_get(uci_state, "wireless.wan0.network", env) == "wan"
    assert _uci_get(uci_state, "wireless.wan0.mode", env) == "sta"
    assert _uci_get(uci_state, "network.wan.proto", env) == "dhcp"
    assert _uci_get(uci_state, "network.wan.device", env) == ""
    assert _uci_get(uci_state, "network.wan.ifname", env) == ""


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
    assert "missing required mesh/node fields" in result.stdout


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
