from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "images" / "openmanet" / "provisioning" / "openwrt-overlay"
PROVISION_SCRIPT = OVERLAY / "usr" / "lib" / "easymanet" / "provision.sh"
PROVISION_LIB = OVERLAY / "usr" / "lib" / "easymanet" / "provision-lib.sh"
PROVISION_RUNTIME = OVERLAY / "usr" / "lib" / "easymanet" / "provision-runtime.sh"
API_LIB = OVERLAY / "usr" / "lib" / "easymanet" / "api-lib.sh"


def test_provision_lib_exists_and_is_sourced():
    assert PROVISION_LIB.exists()
    assert PROVISION_RUNTIME.exists()
    text = PROVISION_SCRIPT.read_text()
    assert "provision-lib.sh" in text
    assert "provision-runtime.sh" in text


def test_firstboot_provisioner_uses_openwrt_jsonfilter_not_python():
    text = PROVISION_SCRIPT.read_text()
    assert "jsonfilter" in text
    assert "python3" not in text


def test_firstboot_supports_easymanet_prefix_for_sandboxed_runs():
    text = PROVISION_SCRIPT.read_text()
    assert 'EASYMANET_PREFIX:=' in text
    assert "_prefix_path" in text


def test_firstboot_fails_if_root_password_hash_not_applied():
    text = PROVISION_SCRIPT.read_text()
    assert "failed to set root password hash in /etc/shadow" in text
    shadow_block = text.split("Setting root password hash")[1].split("Configuring mesh")[0]
    assert 'write_root_shadow_hash "$shadow_path" "$ROOT_PW_HASH"' in shadow_block
    assert "shadow_path" in shadow_block
    assert "failed to set root password hash" in shadow_block
    assert "sed -i" not in shadow_block
    assert "|| true" not in shadow_block.split("write_root_shadow_hash")[1].split("fi")[0]


def test_firstboot_creates_shadow_temp_file_with_restrictive_umask():
    text = PROVISION_RUNTIME.read_text()
    write_block = text.split("write_root_shadow_hash() {", 1)[1].split(
        "wipe_boot_provision_json() {", 1
    )[0]

    assert 'old_umask="$(umask)"' in write_block
    assert "umask 077" in write_block
    assert ': > "$tmp_path"' in write_block
    assert 'umask "$old_umask"' in write_block
    assert write_block.index("umask 077") < write_block.index(': > "$tmp_path"')
    assert write_block.index(': > "$tmp_path"') < write_block.index('umask "$old_umask"')


def test_uci_defaults_propagates_provision_failure_rc():
    defaults = OVERLAY / "etc" / "uci-defaults" / "99-easymanet"
    text = defaults.read_text()
    assert "set -eu" in text
    assert "set +e" in text
    assert '/bin/sh "$PROVISION_SCRIPT"' in text
    assert "rc=$?" in text
    assert "set -e" in text.split("rc=$?")[1]
    assert 'if [ "$rc" -eq 0 ]; then' in text
    assert 'if /bin/sh "$PROVISION_SCRIPT"; then' not in text
    assert "provisioning failed with rc=" in text
    assert 'exit "$rc"' in text


def test_firstboot_honors_ssh_enabled_flag():
    text = PROVISION_SCRIPT.read_text()
    assert "SSH_ENABLED=0" in text
    assert 'if [ -n "$(json_val management ssh_enabled)" ]; then' in text
    assert 'json_bool management ssh_enabled && SSH_ENABLED=1' in text
    assert 'elif [ "$NODE_ROLE" = "gate" ]; then' in text
    assert 'dropbear_init=' in text
    assert '"$dropbear_init" enable' in text
    assert '"$dropbear_init" restart' in text
    assert '"$dropbear_init" start' in text
    assert '"$dropbear_init" disable' in text


def test_firstboot_temp_boot_mount_is_writable_for_payload_removal():
    text = PROVISION_RUNTIME.read_text()
    assert 'mount -t vfat "$dev" "$BOOT_MOUNT_TMP"' in text
    assert 'mount -o ro -t vfat "$dev" "$BOOT_MOUNT_TMP"' not in text
    wipe_block = text.split("wipe_boot_provision_json() {", 1)[1].split("}", 1)[0]
    assert '"$BOOT_JSON"' in wipe_block
    assert 'if [ "$BOOT_MOUNTED_TMP" -eq 1 ]' not in wipe_block


def test_firstboot_requires_node_ip():
    text = PROVISION_SCRIPT.read_text()
    required_block = text.split('missing_fields=""', 1)[1].split('if [ -n "$missing_fields" ]', 1)[0]
    assert '[ -n "$NODE_IP" ] || missing_fields="$missing_fields node.ip"' in required_block


def test_firstboot_does_not_auto_reboot_after_provisioning():
    text = PROVISION_SCRIPT.read_text()
    assert "( sleep 5; reboot )" not in text
    assert "reboot ) &" not in text


def test_management_lan_repair_hook_is_packaged_and_enabled():
    helper = OVERLAY / "usr" / "lib" / "easymanet" / "network.sh"
    init = OVERLAY / "etc" / "init.d" / "easymanet-management-lan"
    defaults = OVERLAY / "etc" / "uci-defaults" / "97-easymanet-management-lan"

    assert helper.exists()
    assert init.exists()
    assert defaults.exists()
    helper_text = helper.read_text()
    assert "provision-lib.sh" in helper_text
    assert "easymanet_repair_management_lan" in helper_text
    assert "uci -q delete network.wan" in helper_text
    assert "brctl addif" in helper_text
    assert "br-ahwlan" in helper_text
    init_text = init.read_text()
    assert "sleep 25" in init_text
    assert "EASYMANET_LIB_DIR=/usr/lib/easymanet" in init_text
    assert "write_easymanet_boot_report post-management-lan" in init_text
    assert "/etc/init.d/easymanet-management-lan enable" in defaults.read_text()


def test_boot_report_hook_is_packaged_and_enabled():
    report = OVERLAY / "usr" / "lib" / "easymanet" / "boot-report.sh"
    init = OVERLAY / "etc" / "init.d" / "easymanet-boot-report"
    defaults = OVERLAY / "etc" / "uci-defaults" / "98-easymanet-boot-report"

    assert report.exists()
    assert init.exists()
    assert defaults.exists()
    report_text = report.read_text()
    assert "write_easymanet_boot_report" in report_text
    assert "boot-report-latest" in report_text
    assert "easymanet_redact_uci_wireless" in report_text
    assert "easymanet_redact_uci_mesh11sd" in report_text
    secret_fields = next(
        line for line in report_text.splitlines() if line.startswith("MESH11SD_SECRET_FIELDS=")
    )
    assert "priv_key_pwd" in secret_fields
    assert "priv_key_pwd" in report_text
    assert "auto_mesh_id" in report_text
    assert "auto_mesh_key" in report_text
    assert "mesh_gate_key" in report_text
    assert "vtun_gate_key" in report_text
    assert 'run_report_cmd "$latest/uci-mesh11sd.txt" easymanet_redact_uci_mesh11sd' in report_text
    assert 'easymanet_redact_config_mesh11sd > "$latest/config-mesh11sd"' in report_text
    assert 'write_easymanet_status_json > "$latest/status.json"' in report_text
    assert "easymanet-display-status.log" in report_text
    assert 'cp /etc/easymanet/provision.json' not in report_text
    assert 'cp /etc/config/mesh11sd "$latest/config-mesh11sd"' not in report_text
    assert "/etc/init.d/easymanet-boot-report enable" in defaults.read_text()
    assert "write_easymanet_boot_report provisioned" in PROVISION_SCRIPT.read_text()


def test_led_status_hook_is_packaged_enabled_and_reported():
    script = OVERLAY / "usr" / "lib" / "easymanet" / "led-status.sh"
    init = OVERLAY / "etc" / "init.d" / "easymanet-led-status"
    defaults = OVERLAY / "etc" / "uci-defaults" / "96-easymanet-led-status"
    report = OVERLAY / "usr" / "lib" / "easymanet" / "boot-report.sh"

    assert script.exists()
    assert init.exists()
    assert defaults.exists()
    assert script.stat().st_mode & 0o111
    assert init.stat().st_mode & 0o111
    assert defaults.stat().st_mode & 0o111

    script_text = script.read_text()
    assert "--once" in script_text
    assert "EASYMANET_LED_NAME" in script_text
    assert "EASYMANET_LED_TARGETS:=1.1.1.1 8.8.8.8" in script_text
    assert "EASYMANET_LED_INTERVAL:=10" in script_text
    assert "LED_ROOT:=/sys/class/leds" in script_text
    assert "PWR" not in script_text
    assert "echo none >" in script_text
    assert "ping -c 1" in script_text

    init_text = init.read_text()
    assert "USE_PROCD=1" in init_text
    assert "procd_set_param command /usr/lib/easymanet/led-status.sh" in init_text
    assert "procd_set_param respawn" in init_text
    assert "EASYMANET_LED_LOG=/var/log/easymanet-led-status.log" in init_text
    assert "/etc/init.d/easymanet-led-status enable" in defaults.read_text()

    provision_text = PROVISION_SCRIPT.read_text()
    assert "led_status_init=" in provision_text
    assert '"$led_status_init" enable' in provision_text
    assert '"$led_status_init" restart' in provision_text
    assert "EasyMANET LED status init script not found" in provision_text
    assert "easymanet-led-status.log" in report.read_text()


def test_display_status_hook_is_packaged_enabled_and_reported():
    script = OVERLAY / "usr" / "lib" / "easymanet" / "display-status.sh"
    status_lib = OVERLAY / "usr" / "lib" / "easymanet" / "status-lib.sh"
    init = OVERLAY / "etc" / "init.d" / "easymanet-display-status"
    defaults = OVERLAY / "etc" / "uci-defaults" / "95-easymanet-display-status"
    report = OVERLAY / "usr" / "lib" / "easymanet" / "boot-report.sh"

    for path in (script, status_lib, init, defaults):
        assert path.exists()
        assert path.stat().st_mode & 0o111

    assert "--once" in script.read_text()
    assert "EASYMANET_DISPLAY_TTY:=/dev/tty1" in status_lib.read_text()
    assert "render_status_text" in status_lib.read_text()
    init_text = init.read_text()
    assert "procd_set_param command /usr/lib/easymanet/display-status.sh" in init_text
    assert "procd_set_param respawn" not in init_text
    assert "/etc/init.d/easymanet-display-status enable" in defaults.read_text()
    assert "easymanet-display-status.log" in report.read_text()


def test_topology_api_overlay_is_packaged():
    api = OVERLAY / "usr" / "lib" / "easymanet" / "api.sh"
    identity = OVERLAY / "www" / "easymanet-api" / "v1" / "identity"
    neighbors = OVERLAY / "www" / "easymanet-api" / "v1" / "neighbors"
    topology = OVERLAY / "www" / "easymanet-api" / "v1" / "topology"
    status = OVERLAY / "www" / "easymanet-api" / "v1" / "status"
    provision_text = PROVISION_RUNTIME.read_text()
    provision_entrypoint = PROVISION_SCRIPT.read_text()

    for path in (api, identity, neighbors, topology, status):
        assert path.exists()
        assert path.stat().st_mode & 0o111
    assert API_LIB.exists()
    api_text = api.read_text()
    assert "api-lib.sh" in api_text
    assert "status-lib.sh" in api_text
    assert api_text.index("status-lib.sh") > api_text.index('status)')
    assert "configure_easymanet_api" in provision_text
    assert "uhttpd.easymanet_api" in provision_text
    assert '0.0.0.0:$EM_EASYMANET_API_PORT' in provision_text
    assert "allow_easymanet_api_wan=rule" in provision_entrypoint
    core_check = 'api_home/v1/identity" ] || [ ! -x "$api_home/v1/topology" ] || [ ! -x "$api_home/v1/neighbors"'
    assert core_check in provision_text
    assert 'api_home/v1/status" ] ||' not in provision_text
