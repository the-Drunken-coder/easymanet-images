#!/bin/sh
# Runtime helpers for the EasyMANET first-boot provisioning entrypoint.

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
    api_script="$(_prefix_path /usr/lib/easymanet/api.sh)"
    EASYMANET_API_CONFIGURED=0
    uci -q delete uhttpd.easymanet_api 2>/dev/null || true
    if [ ! -x "$api_script" ] || [ ! -x "$api_home/v1/identity" ] || [ ! -x "$api_home/v1/topology" ] || [ ! -x "$api_home/v1/neighbors" ]; then
        echo "WARNING: EasyMANET API endpoint wrappers are missing; skipping API setup" >> "$LOG_FILE"
        uci_commit uhttpd
        return 0
    fi
    if [ ! -x "$api_home/v1/status" ]; then
        echo "WARNING: EasyMANET status endpoint wrapper is missing; continuing without /v1/status" >> "$LOG_FILE"
    fi

    echo "Configuring EasyMANET topology API on port $EM_EASYMANET_API_PORT..." >> "$LOG_FILE"
    uci_set uhttpd.easymanet_api=uhttpd
    uci_set uhttpd.easymanet_api.home="$api_home"
    uci_set uhttpd.easymanet_api.cgi_prefix="/v1"
    uci_set uhttpd.easymanet_api.script_timeout="10"
    uci_set uhttpd.easymanet_api.network_timeout="10"
    uci_set uhttpd.easymanet_api.http_keepalive="0"
    uci_set uhttpd.easymanet_api.tcp_keepalive="1"
    if [ "$NODE_ROLE" = "gate" ]; then
        uci_add_list uhttpd.easymanet_api.listen_http="$EM_LAN_FALLBACK_IP:$EM_EASYMANET_API_PORT"
        uci_add_list uhttpd.easymanet_api.listen_http="$NODE_IP:$EM_EASYMANET_API_PORT"
    else
        uci_add_list uhttpd.easymanet_api.listen_http="$NODE_IP:$EM_EASYMANET_API_PORT"
    fi
    uci_commit uhttpd
    # provision.sh reads this after network restart to decide whether to start uhttpd.
    EASYMANET_API_CONFIGURED=1
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
