#!/bin/sh
# Read-only EasyMANET mesh API used by gateways and the desktop Mesh tab.

set -u

SCRIPT_DIR="${EASYMANET_LIB_DIR:-/usr/lib/easymanet}"
# shellcheck source=provision-lib.sh
. "$SCRIPT_DIR/provision-lib.sh"

PROVISION_JSON="${EASYMANET_PROVISION_JSON:-/etc/easymanet/provision.json}"
API_PORT="${EASYMANET_API_PORT:-10411}"
FETCH_TIMEOUT="${EASYMANET_API_FETCH_TIMEOUT:-2}"

json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

json_string() {
    printf '"%s"' "$(json_escape "$1")"
}

json_get() {
    jsonfilter -s "$1" -e "$2" 2>/dev/null || true
}

lower() {
    printf '%s' "$1" | tr 'A-F' 'a-f'
}

generated_at() {
    date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date
}

node_name() {
    json_val node name
}

node_hostname() {
    json_val node hostname
}

node_role() {
    json_val node role
}

node_target() {
    json_val node target
}

node_ip() {
    json_val node ip
}

mesh_iface() {
    iface="$(uci -q get wireless.mesh0.ifname 2>/dev/null || true)"
    [ -n "$iface" ] || iface="wlan0"
    printf '%s' "$iface"
}

iface_mac() {
    iface="$1"
    cat "/sys/class/net/$iface/address" 2>/dev/null | head -n 1 || true
}

emit_header() {
    if [ -n "${GATEWAY_INTERFACE:-}" ]; then
        printf 'Content-Type: application/json\r\n\r\n'
    fi
}

node_json_body() {
    name="$(node_name)"
    hostname="$(node_hostname)"
    role="$(node_role)"
    target="$(node_target)"
    ipaddr="$(node_ip)"
    printf '{"name":"%s","hostname":"%s","role":"%s","target":"%s","ip":"%s"}' \
        "$(json_escape "$name")" \
        "$(json_escape "$hostname")" \
        "$(json_escape "$role")" \
        "$(json_escape "$target")" \
        "$(json_escape "$ipaddr")"
}

interfaces_json_body() {
    hardif="$(mesh_iface)"
    bat0_mac="$(iface_mac bat0)"
    mesh_mac="$(iface_mac "$hardif")"
    printf '{"bat0_mac":"%s","mesh_iface":"%s","mesh_mac":"%s"}' \
        "$(json_escape "$bat0_mac")" \
        "$(json_escape "$hardif")" \
        "$(json_escape "$mesh_mac")"
}

identity_json_body() {
    cat <<EOF
{"ok":true,"generated_at":"$(generated_at)","node":$(node_json_body),"interfaces":$(interfaces_json_body),"api":{"version":1,"port":$API_PORT}}
EOF
}

is_gateway() {
    [ "$(node_role)" = "gate" ]
}

parse_batctl_neighbors() {
    awk '
    function is_mac(value) {
        return value ~ /^[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]$/
    }
    {
        for (i = 1; i <= NF; i++) {
            if (!is_mac($i)) {
                continue
            }
            iface = i > 1 ? $(i - 1) : ""
            if (iface !~ /^[A-Za-z0-9_.:-]+$/) {
                iface = ""
            }
            mac = tolower($i)
            last_seen = i < NF ? $(i + 1) : ""
            throughput = ""
            for (j = i + 2; j <= NF; j++) {
                throughput = throughput (throughput == "" ? "" : " ") $j
            }
            gsub(/[()]/, "", throughput)
            print iface "\t" mac "\t" last_seen "\t" throughput
            next
        }
    }'
}

parse_batctl_originators() {
    awk '
    function is_mac(value) {
        return value ~ /^[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]$/
    }
    {
        if (!is_mac($1)) {
            next
        }
        originator = tolower($1)
        last_seen = $2
        nexthop = ""
        outgoing_if = ""
        for (i = 3; i <= NF; i++) {
            if (is_mac($i)) {
                nexthop = tolower($i)
            }
            if ($i ~ /^\[[^]]+\]$/) {
                outgoing_if = $i
                gsub(/^\[/, "", outgoing_if)
                gsub(/\]$/, "", outgoing_if)
            }
        }
        print originator "\t" last_seen "\t" nexthop "\t" outgoing_if
    }'
}

json_neighbors_array() {
    first=1
    tab="$(printf '\t')"
    while IFS="$tab" read -r iface mac last_seen throughput; do
        [ -n "$mac" ] || continue
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"iface":%s,"mac":%s,"last_seen":%s,"throughput":%s}' \
            "$(json_string "$iface")" \
            "$(json_string "$mac")" \
            "$(json_string "$last_seen")" \
            "$(json_string "$throughput")"
    done
}

json_originators_array() {
    first=1
    tab="$(printf '\t')"
    while IFS="$tab" read -r originator last_seen nexthop outgoing_if; do
        [ -n "$originator" ] || continue
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"originator":%s,"last_seen":%s,"nexthop":%s,"outgoing_iface":%s}' \
            "$(json_string "$originator")" \
            "$(json_string "$last_seen")" \
            "$(json_string "$nexthop")" \
            "$(json_string "$outgoing_if")"
    done
}

neighbors_json_body() {
    neighbors="$(batctl n 2>/dev/null | parse_batctl_neighbors | json_neighbors_array)"
    originators="$(batctl o 2>/dev/null | parse_batctl_originators | json_originators_array)"
    printf '{"ok":true,"generated_at":%s,"node":%s,"interfaces":%s,"neighbors":[%s],"originators":[%s]}\n' \
        "$(json_string "$(generated_at)")" \
        "$(node_json_body)" \
        "$(interfaces_json_body)" \
        "$neighbors" \
        "$originators"
}

fetch_peer() {
    ipaddr="$1"
    endpoint="$2"
    if ! command -v uclient-fetch >/dev/null 2>&1; then
        return 1
    fi
    uclient-fetch -q -T "$FETCH_TIMEOUT" -O - "http://$ipaddr:$API_PORT/v1/$endpoint" 2>/dev/null || return 1
}

append_node_record() {
    file="$1"
    identity="$2"
    fallback_name="$3"
    fallback_hostname="$4"
    fallback_role="$5"
    fallback_target="$6"
    fallback_ip="$7"
    status="$8"

    name="$(json_get "$identity" '@.node.name')"
    hostname="$(json_get "$identity" '@.node.hostname')"
    role="$(json_get "$identity" '@.node.role')"
    target="$(json_get "$identity" '@.node.target')"
    ipaddr="$(json_get "$identity" '@.node.ip')"
    mesh_mac="$(json_get "$identity" '@.interfaces.mesh_mac')"
    bat0_mac="$(json_get "$identity" '@.interfaces.bat0_mac')"
    [ -n "$name" ] || name="$fallback_name"
    [ -n "$hostname" ] || hostname="$fallback_hostname"
    [ -n "$role" ] || role="$fallback_role"
    [ -n "$target" ] || target="$fallback_target"
    [ -n "$ipaddr" ] || ipaddr="$fallback_ip"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$name" "$hostname" "$role" "$target" "$ipaddr" "$mesh_mac" "$bat0_mac" "$status" >> "$file"
}

append_neighbor_records() {
    file="$1"
    source_name="$2"
    source_mac="$3"
    neighbors="$4"
    i=0
    while :; do
        mac="$(json_get "$neighbors" "@.neighbors[$i].mac")"
        [ -n "$mac" ] || break
        iface="$(json_get "$neighbors" "@.neighbors[$i].iface")"
        last_seen="$(json_get "$neighbors" "@.neighbors[$i].last_seen")"
        throughput="$(json_get "$neighbors" "@.neighbors[$i].throughput")"
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$source_name" "$source_mac" "$mac" "$iface" "$last_seen" "$throughput" >> "$file"
        i=$((i + 1))
    done
}

resolve_node_by_mac() {
    nodes_file="$1"
    mac="$(lower "$2")"
    awk -F '\t' -v mac="$mac" '
        tolower($6) == mac || tolower($7) == mac { print $1; exit }
    ' "$nodes_file"
}

nodes_json_from_file() {
    file="$1"
    first=1
    tab="$(printf '\t')"
    while IFS="$tab" read -r name hostname role target ipaddr mesh_mac bat0_mac status; do
        [ -n "$name$ipaddr" ] || continue
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"name":%s,"hostname":%s,"role":%s,"target":%s,"ip":%s,"mesh_mac":%s,"bat0_mac":%s,"status":%s}' \
            "$(json_string "$name")" \
            "$(json_string "$hostname")" \
            "$(json_string "$role")" \
            "$(json_string "$target")" \
            "$(json_string "$ipaddr")" \
            "$(json_string "$mesh_mac")" \
            "$(json_string "$bat0_mac")" \
            "$(json_string "$status")"
    done < "$file"
}

links_json_from_file() {
    links_file="$1"
    nodes_file="$2"
    first=1
    tab="$(printf '\t')"
    while IFS="$tab" read -r source source_mac neighbor_mac iface last_seen throughput; do
        [ -n "$source$neighbor_mac" ] || continue
        target="$(resolve_node_by_mac "$nodes_file" "$neighbor_mac")"
        status="resolved"
        [ -n "$target" ] || status="unresolved"
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"source":%s,"target":%s,"source_mac":%s,"target_mac":%s,"iface":%s,"last_seen":%s,"throughput":%s,"status":%s}' \
            "$(json_string "$source")" \
            "$(json_string "$target")" \
            "$(json_string "$source_mac")" \
            "$(json_string "$neighbor_mac")" \
            "$(json_string "$iface")" \
            "$(json_string "$last_seen")" \
            "$(json_string "$throughput")" \
            "$(json_string "$status")"
    done < "$links_file"
}

topology_json_body() {
    if ! is_gateway; then
        printf '{"ok":false,"code":"not_gateway","errors":["Topology is only available from gate nodes"],"nodes":[],"links":[],"warnings":[],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        return 0
    fi

    tmp_dir="/tmp/easymanet-topology.$$"
    nodes_file="$tmp_dir/nodes.tsv"
    links_file="$tmp_dir/links.tsv"
    warnings_file="$tmp_dir/warnings.txt"
    mkdir -p "$tmp_dir"
    : > "$nodes_file"
    : > "$links_file"
    : > "$warnings_file"

    self_name="$(node_name)"
    self_ip="$(node_ip)"
    i=0
    while :; do
        peer_name="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$i].name" 2>/dev/null || true)"
        [ -n "$peer_name" ] || break
        peer_hostname="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$i].hostname" 2>/dev/null || true)"
        peer_role="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$i].role" 2>/dev/null || true)"
        peer_target="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$i].target" 2>/dev/null || true)"
        peer_ip="$(jsonfilter -i "$PROVISION_JSON" -e "@.fleet.nodes[$i].ip" 2>/dev/null || true)"

        identity=""
        neighbors=""
        status="online"
        if [ "$peer_name" = "$self_name" ] || [ "$peer_ip" = "$self_ip" ]; then
            identity="$(identity_json_body)"
            neighbors="$(neighbors_json_body)"
        elif [ -n "$peer_ip" ]; then
            identity="$(fetch_peer "$peer_ip" identity || true)"
            if [ "$(json_get "$identity" '@.ok')" = "true" ]; then
                neighbors="$(fetch_peer "$peer_ip" neighbors || true)"
            else
                status="offline"
                echo "$peer_name did not answer topology API at $peer_ip" >> "$warnings_file"
            fi
        else
            status="offline"
            echo "$peer_name has no mesh IP in fleet inventory" >> "$warnings_file"
        fi

        append_node_record "$nodes_file" "$identity" "$peer_name" "$peer_hostname" "$peer_role" "$peer_target" "$peer_ip" "$status"
        if [ "$status" = "online" ] && [ -n "$neighbors" ]; then
            source_name="$(json_get "$identity" '@.node.name')"
            source_mac="$(json_get "$identity" '@.interfaces.mesh_mac')"
            [ -n "$source_name" ] || source_name="$peer_name"
            append_neighbor_records "$links_file" "$source_name" "$source_mac" "$neighbors"
        fi
        i=$((i + 1))
    done

    if [ "$i" -eq 0 ]; then
        identity="$(identity_json_body)"
        neighbors="$(neighbors_json_body)"
        append_node_record "$nodes_file" "$identity" "$self_name" "$(node_hostname)" "$(node_role)" "$(node_target)" "$self_ip" "online"
        append_neighbor_records "$links_file" "$self_name" "$(json_get "$identity" '@.interfaces.mesh_mac')" "$neighbors"
    fi

    nodes_json="$(nodes_json_from_file "$nodes_file")"
    links_json="$(links_json_from_file "$links_file" "$nodes_file")"
    warnings_json="$(json_warnings_array "$warnings_file")"
    gateway_json="$(identity_json_body)"
    rm -rf "$tmp_dir"

    printf '{"ok":true,"generated_at":%s,"gateway":%s,"nodes":[%s],"links":[%s],"warnings":[%s]}\n' \
        "$(json_string "$(generated_at)")" \
        "$gateway_json" \
        "$nodes_json" \
        "$links_json" \
        "$warnings_json"
}

json_warnings_array() {
    file="$1"
    first=1
    while IFS= read -r warning; do
        [ -n "$warning" ] || continue
        [ "$first" -eq 1 ] || printf ','
        first=0
        json_string "$warning"
    done < "$file"
}

case "${EASYMANET_API_TEST_MODE:-}" in
    parse-neighbors)
        parse_batctl_neighbors
        exit 0
        ;;
    parse-originators)
        parse_batctl_originators
        exit 0
        ;;
esac

endpoint="${1:-}"
case "$endpoint" in
    identity)
        emit_header
        identity_json_body
        ;;
    neighbors)
        emit_header
        neighbors_json_body
        ;;
    topology)
        emit_header
        topology_json_body
        ;;
    *)
        emit_header
        printf '{"ok":false,"code":"not_found","errors":["Unknown EasyMANET API endpoint"],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        ;;
esac
