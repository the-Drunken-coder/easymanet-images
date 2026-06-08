#!/bin/sh
# Write boot diagnostics to the FAT boot partition for offline inspection.

BOOT_REPORT_TMP="/tmp/easymanet-boot-report"
BOOT_REPORT_MOUNTED=0
MESH11SD_SECRET_FIELDS="key|password|passphrase|psk|secret|priv_key_pwd|auto_mesh_id|auto_mesh_key|mesh_gate_key|vtun_gate_key"

boot_report_cleanup() {
    if [ "$BOOT_REPORT_MOUNTED" -eq 1 ]; then
        umount "$BOOT_REPORT_TMP" 2>/dev/null || true
        rmdir "$BOOT_REPORT_TMP" 2>/dev/null || true
    fi
}

find_boot_report_dir() {
    for dir in /boot/easymanet /boot/firmware/easymanet; do
        if [ -d "$dir" ]; then
            printf '%s' "$dir"
            return 0
        fi
    done

    mkdir -p "$BOOT_REPORT_TMP"
    for dev in /dev/mmcblk0p1 /dev/sda1 /dev/nvme0n1p1; do
        [ -b "$dev" ] || continue
        if mount -t vfat "$dev" "$BOOT_REPORT_TMP" 2>/dev/null; then
            BOOT_REPORT_MOUNTED=1
            mkdir -p "$BOOT_REPORT_TMP/easymanet"
            printf '%s' "$BOOT_REPORT_TMP/easymanet"
            return 0
        fi
    done

    return 1
}

run_report_cmd() {
    out="$1"
    shift
    {
        printf '$'
        printf ' %s' "$@"
        printf '\n\n'
        "$@" 2>&1 || true
    } > "$out"
}

easymanet_redact_uci_wireless() {
    uci show wireless 2>/dev/null | sed -E "s/(\.(key|password|priv_key_pwd)=)'[^']*'/\1'<redacted>'/g" || true
}

easymanet_redact_uci_mesh11sd() {
    uci show mesh11sd 2>/dev/null | sed -E "s/\.($MESH11SD_SECRET_FIELDS)='[^']*'/.\1='<redacted>'/g" || true
}

easymanet_redact_config_mesh11sd() {
    if command -v uci >/dev/null 2>&1; then
        easymanet_redact_uci_mesh11sd
        return 0
    fi
    if [ -f /etc/config/mesh11sd ]; then
        sed -E \
            -e "s/(option[[:space:]]+($MESH11SD_SECRET_FIELDS)[[:space:]]+)'[^']*'/\1'<redacted>'/g" \
            -e "s/(option[[:space:]]+($MESH11SD_SECRET_FIELDS)[[:space:]]+)\"[^\"]*\"/\1\"<redacted>\"/g" \
            /etc/config/mesh11sd 2>/dev/null || true
    fi
}

write_easymanet_boot_report() {
    reason="${1:-boot}"
    report_dir="$(find_boot_report_dir)" || return 0
    timestamp="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%s)"
    latest="$report_dir/boot-report-latest"
    current="$report_dir/boot-report-$timestamp"

    rm -rf "$latest" "$current"
    mkdir -p "$latest" "$current"

    {
        echo "reason=$reason"
        echo "timestamp_utc=$timestamp"
        echo "hostname=$(hostname 2>/dev/null || true)"
        echo "uptime=$(cat /proc/uptime 2>/dev/null || true)"
        echo "kernel=$(uname -a 2>/dev/null || true)"
        echo "cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
    } > "$latest/summary.txt"

    cp "$latest/summary.txt" "$current/summary.txt" 2>/dev/null || true

    run_report_cmd "$latest/dmesg.txt" dmesg
    run_report_cmd "$latest/logread.txt" logread
    run_report_cmd "$latest/ip-addr.txt" ip addr
    run_report_cmd "$latest/ip-route.txt" ip route
    run_report_cmd "$latest/ip-link.txt" ip link
    run_report_cmd "$latest/brctl-show.txt" brctl show
    run_report_cmd "$latest/iw-dev.txt" iw dev
    run_report_cmd "$latest/iw-wlan0-info.txt" iw dev wlan0 info
    run_report_cmd "$latest/iw-wlan0-station-dump.txt" iw dev wlan0 station dump
    run_report_cmd "$latest/iw-wlan0-mpath-dump.txt" iw dev wlan0 mpath dump
    run_report_cmd "$latest/batctl-neighbors.txt" batctl n
    run_report_cmd "$latest/batctl-originators.txt" batctl o
    run_report_cmd "$latest/batctl-ifaces.txt" batctl if
    run_report_cmd "$latest/mesh11sd-status.txt" mesh11sd status
    run_report_cmd "$latest/wifi-status.txt" wifi status
    run_report_cmd "$latest/uci-wireless.txt" easymanet_redact_uci_wireless
    run_report_cmd "$latest/uci-network.txt" uci show network
    run_report_cmd "$latest/uci-mesh11sd.txt" easymanet_redact_uci_mesh11sd
    run_report_cmd "$latest/uci-dhcp.txt" uci show dhcp
    run_report_cmd "$latest/uci-firewall.txt" uci show firewall
    run_report_cmd "$latest/ps.txt" ps w
    run_report_cmd "$latest/mount.txt" mount

    cp /var/log/easymanet.log "$latest/easymanet.log" 2>/dev/null || true
    cp /var/log/easymanet-network.log "$latest/easymanet-network.log" 2>/dev/null || true
    cp /etc/easymanet/provisioned "$latest/provisioned" 2>/dev/null || true
    cp /etc/config/network "$latest/config-network" 2>/dev/null || true
    easymanet_redact_config_mesh11sd > "$latest/config-mesh11sd" 2>/dev/null || true
    cp /etc/config/dhcp "$latest/config-dhcp" 2>/dev/null || true
    cp /etc/config/firewall "$latest/config-firewall" 2>/dev/null || true

    cp -R "$latest"/. "$current"/ 2>/dev/null || true
    sync
    boot_report_cleanup
    return 0
}
