#!/bin/sh
# EasyMANET generic first-boot provisioning script.
#
# Expects node-specific provision.json on the FAT boot partition at:
#   /boot/easymanet/provision.json
# and copies it into overlay storage before applying configuration.

set -eu

: "${EASYMANET_PREFIX:=}"

_prefix_path() {
    printf '%s%s' "$EASYMANET_PREFIX" "$1"
}

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
# shellcheck source=provision-lib.sh
. "$SCRIPT_DIR/provision-lib.sh"

# OpenMANET-coupled defaults — override via environment when upstream changes.
: "${EM_MESH_BCF:=bcf_fgh100mhaamd.bin}"
: "${EM_MESH_ENCRYPTION:=sae}"
: "${EM_MESH_FWDING:=0}"
: "${EM_LOCAL_AP_ENCRYPTION:=psk2}"
: "${EM_WIFI_UPLINK_ENCRYPTION_DEFAULT:=psk2}"
: "${EM_BATMAN_ROUTING_ALGO:=BATMAN_V}"
: "${EM_MESH_NETMASK:=255.255.0.0}"
: "${EM_LAN_FALLBACK_IP:=10.41.254.1}"
: "${EM_LAN_NETMASK:=255.255.255.0}"
: "${EM_UPLINK_DNS:=1.1.1.1 8.8.8.8}"
: "${EM_MESH11SD_MESH_FWDING:=0}"
: "${EM_MESH11SD_MAX_PEER_LINKS:=10}"
: "${EM_MESH11SD_RSSI_THRESHOLD:=0}"
: "${EM_MESH11SD_HWMP_ROOTMODE:=0}"
: "${EM_MESH11SD_NOLEARN:=0}"
: "${EM_MESH11SD_DYNAMIC_PEERING:=1}"
: "${EM_MESH11SD_BEACON_LESS_MODE:=0}"
: "${EM_MESH11SD_MBCA_CONFIG:=1}"
: "${EM_MESH11SD_BEACON_TIMING_REPORT_INT:=10}"
: "${EM_MESH11SD_MBSS_START_SCAN_MS:=2048}"
: "${EM_MESH11SD_MBCA_MIN_BEACON_GAP_MS:=25}"
: "${EM_MESH11SD_MBCA_TBTT_ADJ_INTERVAL_SEC:=60}"
: "${EM_EASYMANET_API_PORT:=10411}"

LOG_FILE="$(_prefix_path /var/log/easymanet.log)"
PROVISIONED_FLAG="$(_prefix_path /etc/easymanet/provisioned)"
PROVISION_DIR="$(_prefix_path /etc/easymanet)"
PROVISION_JSON="$PROVISION_DIR/provision.json"
BOOT_REPORT_SCRIPT="$(_prefix_path /usr/lib/easymanet/boot-report.sh)"
NETWORK_HELPERS="${EASYMANET_NETWORK_HELPERS:-$(_prefix_path /usr/lib/easymanet/network.sh)}"
BOOT_MOUNT_TMP="$(_prefix_path /tmp/easymanet-boot)"
BOOT_JSON=""
BOOT_MOUNTED_TMP=0

mkdir -p "$(dirname "$LOG_FILE")"
exec 2>>"$LOG_FILE"
echo "=== EasyMANET provisioning started $(date) ===" >> "$LOG_FILE"

cleanup() {
    if [ "$BOOT_MOUNTED_TMP" -eq 1 ]; then
        umount "$BOOT_MOUNT_TMP" 2>/dev/null || true
        rmdir "$BOOT_MOUNT_TMP" 2>/dev/null || true
    fi
}

trap cleanup EXIT

write_root_shadow_hash() {
    shadow_path="$1"
    root_hash="$2"
    tmp_path="${shadow_path}.easymanet.$$"
    mode="$(stat -c '%a' "$shadow_path" 2>/dev/null || stat -f '%Lp' "$shadow_path" 2>/dev/null || true)"
    owner="$(stat -c '%u:%g' "$shadow_path" 2>/dev/null || stat -f '%u:%g' "$shadow_path" 2>/dev/null || true)"

    case "$root_hash" in
        *[!A-Za-z0-9./$=]*)
            echo "Invalid root password hash characters" >> "$LOG_FILE"
            return 1
            ;;
    esac

    old_umask="$(umask)"
    umask 077
    if ! : > "$tmp_path"; then
        umask "$old_umask"
        return 1
    fi
    umask "$old_umask"
    chmod 0600 "$tmp_path" 2>/dev/null || {
        rm -f "$tmp_path"
        return 1
    }

    EASYMANET_ROOT_HASH="$root_hash"
    export EASYMANET_ROOT_HASH
    if ! awk '
        BEGIN {
            hash = ENVIRON["EASYMANET_ROOT_HASH"]
            replaced = 0
        }
        /^root:/ {
            printf "root:%s:19000:0:99999:7:::\n", hash
            replaced = 1
            next
        }
        { print }
        END {
            if (!replaced) {
                printf "root:%s:19000:0:99999:7:::\n", hash
            }
        }
    ' "$shadow_path" > "$tmp_path"; then
        unset EASYMANET_ROOT_HASH
        rm -f "$tmp_path"
        return 1
    fi
    unset EASYMANET_ROOT_HASH

    if [ -n "$mode" ]; then
        chmod "$mode" "$tmp_path" 2>/dev/null || true
    else
        chmod 0600 "$tmp_path" 2>/dev/null || true
    fi
    if [ -n "$owner" ]; then
        chown "$owner" "$tmp_path" 2>/dev/null || true
    fi
    mv "$tmp_path" "$shadow_path"
}

wipe_boot_provision_json() {
    for candidate in \
        "$BOOT_JSON" \
        "$(_prefix_path /boot/easymanet/provision.json)" \
        "$(_prefix_path /boot/firmware/easymanet/provision.json)"
    do
        if [ -f "$candidate" ]; then
            rm -f "$candidate"
            echo "Removed boot payload: $candidate" >> "$LOG_FILE"
        fi
    done
}

uci_set() {
    uci set "$@" >> "$LOG_FILE" 2>&1
}

uci_commit() {
    uci commit "$1" >> "$LOG_FILE" 2>&1
}

uci_add_list() {
    uci add_list "$@" >> "$LOG_FILE" 2>&1
}

configure_mesh_radio_device() {
    radio="$1"
    uci_set wireless."$radio".channel="$MESH_CHANNEL"
    uci_set wireless."$radio".s1g_chanbw="$MESH_BW"
    uci -q delete wireless."$radio".htmode 2>/dev/null || true
    uci_set wireless."$radio".country="$MESH_COUNTRY"
    uci_set wireless."$radio".bcf="$EM_MESH_BCF"
    uci_set wireless."$radio".disabled="0"
}

configure_easymanet_api() {
    api_home="$(_prefix_path /www/easymanet-api)"
    if [ ! -x "$api_home/v1/identity" ] || [ ! -x "$api_home/v1/topology" ]; then
        echo "WARNING: EasyMANET API endpoint wrappers are missing; skipping API setup" >> "$LOG_FILE"
        return 0
    fi

    echo "Configuring EasyMANET topology API on port $EM_EASYMANET_API_PORT..." >> "$LOG_FILE"
    uci -q delete uhttpd.easymanet_api 2>/dev/null || true
    uci_set uhttpd.easymanet_api=uhttpd
    uci_set uhttpd.easymanet_api.home="$api_home"
    uci_set uhttpd.easymanet_api.cgi_prefix="/v1"
    uci_set uhttpd.easymanet_api.script_timeout="10"
    uci_set uhttpd.easymanet_api.network_timeout="10"
    uci_set uhttpd.easymanet_api.http_keepalive="0"
    uci_set uhttpd.easymanet_api.tcp_keepalive="1"
    if [ "$NODE_ROLE" = "gate" ]; then
        uci_add_list uhttpd.easymanet_api.listen_http="0.0.0.0:$EM_EASYMANET_API_PORT"
    else
        uci_add_list uhttpd.easymanet_api.listen_http="$NODE_IP:$EM_EASYMANET_API_PORT"
    fi
    uci_commit uhttpd
}

find_boot_json() {
    for candidate in \
        "$(_prefix_path /boot/easymanet/provision.json)" \
        "$(_prefix_path /boot/firmware/easymanet/provision.json)"
    do
        if [ -s "$candidate" ]; then
            BOOT_JSON="$candidate"
            return 0
        fi
    done

    if [ -n "$EASYMANET_PREFIX" ]; then
        return 1
    fi

    mkdir -p "$BOOT_MOUNT_TMP"
    for dev in /dev/mmcblk0p1 /dev/sda1 /dev/nvme0n1p1; do
        [ -b "$dev" ] || continue
        if mount -t vfat "$dev" "$BOOT_MOUNT_TMP" 2>/dev/null; then
            BOOT_MOUNTED_TMP=1
            if [ -s "$BOOT_MOUNT_TMP/easymanet/provision.json" ]; then
                BOOT_JSON="$BOOT_MOUNT_TMP/easymanet/provision.json"
                return 0
            fi
            umount "$BOOT_MOUNT_TMP" 2>/dev/null || true
            BOOT_MOUNTED_TMP=0
        fi
    done

    return 1
}

if [ -f "$PROVISIONED_FLAG" ]; then
    echo "Already provisioned, skipping." >> "$LOG_FILE"
    exit 0
fi

if ! find_boot_json; then
    echo "FATAL: no boot-partition provision.json found" | tee -a "$LOG_FILE"
    exit 1
fi

mkdir -p "$PROVISION_DIR"
cp "$BOOT_JSON" "$PROVISION_JSON"
chmod 0600 "$PROVISION_JSON"

if ! command -v jsonfilter >/dev/null 2>&1; then
    echo "FATAL: jsonfilter not found; cannot parse provision.json" | tee -a "$LOG_FILE"
    exit 1
fi

PROVISION_VERSION="$(json_val version)"
MESH_ID="$(json_val mesh id)"
MESH_PASSWORD="$(json_val mesh password)"
HOSTNAME="$(json_val node hostname)"
NODE_ROLE="$(json_val node role)"
NODE_IP="$(json_val node ip)"
NODE_TARGET="$(json_val node target)"
MESH_CHANNEL="$(json_val mesh channel)"
MESH_BW="$(json_val mesh bandwidth_mhz)"
MESH_COUNTRY="$(json_val mesh country)"

missing_fields=""
[ -n "$PROVISION_VERSION" ] || missing_fields="$missing_fields version"
[ -n "$MESH_ID" ] || missing_fields="$missing_fields mesh.id"
[ -n "$MESH_PASSWORD" ] || missing_fields="$missing_fields mesh.password"
[ -n "$MESH_CHANNEL" ] || missing_fields="$missing_fields mesh.channel"
[ -n "$MESH_BW" ] || missing_fields="$missing_fields mesh.bandwidth_mhz"
[ -n "$MESH_COUNTRY" ] || missing_fields="$missing_fields mesh.country"
[ -n "$HOSTNAME" ] || missing_fields="$missing_fields node.hostname"
[ -n "$NODE_ROLE" ] || missing_fields="$missing_fields node.role"
[ -n "$NODE_IP" ] || missing_fields="$missing_fields node.ip"
[ -n "$NODE_TARGET" ] || NODE_TARGET="rpi4-mm6108-spi"
if [ -n "$missing_fields" ]; then
    echo "FATAL: missing required provision.json fields:$missing_fields" | tee -a "$LOG_FILE"
    exit 1
fi
if [ "$PROVISION_VERSION" != "1" ]; then
    echo "FATAL: unsupported provision.json version: $PROVISION_VERSION" | tee -a "$LOG_FILE"
    exit 1
fi
case "$NODE_ROLE" in
    gate|point) ;;
    *)
        echo "FATAL: unsupported node.role in provision.json: $NODE_ROLE" | tee -a "$LOG_FILE"
        exit 1
        ;;
esac
case "$MESH_BW" in
    1|2|4|8) ;;
    *)
        echo "FATAL: unsupported mesh.bandwidth_mhz in provision.json: $MESH_BW" | tee -a "$LOG_FILE"
        exit 1
        ;;
esac
case "$MESH_CHANNEL" in
    *[!0-9]*)
        echo "FATAL: mesh.channel must be numeric in provision.json: $MESH_CHANNEL" | tee -a "$LOG_FILE"
        exit 1
        ;;
esac
if [ "$NODE_TARGET" = "rpi4-mm6108-spi" ] && [ "$MESH_COUNTRY" = "US" ]; then
    case "${MESH_CHANNEL}:${MESH_BW}" in
        0:2|42:2) ;;
        *)
            echo "FATAL: rpi4-mm6108-spi in US requires mesh.channel 42 and mesh.bandwidth_mhz 2; got channel $MESH_CHANNEL bandwidth $MESH_BW" | tee -a "$LOG_FILE"
            exit 1
            ;;
    esac
fi

BATMAN_GW_MODE="client"
MESH_GATE_ANNOUNCEMENTS="0"
WIFI_UPLINK_ENABLED=0
if json_bool node gateway wifi enabled; then
    WIFI_UPLINK_ENABLED=1
fi
if [ "$NODE_ROLE" = "gate" ]; then
    BATMAN_GW_MODE="server"
    MESH_GATE_ANNOUNCEMENTS="1"
fi

SSH_ENABLED=0
if [ -n "$(json_val management ssh_enabled)" ]; then
    json_bool management ssh_enabled && SSH_ENABLED=1
elif [ "$NODE_ROLE" = "gate" ]; then
    SSH_ENABLED=1
fi

echo "Setting hostname to $HOSTNAME..." >> "$LOG_FILE"
uci_set system.@system[0].hostname="$HOSTNAME"
uci_set system.@system[0].timezone="UTC"
uci_commit system
echo "$HOSTNAME" > "$(_prefix_path /proc/sys/kernel/hostname)" 2>/dev/null || true

if command -v dropbear >/dev/null 2>&1 || [ -x "$(_prefix_path /etc/init.d/dropbear)" ]; then
    mkdir -p "$(_prefix_path /etc/dropbear)"
fi

ROOT_PW_HASH="$(json_val management root_password_hash 2>/dev/null || true)"
if [ -n "$ROOT_PW_HASH" ]; then
    echo "Setting root password hash..." >> "$LOG_FILE"
    shadow_path="$(_prefix_path /etc/shadow)"
    if [ ! -f "$shadow_path" ]; then
        printf 'root::0:0:99999:7:::\n' > "$shadow_path"
    fi
    if ! write_root_shadow_hash "$shadow_path" "$ROOT_PW_HASH"; then
        echo "FATAL: failed to set root password hash in /etc/shadow" | tee -a "$LOG_FILE"
        exit 1
    fi
fi

if jsonfilter -i "$PROVISION_JSON" -e '@.management.ssh_authorized_keys' >/dev/null 2>&1; then
    auth_keys="$(_prefix_path /etc/dropbear/authorized_keys)"
    : > "$auth_keys"
    jsonfilter -i "$PROVISION_JSON" -e '@.management.ssh_authorized_keys[*]' | while IFS= read -r key; do
        [ -z "$key" ] && continue
        echo "$key" >> "$auth_keys"
    done
    chmod 0600 "$auth_keys"
    chown root "$auth_keys" 2>/dev/null || true
fi

echo "Configuring mesh wireless..." >> "$LOG_FILE"
MESH_RADIO="$(find_morse_radio)"
if [ -z "$MESH_RADIO" ]; then
    echo "FATAL: no Morse/802.11ah wifi-device found in /etc/config/wireless" | tee -a "$LOG_FILE"
    exit 1
fi

echo "Using Morse HaLow radio $MESH_RADIO..." >> "$LOG_FILE"
delete_ifaces_for_radio "$MESH_RADIO"
configure_mesh_radio_device "$MESH_RADIO"

uci_set wireless.mesh0=wifi-iface
uci_set wireless.mesh0.device="$MESH_RADIO"
uci_set wireless.mesh0.ifname="wlan0"
uci_set wireless.mesh0.network="mesh"
uci_set wireless.mesh0.mode="mesh"
uci_set wireless.mesh0.mesh_id="$MESH_ID"
uci_set wireless.mesh0.encryption="$EM_MESH_ENCRYPTION"
uci_set wireless.mesh0.key="$MESH_PASSWORD"
uci_set wireless.mesh0.mesh_fwding="$EM_MESH_FWDING"

if json_bool node local_ap enabled && [ "$WIFI_UPLINK_ENABLED" -ne 1 ]; then
    LOCAL_AP_SSID="$(json_val node local_ap ssid)"
    LOCAL_AP_PASSWORD="$(json_val node local_ap password)"
    AP_RADIO="$(find_local_ap_radio)"
    if [ -n "$AP_RADIO" ]; then
        echo "Using local AP radio $AP_RADIO..." >> "$LOG_FILE"
        delete_ifaces_for_radio "$AP_RADIO"
        uci_set wireless."$AP_RADIO".country="$MESH_COUNTRY"
        uci_set wireless."$AP_RADIO".disabled="0"
        uci_set wireless.ap0=wifi-iface
        uci_set wireless.ap0.device="$AP_RADIO"
        uci_set wireless.ap0.network="lan"
        uci_set wireless.ap0.mode="ap"
        uci_set wireless.ap0.ssid="$LOCAL_AP_SSID"
        uci_set wireless.ap0.encryption="$EM_LOCAL_AP_ENCRYPTION"
        uci_set wireless.ap0.key="$LOCAL_AP_PASSWORD"
    else
        echo "WARNING: local_ap enabled but no mac80211 wifi-device was found; skipping local AP" >> "$LOG_FILE"
    fi
fi

if [ "$WIFI_UPLINK_ENABLED" -eq 1 ]; then
    WIFI_UPLINK_SSID="$(json_val node gateway wifi ssid)"
    WIFI_UPLINK_PASSWORD="$(json_val node gateway wifi password)"
    WIFI_UPLINK_ENCRYPTION="$(json_val node gateway wifi encryption)"
    if [ -z "$WIFI_UPLINK_ENCRYPTION" ]; then
        WIFI_UPLINK_ENCRYPTION="$EM_WIFI_UPLINK_ENCRYPTION_DEFAULT"
    fi
    if [ -z "$WIFI_UPLINK_SSID" ] || [ -z "$WIFI_UPLINK_PASSWORD" ]; then
        echo "FATAL: gateway.wifi.enabled requires gateway.wifi.ssid and gateway.wifi.password" | tee -a "$LOG_FILE"
        exit 1
    fi
    if [ -z "${AP_RADIO:-}" ]; then
        AP_RADIO="$(find_local_ap_radio)"
    fi
    if [ -z "$AP_RADIO" ]; then
        echo "FATAL: gateway.wifi.enabled but no mac80211 wifi-device was found" | tee -a "$LOG_FILE"
        exit 1
    fi
    echo "Using Wi-Fi uplink on radio $AP_RADIO for SSID $WIFI_UPLINK_SSID..." >> "$LOG_FILE"
    delete_ifaces_for_radio "$AP_RADIO"
    uci_set wireless."$AP_RADIO".country="$MESH_COUNTRY"
    uci_set wireless."$AP_RADIO".disabled="0"
    uci_set wireless.wan0=wifi-iface
    uci_set wireless.wan0.device="$AP_RADIO"
    uci_set wireless.wan0.network="wan"
    uci_set wireless.wan0.mode="sta"
    uci_set wireless.wan0.ssid="$WIFI_UPLINK_SSID"
    uci_set wireless.wan0.encryption="$WIFI_UPLINK_ENCRYPTION"
    uci_set wireless.wan0.key="$WIFI_UPLINK_PASSWORD"
fi
uci_commit wireless

echo "Configuring network..." >> "$LOG_FILE"
uci_set network.bat0=interface
uci_set network.bat0.proto="batadv"
uci_set network.bat0.routing_algo="$EM_BATMAN_ROUTING_ALGO"
uci_set network.bat0.bridge_loop_avoidance="1"
uci_set network.bat0.distributed_arp_table="1"
uci_set network.bat0.multicast_mode="1"
uci_set network.bat0.gw_mode="$BATMAN_GW_MODE"

uci_set network.mesh=interface
uci_set network.mesh.proto="batadv_hardif"
uci_set network.mesh.master="bat0"
uci -q delete network.mesh.ipaddr 2>/dev/null || true
uci -q delete network.mesh.netmask 2>/dev/null || true

uci_set network.meship=interface
uci_set network.meship.proto="static"
uci_set network.meship.device="bat0"
uci_set network.meship.ipaddr="$NODE_IP"
uci_set network.meship.netmask="$EM_MESH_NETMASK"

if ! uci -q get network.lan >/dev/null 2>&1; then
    uci_set network.lan=interface
fi
uci_set network.lan.device="br-lan"
uci_set network.lan.proto="static"
if [ -z "$(uci -q get network.lan.ipaddr)" ]; then
    uci_set network.lan.ipaddr="$EM_LAN_FALLBACK_IP"
fi
uci_set network.lan.netmask="$EM_LAN_NETMASK"
uci_commit network

uci -q delete dhcp.mesh 2>/dev/null || true
uci_set dhcp.meship=dhcp
uci_set dhcp.meship.interface="meship"
uci_set dhcp.meship.ignore="1"
uci_set dhcp.lan=dhcp
uci_set dhcp.lan.interface="lan"
uci_set dhcp.lan.start="100"
uci_set dhcp.lan.limit="150"
uci_set dhcp.lan.leasetime="12h"
uci_commit dhcp

uci_set firewall.mesh_zone=zone
uci_set firewall.mesh_zone.name="mesh"
uci_set firewall.mesh_zone.network="meship"
uci_set firewall.mesh_zone.input="ACCEPT"
uci_set firewall.mesh_zone.output="ACCEPT"
uci_set firewall.mesh_zone.forward="ACCEPT"
uci -q delete firewall.allow_easymanet_api_wan 2>/dev/null || true
if [ "$NODE_ROLE" = "gate" ]; then
    uci_set firewall.allow_easymanet_api_wan=rule
    uci_set firewall.allow_easymanet_api_wan.name="Allow-EasyMANET-API-WAN"
    uci_set firewall.allow_easymanet_api_wan.src="wan"
    uci_set firewall.allow_easymanet_api_wan.proto="tcp"
    uci_set firewall.allow_easymanet_api_wan.dest_port="$EM_EASYMANET_API_PORT"
    uci_set firewall.allow_easymanet_api_wan.target="ACCEPT"
fi
uci_commit firewall

configure_easymanet_api

uci_set mesh11sd.setup=mesh11sd
uci_set mesh11sd.setup.enabled="1"
uci_set mesh11sd.mesh_params=mesh11sd
uci_set mesh11sd.mesh_params.mesh_fwding="$EM_MESH11SD_MESH_FWDING"
uci_set mesh11sd.mesh_params.mesh_max_peer_links="$EM_MESH11SD_MAX_PEER_LINKS"
uci_set mesh11sd.mesh_params.mesh_rssi_threshold="$EM_MESH11SD_RSSI_THRESHOLD"
uci_set mesh11sd.mesh_params.mesh_hwmp_rootmode="$EM_MESH11SD_HWMP_ROOTMODE"
uci_set mesh11sd.mesh_params.mesh_gate_announcements="$MESH_GATE_ANNOUNCEMENTS"
uci_set mesh11sd.mesh_params.mesh_nolearn="$EM_MESH11SD_NOLEARN"
uci_set mesh11sd.mesh_dynamic_peering=mesh11sd
uci_set mesh11sd.mesh_dynamic_peering.enabled="$EM_MESH11SD_DYNAMIC_PEERING"
uci_set mesh11sd.mesh_beaconless=mesh11sd
uci_set mesh11sd.mesh_beaconless.mesh_beacon_less_mode="$EM_MESH11SD_BEACON_LESS_MODE"
uci_set mesh11sd.mbca=mesh11sd
uci_set mesh11sd.mbca.mbca_config="$EM_MESH11SD_MBCA_CONFIG"
uci_set mesh11sd.mbca.mesh_beacon_timing_report_int="$EM_MESH11SD_BEACON_TIMING_REPORT_INT"
uci_set mesh11sd.mbca.mbss_start_scan_duration_ms="$EM_MESH11SD_MBSS_START_SCAN_MS"
uci_set mesh11sd.mbca.mbca_min_beacon_gap_ms="$EM_MESH11SD_MBCA_MIN_BEACON_GAP_MS"
uci_set mesh11sd.mbca.mbca_tbtt_adj_interval_sec="$EM_MESH11SD_MBCA_TBTT_ADJ_INTERVAL_SEC"
uci_commit mesh11sd

echo "Ensuring eth0 stays on br-lan for management..." >> "$LOG_FILE"
if [ -f "$NETWORK_HELPERS" ]; then
    EASYMANET_NETWORK_LOG="$LOG_FILE" EASYMANET_PROVISION_JSON="$PROVISION_JSON" . "$NETWORK_HELPERS"
    easymanet_repair_management_lan firstboot
fi

if [ "$NODE_ROLE" = "gate" ] && [ "$WIFI_UPLINK_ENABLED" -ne 1 ]; then
    UPLINK="$(json_val node gateway uplink_interface 2>/dev/null || true)"
    [ -n "$UPLINK" ] || UPLINK="eth0"
    if [ "$UPLINK" = "eth0" ]; then
        uci -q delete network.wan 2>/dev/null || true
        uci -q delete network.wan6 2>/dev/null || true
        uci_commit network
    else
        uci_set network.wan=interface
        uci_set network.wan.proto="dhcp"
        uci_set network.wan.device="$UPLINK"
        uci_set network.wan.ifname="$UPLINK"
        uci_set network.wan.peerdns="0"
        uci_set network.wan.dns="$EM_UPLINK_DNS"
        uci_commit network
    fi
fi

if [ "$WIFI_UPLINK_ENABLED" -eq 1 ]; then
    uci_set network.wan=interface
    uci_set network.wan.proto="dhcp"
    uci -q delete network.wan.device 2>/dev/null || true
    uci -q delete network.wan.ifname 2>/dev/null || true
    uci_set network.wan.peerdns="0"
    uci_set network.wan.dns="$EM_UPLINK_DNS"
    uci -q delete network.wan6 2>/dev/null || true
    uci_commit network

    uci -q delete firewall.allow_ssh_wan 2>/dev/null || true
    if [ "$SSH_ENABLED" -eq 1 ]; then
        uci_set firewall.allow_ssh_wan=rule
        uci_set firewall.allow_ssh_wan.name="Allow-SSH-WAN"
        uci_set firewall.allow_ssh_wan.src="wan"
        uci_set firewall.allow_ssh_wan.proto="tcp"
        uci_set firewall.allow_ssh_wan.dest_port="22"
        uci_set firewall.allow_ssh_wan.target="ACCEPT"
    fi
    uci_commit firewall
fi

dropbear_init="$(_prefix_path /etc/init.d/dropbear)"
if [ -x "$dropbear_init" ]; then
    if [ "$SSH_ENABLED" -eq 1 ]; then
        echo "Enabling SSH (dropbear)..." >> "$LOG_FILE"
        "$dropbear_init" enable 2>/dev/null || true
        "$dropbear_init" restart 2>/dev/null || "$dropbear_init" start 2>/dev/null || true
    else
        echo "Disabling SSH (dropbear) for this node..." >> "$LOG_FILE"
        "$dropbear_init" stop 2>/dev/null || true
        "$dropbear_init" disable 2>/dev/null || true
    fi
fi

uhttpd_init="$(_prefix_path /etc/init.d/uhttpd)"
if [ -x "$uhttpd_init" ]; then
    echo "Enabling EasyMANET topology API (uhttpd)..." >> "$LOG_FILE"
    "$uhttpd_init" enable 2>/dev/null || true
    "$uhttpd_init" restart 2>/dev/null || "$uhttpd_init" start 2>/dev/null || true
else
    echo "WARNING: uhttpd init script not found; EasyMANET topology API will not start" >> "$LOG_FILE"
fi

openmanetd_config="$(_prefix_path /etc/openmanetd/config.yml)"
if [ -f "$openmanetd_config" ]; then
    cat > "$openmanetd_config" <<EOF
meshNetInterface: "bat0"
mesh:
  id: "${MESH_ID}"
  password: "${MESH_PASSWORD}"
  channel: ${MESH_CHANNEL}
  bandwidth_mhz: ${MESH_BW}
  country: "${MESH_COUNTRY}"
node:
  name: "$(json_val node name)"
  hostname: "${HOSTNAME}"
  role: "${NODE_ROLE}"
  ip: "${NODE_IP}"
EOF
fi

network_init="$(_prefix_path /etc/init.d/network)"
if [ -x "$network_init" ]; then
    "$network_init" enable 2>/dev/null || true
    "$network_init" restart 2>/dev/null || true
fi
echo "Reapplying Morse mesh wireless settings after network restart..." >> "$LOG_FILE"
configure_mesh_radio_device "$MESH_RADIO"
uci_commit wireless
if command -v wifi >/dev/null 2>&1; then
    wifi reload "$MESH_RADIO" >> "$LOG_FILE" 2>&1 || true
fi
mesh11sd_init="$(_prefix_path /etc/init.d/mesh11sd)"
if [ -x "$mesh11sd_init" ]; then
    "$mesh11sd_init" enable 2>/dev/null || true
fi
openmanetd_init="$(_prefix_path /etc/init.d/openmanetd)"
if [ -x "$openmanetd_init" ]; then
    "$openmanetd_init" enable 2>/dev/null || true
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)" > "$PROVISIONED_FLAG"
echo "hostname: $HOSTNAME" >> "$PROVISIONED_FLAG"
echo "role: $NODE_ROLE" >> "$PROVISIONED_FLAG"
echo "ip: $NODE_IP" >> "$PROVISIONED_FLAG"
echo "=== EasyMANET provisioning complete $(date) ===" >> "$LOG_FILE"

wipe_boot_provision_json

if [ -f "$BOOT_REPORT_SCRIPT" ]; then
    # shellcheck source=boot-report.sh
    . "$BOOT_REPORT_SCRIPT"
    write_easymanet_boot_report provisioned || true
fi

exit 0
