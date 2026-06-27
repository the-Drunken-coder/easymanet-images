#!/bin/sh
# EasyMANET network helpers shared by provisioning and late boot repair.

EASYMANET_NETWORK_LOG="${EASYMANET_NETWORK_LOG:-/var/log/easymanet-network.log}"
EASYMANET_PROVISION_JSON="${EASYMANET_PROVISION_JSON:-/etc/easymanet/provision.json}"
: "${EM_MESH_NETMASK:=255.255.0.0}"
: "${EM_UPLINK_DNS:=1.1.1.1 8.8.8.8}"
: "${EM_AHWLAN_IFACE:=ahwlan}"
: "${EM_AHWLAN_BRIDGE:=br-ahwlan}"

SCRIPT_DIR="${EASYMANET_LIB_DIR:-/usr/lib/easymanet}"
# shellcheck source=provision-lib.sh
. "$SCRIPT_DIR/provision-lib.sh"
PROVISION_JSON="$EASYMANET_PROVISION_JSON"

easymanet_log_network() {
    echo "[$(date)] $*" >> "$EASYMANET_NETWORK_LOG"
}

easymanet_json_path() {
    json_path "$@"
}

easymanet_json_val() {
    json_val "$@"
}

easymanet_find_network_device_section() {
    bridge_name="$1"
    uci show network | sed -n "s/^network\.\([^.=]*\)\.name='$bridge_name'$/\1/p" | head -n 1
}

easymanet_delete_network_device_by_name() {
    bridge_name="$1"
    bridge="$(easymanet_find_network_device_section "$bridge_name")"
    if [ -n "$bridge" ]; then
        uci -q delete network."$bridge" 2>/dev/null || true
    fi
}

easymanet_eth0_mesh_side() {
    role="$(easymanet_json_val node role 2>/dev/null || true)"
    uplink="$(easymanet_json_val node gateway uplink_interface 2>/dev/null || true)"
    [ -n "$uplink" ] || uplink="eth0"
    wifi_uplink=0
    if json_bool node gateway wifi enabled; then
        wifi_uplink=1
    fi

    if [ "$role" = "gate" ] && [ "$wifi_uplink" -ne 1 ] && [ "$uplink" = "eth0" ]; then
        return 1
    fi
    return 0
}

easymanet_ensure_ahwlan_bridge() {
    bridge="$(easymanet_find_network_device_section "$EM_AHWLAN_BRIDGE")"
    [ -n "$bridge" ] || bridge="ahwlan_dev"

    # OpenMANET uses br-ahwlan as the mesh LAN. The late repair must keep
    # Ethernet on that bridge, not recreate EasyMANET's old br-lan split.
    uci set network."$bridge"=device >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network."$bridge".name="$EM_AHWLAN_BRIDGE" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network."$bridge".type="bridge" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci -q delete network."$bridge".ports 2>/dev/null || true
    uci add_list network."$bridge".ports="bat0" >> "$EASYMANET_NETWORK_LOG" 2>&1
    if easymanet_eth0_mesh_side; then
        uci add_list network."$bridge".ports="eth0" >> "$EASYMANET_NETWORK_LOG" 2>&1
    fi
}

easymanet_ensure_ahwlan_interface() {
    node_ip="$(easymanet_json_val node ip 2>/dev/null || true)"
    uci set network."$EM_AHWLAN_IFACE"=interface >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network."$EM_AHWLAN_IFACE".proto="static" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network."$EM_AHWLAN_IFACE".device="$EM_AHWLAN_BRIDGE" >> "$EASYMANET_NETWORK_LOG" 2>&1
    if [ -n "$node_ip" ]; then
        uci set network."$EM_AHWLAN_IFACE".ipaddr="$node_ip" >> "$EASYMANET_NETWORK_LOG" 2>&1
    fi
    uci set network."$EM_AHWLAN_IFACE".netmask="$EM_MESH_NETMASK" >> "$EASYMANET_NETWORK_LOG" 2>&1
}

easymanet_restore_gateway_wan() {
    role="$(easymanet_json_val node role 2>/dev/null || true)"
    [ "$role" = "gate" ] || return 0

    uci set network.wan=interface >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network.wan.proto="dhcp" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network.wan.peerdns="0" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network.wan.dns="$EM_UPLINK_DNS" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci -q delete network.wan6 2>/dev/null || true

    if json_bool node gateway wifi enabled; then
        uci -q delete network.wan.device 2>/dev/null || true
        uci -q delete network.wan.ifname 2>/dev/null || true
        return 0
    fi

    uplink="$(easymanet_json_val node gateway uplink_interface 2>/dev/null || true)"
    [ -n "$uplink" ] || uplink="eth0"
    uci set network.wan.device="$uplink" >> "$EASYMANET_NETWORK_LOG" 2>&1
    uci set network.wan.ifname="$uplink" >> "$EASYMANET_NETWORK_LOG" 2>&1
}

easymanet_repair_management_lan() {
    reason="${1:-manual}"
    mgmt_iface="eth0"

    role="$(easymanet_json_val node role 2>/dev/null || true)"
    uplink="$(easymanet_json_val node gateway uplink_interface 2>/dev/null || true)"
    [ -n "$uplink" ] || uplink="eth0"

    easymanet_log_network "ensuring mesh-side access uses $EM_AHWLAN_BRIDGE reason=$reason role=$role uplink=$uplink"

    uci -q delete network.lan 2>/dev/null || true
    easymanet_delete_network_device_by_name br-lan

    # eth0 belongs to WAN only when this gateway selected it as the uplink.
    # Otherwise stale WAN config on eth0 or a legacy bridge fights br-ahwlan.
    wan_device="$(uci -q get network.wan.device || true)"
    wan_ifname="$(uci -q get network.wan.ifname || true)"
    wan_uses_mesh_eth=0
    if easymanet_eth0_mesh_side; then
        if [ "$wan_device" = "$mgmt_iface" ] || [ "$wan_ifname" = "$mgmt_iface" ]; then
            wan_uses_mesh_eth=1
        elif [ "$wan_device" = "br-lan" ] || [ "$wan_ifname" = "br-lan" ]; then
            wan_uses_mesh_eth=1
        elif [ "$wan_device" = "$EM_AHWLAN_BRIDGE" ] || [ "$wan_ifname" = "$EM_AHWLAN_BRIDGE" ]; then
            wan_uses_mesh_eth=1
        fi
    fi
    if [ "$wan_uses_mesh_eth" = "1" ]; then
        /sbin/ifdown wan >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
        /sbin/ifdown wan6 >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
        uci -q delete network.wan 2>/dev/null || true
        uci -q delete network.wan6 2>/dev/null || true
    fi

    # If stale mesh-side WAN was removed, rebuild gateway WAN from the
    # provision payload for eth0, non-eth0, and Wi-Fi uplink gateways.
    easymanet_restore_gateway_wan

    easymanet_ensure_ahwlan_bridge
    easymanet_ensure_ahwlan_interface
    uci commit network >> "$EASYMANET_NETWORK_LOG" 2>&1
    /sbin/ifup "$EM_AHWLAN_IFACE" >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
    ubus call network reload >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
    ip link set bat0 up >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
    brctl addif "$EM_AHWLAN_BRIDGE" bat0 >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
    if easymanet_eth0_mesh_side; then
        ip link set "$mgmt_iface" up >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
        brctl addif "$EM_AHWLAN_BRIDGE" "$mgmt_iface" >> "$EASYMANET_NETWORK_LOG" 2>&1 || true
    fi
}
