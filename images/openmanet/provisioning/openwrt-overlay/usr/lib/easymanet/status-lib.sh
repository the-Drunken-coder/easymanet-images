#!/bin/sh
# Shared EasyMANET node status, support-code, and HDMI text helpers.
# Requires provision-lib.sh, api-lib.sh, and PROVISION_JSON to be initialized by
# the caller; this file is intentionally a small sourced library, not a daemon.

: "${EASYMANET_STATUS_SCHEMA:=1}"
: "${EASYMANET_INTERNET_TARGETS:=1.1.1.1 8.8.8.8}"
: "${EASYMANET_INTERNET_PING_TIMEOUT:=2}"
: "${EASYMANET_DISPLAY_INTERVAL:=5}"
: "${EASYMANET_DISPLAY_TTY:=/dev/tty1}"
: "${EASYMANET_PROVISIONED_FLAG:=/etc/easymanet/provisioned}"

status_bool() {
    if "$@" >/dev/null 2>&1; then
        printf 'true'
    else
        printf 'false'
    fi
}

status_provisioned() {
    [ -s "$EASYMANET_PROVISIONED_FLAG" ]
}

status_neighbor_count() {
    if ! command -v batctl >/dev/null 2>&1; then
        printf '0'
        return 0
    fi
    batctl n 2>/dev/null | parse_batctl_neighbors | awk 'NF { count++ } END { print count + 0 }'
}

status_public_internet() {
    if ! command -v ping >/dev/null 2>&1; then
        return 1
    fi
    for target in $EASYMANET_INTERNET_TARGETS; do
        ping -c 1 -w "$EASYMANET_INTERNET_PING_TIMEOUT" "$target" >/dev/null 2>&1 && return 0
    done
    return 1
}

status_manageable() {
    status_provisioned || return 1
    script_dir="${SCRIPT_DIR:-/usr/lib/easymanet}"
    api_home="${EASYMANET_API_HOME:-/www/easymanet-api}"
    [ -x "${EASYMANET_API_SCRIPT:-$script_dir/api.sh}" ] || return 1
    [ -x "$api_home/v1/status" ] || return 1
}

status_support_code() {
    neighbor_count="$1"
    internet_ok="$2"
    missing_count="$3"
    if ! status_provisioned; then
        printf 'EM-BOOT-INCOMPLETE'
    elif [ "${missing_count:-0}" -gt 0 ]; then
        printf 'EM-NODE-MISSING'
    elif [ "${neighbor_count:-0}" -eq 0 ]; then
        printf 'EM-MESH-DOWN'
    elif [ "$internet_ok" != "true" ]; then
        printf 'EM-INET-DOWN'
    else
        printf 'EM-OK'
    fi
}

status_support_level() {
    case "$1" in
        EM-OK) printf 'ok' ;;
        EM-BOOT-INCOMPLETE|EM-MESH-DOWN|EM-INET-DOWN|EM-NODE-MISSING|EM-API-DOWN) printf 'warn' ;;
        *) printf 'warn' ;;
    esac
}

status_warnings_json() {
    code="$1"
    neighbor_count="$2"
    internet_ok="$3"
    manageable_ok="$4"
    missing_count="$5"
    case "$neighbor_count" in
        ""|*[!0-9]*) neighbor_count=0 ;;
    esac
    case "$missing_count" in
        ""|*[!0-9]*) missing_count=0 ;;
    esac
    first=1
    printf '['
    status_add_warning() {
        [ "$first" -eq 1 ] || printf ','
        first=0
        json_string "$1"
    }
    [ "$code" = "EM-BOOT-INCOMPLETE" ] && status_add_warning "Node has not completed EasyMANET provisioning."
    [ "$neighbor_count" -eq 0 ] && status_add_warning "No BATMAN mesh neighbors are visible."
    [ "$internet_ok" = "true" ] || status_add_warning "Public internet check failed for configured targets."
    [ "$manageable_ok" = "true" ] || status_add_warning "EasyMANET management API is not fully available."
    [ "$missing_count" -gt 0 ] && status_add_warning "$missing_count expected fleet node(s) are missing."
    printf ']'
}

status_fleet_json() {
    missing_count_file="$1"
    : > "$missing_count_file"
    if ! is_gateway; then
        printf '[]'
        return 0
    fi

    topology="$(topology_json_body 2>/dev/null || true)"
    if [ "$(json_get "$topology" '@.ok')" != "true" ]; then
        status_unknown_fleet_json
        return 0
    fi

    first=1
    index=0
    printf '['
    while :; do
        name="$(json_get "$topology" "@.nodes[$index].name")"
        [ -n "$name" ] || break
        raw_status="$(json_get "$topology" "@.nodes[$index].status")"
        case "$raw_status" in
            online) state="OK" ;;
            offline)
                state="MISSING"
                printf '%s\n' missing >> "$missing_count_file"
                ;;
            *) state="UNKNOWN" ;;
        esac
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"name":%s,"status":%s}' "$(json_string "$name")" "$(json_string "$state")"
        index=$((index + 1))
    done
    printf ']'
}

status_unknown_fleet_json() {
    first=1
    index=0
    printf '['
    while :; do
        name="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$index].name" 2>/dev/null || true)"
        [ -n "$name" ] || break
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"name":%s,"status":"UNKNOWN"}' "$(json_string "$name")"
        index=$((index + 1))
    done
    printf ']'
}

status_json_body() {
    neighbor_count="$(status_neighbor_count)"
    internet_ok="$(status_bool status_public_internet)"
    manageable_ok="$(status_bool status_manageable)"
    mesh_ok=false
    [ "$neighbor_count" -gt 0 ] && mesh_ok=true
    tmp_missing="$(mktemp /tmp/easymanet-status-missing.XXXXXX 2>/dev/null || true)"
    if [ -n "$tmp_missing" ]; then
        fleet_json="$(status_fleet_json "$tmp_missing")"
        missing_count="$(wc -l < "$tmp_missing" 2>/dev/null | tr -d ' ')"
        rm -f "$tmp_missing"
    else
        fleet_json="[]"
        missing_count=0
    fi
    case "$missing_count" in
        ""|*[!0-9]*) missing_count=0 ;;
    esac
    code="$(status_support_code "$neighbor_count" "$internet_ok" "$missing_count")"
    level="$(status_support_level "$code")"
    printf '{"ok":true,"schema_version":%s,"generated_at":%s,"support_code":%s,"support_level":%s,"node":%s,"interfaces":%s,"mesh":{"ok":%s,"neighbor_count":%s},"internet":{"ok":%s,"targets":%s},"manageability":{"ok":%s},"fleet":%s,"warnings":%s}\n' \
        "$EASYMANET_STATUS_SCHEMA" \
        "$(json_string "$(generated_at)")" \
        "$(json_string "$code")" \
        "$(json_string "$level")" \
        "$(node_json_body)" \
        "$(interfaces_json_body)" \
        "$mesh_ok" \
        "$neighbor_count" \
        "$internet_ok" \
        "$(json_string "$EASYMANET_INTERNET_TARGETS")" \
        "$manageable_ok" \
        "$fleet_json" \
        "$(status_warnings_json "$code" "$neighbor_count" "$internet_ok" "$manageable_ok" "$missing_count")"
}

status_label() {
    if [ "$1" = "true" ]; then
        printf 'OK'
    else
        printf 'DOWN'
    fi
}

status_color() {
    case "$1" in
        OK|EM-OK) printf '\033[32m' ;;
        MISSING|DOWN|EM-*) printf '\033[31m' ;;
        *) printf '\033[33m' ;;
    esac
}

status_reset_color() {
    printf '\033[0m'
}

memory_text() {
    mem_total="$1"
    mem_avail="$2"
    if [ "$mem_total" -gt 0 ] 2>/dev/null; then
        mem_used=$((mem_total - mem_avail))
        mem_pct="$(( mem_used * 100 / mem_total ))%"
        mem_used_mib=$(( mem_used / 1024 ))
        mem_total_mib=$(( mem_total / 1024 ))
        printf '%s/%sMiB (%s)' "$mem_used_mib" "$mem_total_mib" "$mem_pct"
    else
        printf 'unknown'
    fi
}

resources_text() {
    # Load average (1/5/15 min)
    load="$(awk '{print $1"/"$2"/"$3}' /proc/loadavg 2>/dev/null || true)"

    # Memory: used / total in MiB
    mem_total=0; mem_avail=0
    while read -r key value _; do
        case "$key" in
            MemTotal:) mem_total="$value" ;;
            MemAvailable:) mem_avail="$value" ;;
            MemFree:)
                [ "$mem_avail" -eq 0 ] 2>/dev/null && mem_avail="$value"
                ;;
        esac
    done < /proc/meminfo 2>/dev/null || true
    mem_str="$(memory_text "$mem_total" "$mem_avail")"

    # Rootfs usage
    disk_pct="$(df / 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')"
    [ -n "$disk_pct" ] && disk_str="${disk_pct}%" || disk_str="unknown"

    printf 'LOAD %s | MEM %s | DISK %s' "$load" "$mem_str" "$disk_str"
}

render_status_text() {
    payload="${1:-$(status_json_body)}"
    name="$(json_get "$payload" '@.node.name')"
    role="$(json_get "$payload" '@.node.role' | tr '[:lower:]' '[:upper:]')"
    ipaddr="$(json_get "$payload" '@.node.ip')"
    code="$(json_get "$payload" '@.support_code')"
    mesh_ok="$(json_get "$payload" '@.mesh.ok')"
    neighbor_count="$(json_get "$payload" '@.mesh.neighbor_count')"
    internet_ok="$(json_get "$payload" '@.internet.ok')"
    manageable_ok="$(json_get "$payload" '@.manageability.ok')"
    mesh_label="$(status_label "$mesh_ok")"
    internet_label="$(status_label "$internet_ok")"
    manage_label="$(status_label "$manageable_ok")"

    printf '\033[2J\033[H'
    printf '%s  %s  %s\n\n' "$name" "$role" "$ipaddr"
    printf 'NODE %sOK%s | MESH %s%s%s %s neighbors | INTERNET %s%s%s | MANAGE %s%s%s\n' \
        "$(status_color OK)" "$(status_reset_color)" \
        "$(status_color "$mesh_label")" "$mesh_label" "$(status_reset_color)" "$neighbor_count" \
        "$(status_color "$internet_label")" "$internet_label" "$(status_reset_color)" \
        "$(status_color "$manage_label")" "$manage_label" "$(status_reset_color)"
    printf 'CODE %s%s%s\n' "$(status_color "$code")" "$code" "$(status_reset_color)"
    printf '%s\n' "$(resources_text)"

    if [ "$(json_get "$payload" '@.node.role')" = "gate" ]; then
        printf '\nFLEET\n'
        index=0
        while :; do
            fleet_name="$(json_get "$payload" "@.fleet[$index].name")"
            [ -n "$fleet_name" ] || break
            fleet_status="$(json_get "$payload" "@.fleet[$index].status")"
            printf '%-12s %s%s%s\n' "$fleet_name" "$(status_color "$fleet_status")" "$fleet_status" "$(status_reset_color)"
            index=$((index + 1))
        done
    fi
}
