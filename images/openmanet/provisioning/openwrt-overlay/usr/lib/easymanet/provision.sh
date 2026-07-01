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
: "${EM_AHWLAN_IFACE:=ahwlan}"
: "${EM_AHWLAN_BRIDGE:=br-ahwlan}"
: "${EM_AHWLAN_DHCP_START:=351}"
: "${EM_AHWLAN_DHCP_LIMIT:=16}"
: "${EM_AHWLAN_DHCP_LEASETIME:=12h}"
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

EASYMANET_API_CONFIGURED=0

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

# shellcheck source=provision-runtime.sh
. "$SCRIPT_DIR/provision-runtime.sh"

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
[ -n "$NODE_TARGET" ] || missing_fields="$missing_fields node.target"
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
case "$NODE_TARGET" in
    rpi4-mm6108-spi) ;;
    *)
        echo "FATAL: unsupported node.target in provision.json: $NODE_TARGET" | tee -a "$LOG_FILE"
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
        42:2) ;;
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

UPLINK_INTERFACE="$(json_val node gateway uplink_interface 2>/dev/null || true)"
[ -n "$UPLINK_INTERFACE" ] || UPLINK_INTERFACE="eth0"
ETH0_MESH_SIDE=1
if [ "$NODE_ROLE" = "gate" ] && [ "$WIFI_UPLINK_ENABLED" -ne 1 ] && [ "$UPLINK_INTERFACE" = "eth0" ]; then
    ETH0_MESH_SIDE=0
fi

find_network_device_section() {
    bridge_name="$1"
    uci show network | sed -n "s/^network\.\([^.=]*\)\.name='$bridge_name'$/\1/p" | head -n 1
}

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

if [ "$WIFI_UPLINK_ENABLED" -ne 1 ]; then
    uci -q delete wireless.wan0 2>/dev/null || true
fi

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
        uci_set wireless.ap0.network="$EM_AHWLAN_IFACE"
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

uci -q delete network.meship 2>/dev/null || true
uci -q delete network.lan 2>/dev/null || true
brlan_device_section="$(find_network_device_section br-lan)"
if [ -n "$brlan_device_section" ]; then
    uci -q delete network."$brlan_device_section" 2>/dev/null || true
fi

# OpenMANET's mesh LAN is br-ahwlan, not raw bat0. BATMAN, Ethernet,
# and the local AP share this flat 10.41.0.0/16 bridge so OpenMANETd
# can manage gateway routing and client access using its normal model.
ahwlan_device_section="$(find_network_device_section "$EM_AHWLAN_BRIDGE")"
[ -n "$ahwlan_device_section" ] || ahwlan_device_section="ahwlan_dev"
uci_set network."$ahwlan_device_section"=device
uci_set network."$ahwlan_device_section".name="$EM_AHWLAN_BRIDGE"
uci_set network."$ahwlan_device_section".type="bridge"
uci -q delete network."$ahwlan_device_section".ports 2>/dev/null || true
uci_add_list network."$ahwlan_device_section".ports="bat0"

# eth0 is mesh-side client access unless this gateway explicitly uses it
# as the WAN uplink. In that uplink case, keep it out of br-ahwlan.
if [ "$ETH0_MESH_SIDE" -eq 1 ]; then
    uci_add_list network."$ahwlan_device_section".ports="eth0"
fi

uci_set network."$EM_AHWLAN_IFACE"=interface
uci_set network."$EM_AHWLAN_IFACE".proto="static"
uci_set network."$EM_AHWLAN_IFACE".device="$EM_AHWLAN_BRIDGE"
uci_set network."$EM_AHWLAN_IFACE".ipaddr="$NODE_IP"
uci_set network."$EM_AHWLAN_IFACE".netmask="$EM_MESH_NETMASK"
uci_set network."$EM_AHWLAN_IFACE".dns="$EM_UPLINK_DNS"
if [ "$NODE_ROLE" != "gate" ]; then
    wan_device="$(uci -q get network.wan.device || true)"
    wan_ifname="$(uci -q get network.wan.ifname || true)"
    wan6_device="$(uci -q get network.wan6.device || true)"
    wan6_ifname="$(uci -q get network.wan6.ifname || true)"
    clear_point_wan=0
    clear_point_wan6=0
    if [ -z "$wan_device$wan_ifname" ]; then
        if uci -q get network.wan.proto >/dev/null 2>&1; then
            clear_point_wan=1
        fi
    fi
    if [ -z "$wan6_device$wan6_ifname" ]; then
        if uci -q get network.wan6.proto >/dev/null 2>&1; then
            clear_point_wan6=1
        fi
    fi
    case " $wan_device $wan_ifname " in
        *" eth0 "*|*" br-lan "*|*" $EM_AHWLAN_BRIDGE "*|*" bat0 "*|*" mesh "*|*" wlan0 "*)
            clear_point_wan=1
            ;;
    esac
    case " $wan6_device $wan6_ifname " in
        *" eth0 "*|*" br-lan "*|*" $EM_AHWLAN_BRIDGE "*|*" bat0 "*|*" mesh "*|*" wlan0 "*)
            clear_point_wan6=1
            ;;
    esac
    if [ "$clear_point_wan" -eq 1 ]; then
        uci -q delete network.wan 2>/dev/null || true
        clear_point_wan6=1
    fi
    if [ "$clear_point_wan6" -eq 1 ]; then
        uci -q delete network.wan6 2>/dev/null || true
    fi
fi
uci_commit network

uci -q delete dhcp.mesh 2>/dev/null || true
uci -q delete dhcp.meship 2>/dev/null || true
uci -q delete dhcp.lan 2>/dev/null || true
uci_set dhcp."$EM_AHWLAN_IFACE"=dhcp
uci_set dhcp."$EM_AHWLAN_IFACE".interface="$EM_AHWLAN_IFACE"
# br-ahwlan is one flat mesh LAN, so only the gate serves DHCP. Point
# nodes bridge client traffic to the gate instead of racing it with the
# same lease pool.
if [ "$NODE_ROLE" = "gate" ]; then
    uci -q delete dhcp."$EM_AHWLAN_IFACE".ignore 2>/dev/null || true
    uci_set dhcp."$EM_AHWLAN_IFACE".start="$EM_AHWLAN_DHCP_START"
    uci_set dhcp."$EM_AHWLAN_IFACE".limit="$EM_AHWLAN_DHCP_LIMIT"
    uci_set dhcp."$EM_AHWLAN_IFACE".leasetime="$EM_AHWLAN_DHCP_LEASETIME"
else
    uci_set dhcp."$EM_AHWLAN_IFACE".ignore="1"
    uci -q delete dhcp."$EM_AHWLAN_IFACE".start 2>/dev/null || true
    uci -q delete dhcp."$EM_AHWLAN_IFACE".limit 2>/dev/null || true
    uci -q delete dhcp."$EM_AHWLAN_IFACE".leasetime 2>/dev/null || true
fi
uci_commit dhcp

uci_set firewall.mesh_zone=zone
uci_set firewall.mesh_zone.name="mesh"
uci_set firewall.mesh_zone.network="$EM_AHWLAN_IFACE"
uci_set firewall.mesh_zone.input="ACCEPT"
uci_set firewall.mesh_zone.output="ACCEPT"
uci_set firewall.mesh_zone.forward="ACCEPT"
uci -q delete firewall.mesh_wan_forwarding 2>/dev/null || true
uci -q delete firewall.allow_ssh_wan 2>/dev/null || true
uci -q delete firewall.allow_easymanet_api_wan 2>/dev/null || true
if [ "$NODE_ROLE" = "gate" ]; then
    uci_set firewall.mesh_wan_forwarding=forwarding
    uci_set firewall.mesh_wan_forwarding.src="mesh"
    uci_set firewall.mesh_wan_forwarding.dest="wan"
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

echo "Ensuring mesh-side Ethernet matches br-ahwlan policy..." >> "$LOG_FILE"
if [ -f "$NETWORK_HELPERS" ]; then
    EASYMANET_NETWORK_LOG="$LOG_FILE" EASYMANET_PROVISION_JSON="$PROVISION_JSON" . "$NETWORK_HELPERS"
    easymanet_repair_management_lan firstboot
fi

if [ "$NODE_ROLE" = "gate" ] && [ "$WIFI_UPLINK_ENABLED" -ne 1 ]; then
    uci_set network.wan=interface
    uci_set network.wan.proto="dhcp"
    uci_set network.wan.device="$UPLINK_INTERFACE"
    uci_set network.wan.ifname="$UPLINK_INTERFACE"
    uci_set network.wan.peerdns="0"
    uci_set network.wan.dns="$EM_UPLINK_DNS"
    uci -q delete network.wan6 2>/dev/null || true
    uci_commit network
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
    if [ "$NODE_ROLE" = "gate" ]; then
        uci_set firewall.allow_easymanet_api_wan=rule
        uci_set firewall.allow_easymanet_api_wan.name="Allow-EasyMANET-API-WAN"
        uci_set firewall.allow_easymanet_api_wan.src="wan"
        uci_set firewall.allow_easymanet_api_wan.proto="tcp"
        uci_set firewall.allow_easymanet_api_wan.dest_port="$EM_EASYMANET_API_PORT"
        uci_set firewall.allow_easymanet_api_wan.target="ACCEPT"
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

openmanetd_config="$(_prefix_path /etc/openmanetd/config.yml)"
if [ -f "$openmanetd_config" ]; then
    if ! chmod 0600 "$openmanetd_config"; then
        echo "FATAL: failed to secure /etc/openmanetd/config.yml" | tee -a "$LOG_FILE"
        exit 1
    fi
    old_umask="$(umask)"
    umask 077
    # OpenMANETd expects the mesh LAN bridge, not raw bat0, so its
    # gateway route and DNS management sees the same interface as clients.
    if ! cat > "$openmanetd_config" <<EOF
meshNetInterface: "$EM_AHWLAN_BRIDGE"
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
    then
        umask "$old_umask"
        echo "FATAL: failed to write /etc/openmanetd/config.yml" | tee -a "$LOG_FILE"
        exit 1
    fi
    umask "$old_umask"
fi

network_init="$(_prefix_path /etc/init.d/network)"
if [ -x "$network_init" ]; then
    "$network_init" enable 2>/dev/null || true
    "$network_init" restart 2>/dev/null || true
fi
if [ "$EASYMANET_API_CONFIGURED" = "1" ]; then
    uhttpd_init="$(_prefix_path /etc/init.d/uhttpd)"
    if [ -x "$uhttpd_init" ]; then
        echo "Enabling EasyMANET topology API (uhttpd)..." >> "$LOG_FILE"
        "$uhttpd_init" enable 2>/dev/null || true
        "$uhttpd_init" restart 2>/dev/null || "$uhttpd_init" start 2>/dev/null || true
    else
        echo "WARNING: uhttpd init script not found; EasyMANET topology API will not start" >> "$LOG_FILE"
    fi
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

led_status_init="$(_prefix_path /etc/init.d/easymanet-led-status)"
if [ -x "$led_status_init" ]; then
    echo "Enabling EasyMANET LED status..." >> "$LOG_FILE"
    "$led_status_init" enable 2>/dev/null || true
    "$led_status_init" restart 2>/dev/null || "$led_status_init" start 2>/dev/null || true
else
    echo "WARNING: EasyMANET LED status init script not found; LED status will not start" >> "$LOG_FILE"
fi

display_status_init="$(_prefix_path /etc/init.d/easymanet-display-status)"
if [ -x "$display_status_init" ]; then
    echo "Enabling EasyMANET HDMI status display..." >> "$LOG_FILE"
    "$display_status_init" enable 2>/dev/null || true
    "$display_status_init" restart 2>/dev/null || "$display_status_init" start 2>/dev/null || true
else
    echo "WARNING: EasyMANET display status init script not found; HDMI status will not start" >> "$LOG_FILE"
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
