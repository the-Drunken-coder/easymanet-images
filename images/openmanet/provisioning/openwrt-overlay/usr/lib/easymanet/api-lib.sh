#!/bin/sh
# JSON, BATMAN, and topology helpers for the EasyMANET mesh API entrypoint.

json_escape() {
    printf '%s' "$1" | awk 'BEGIN { ORS = "" }
    {
        if (NR > 1) {
            printf "\\n"
        }
        value = $0
        gsub(/\\/, "\\\\", value)
        gsub(/"/, "\\\"", value)
        gsub(/\t/, "\\t", value)
        gsub(/\r/, "\\r", value)
        printf "%s", value
    }'
}

json_string() {
    printf '"%s"' "$(json_escape "$1")"
}

json_get() {
    jsonfilter -s "$1" -e "$2" 2>/dev/null || true
}

is_mac_address() {
    printf '%s' "$1" | grep -Eq '^[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]$'
}

lower() {
    printf '%s' "$1" | tr 'A-F' 'a-f'
}

generated_at() {
    date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date
}

# Node accessors require json_val from provision-lib.sh. The api.sh entrypoint
# must source provision-lib.sh before api-lib.sh.
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
    case "$iface" in
        ""|*[!a-zA-Z0-9_-]*)
            return 1
            ;;
    esac
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
    function clean_iface(value) {
        gsub(/^\[/, "", value)
        gsub(/\]$/, "", value)
        return value
    }
    function clean_metric(value) {
        gsub(/[()]/, "", value)
        return value
    }
    {
        if (is_mac($1)) {
            mac = tolower($1)
            last_seen = NF >= 2 ? $2 : ""
            throughput = ""
            iface = ""
            for (i = 3; i <= NF; i++) {
                if ($i == "[" && i < NF) {
                    iface = clean_iface($(i + 1))
                    i++
                    continue
                }
                if ($i ~ /^\[/) {
                    iface = clean_iface($i)
                    continue
                }
                if ($i ~ /\]$/) {
                    iface = clean_iface($i)
                    continue
                }
                value = clean_metric($i)
                if (value != "") {
                    throughput = throughput (throughput == "" ? "" : " ") value
                }
            }
            print iface "\t" mac "\t" last_seen "\t" throughput
            next
        }
        if (NF >= 2 && is_mac($2)) {
            iface = $1
            mac = tolower($2)
            last_seen = NF >= 3 ? $3 : ""
            throughput = ""
            for (i = 4; i <= NF; i++) {
                value = clean_metric($i)
                if (value != "") {
                    throughput = throughput (throughput == "" ? "" : " ") value
                }
            }
            print iface "\t" mac "\t" last_seen "\t" throughput
        }
    }'
}

parse_batctl_originators() {
    awk '
    function is_mac(value) {
        return value ~ /^[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]$/
    }
    function clean_iface(value) {
        gsub(/^\[/, "", value)
        gsub(/\]$/, "", value)
        return value
    }
    {
        originator_idx = 0
        for (i = 1; i <= NF; i++) {
            if (is_mac($i)) {
                originator_idx = i
                break
            }
        }
        if (originator_idx == 0) {
            next
        }
        originator = tolower($originator_idx)
        last_seen = originator_idx < NF ? $(originator_idx + 1) : ""
        nexthop = ""
        outgoing_if = ""
        for (i = originator_idx + 2; i <= NF; i++) {
            if (is_mac($i)) {
                nexthop = tolower($i)
            }
            if ($i == "[" && i < NF) {
                outgoing_if = clean_iface($(i + 1))
                i++
                continue
            }
            if ($i ~ /^\[/ || $i ~ /\]$/) {
                outgoing_if = clean_iface($i)
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

record_sep() {
    printf '|'
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
    sep="$(record_sep)"
    printf '%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n' \
        "$name" "$sep" "$hostname" "$sep" "$role" "$sep" "$target" "$sep" \
        "$ipaddr" "$sep" "$mesh_mac" "$sep" "$bat0_mac" "$sep" "$status" >> "$file"
}

append_neighbor_records() {
    file="$1"
    source_name="$2"
    source_mac="$3"
    neighbors="$4"
    neighbor_index=0
    sep="$(record_sep)"
    while :; do
        mac="$(json_get "$neighbors" "@.neighbors[$neighbor_index].mac")"
        [ -n "$mac" ] || break
        if ! is_mac_address "$mac"; then
            neighbor_index=$((neighbor_index + 1))
            continue
        fi
        iface="$(json_get "$neighbors" "@.neighbors[$neighbor_index].iface")"
        last_seen="$(json_get "$neighbors" "@.neighbors[$neighbor_index].last_seen")"
        throughput="$(json_get "$neighbors" "@.neighbors[$neighbor_index].throughput")"
        printf '%s%s%s%s%s%s%s%s%s%s%s\n' \
            "$source_name" "$sep" "$source_mac" "$sep" "$mac" "$sep" \
            "$iface" "$sep" "$last_seen" "$sep" "$throughput" >> "$file"
        neighbor_index=$((neighbor_index + 1))
    done
}

resolve_node_by_mac() {
    nodes_file="$1"
    mac="$(lower "$2")"
    awk -F '[|]' -v mac="$mac" '
        tolower($6) == mac || tolower($7) == mac { print $1; exit }
    ' "$nodes_file"
}

nodes_json_from_file() {
    file="$1"
    first=1
    sep="$(record_sep)"
    while IFS="$sep" read -r name hostname role target ipaddr mesh_mac bat0_mac status; do
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
    sep="$(record_sep)"
    while IFS="$sep" read -r source source_mac neighbor_mac iface last_seen throughput; do
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

    tmp_dir="$(mktemp -d /tmp/easymanet-topology.XXXXXX 2>/dev/null || true)"
    if [ -z "$tmp_dir" ]; then
        printf '{"ok":false,"code":"scratch_init_failed","errors":["Failed to allocate topology scratch directory"],"nodes":[],"links":[],"warnings":[],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        return 0
    fi
    trap 'rm -rf "$tmp_dir"' EXIT INT TERM
    nodes_file="$tmp_dir/nodes.tsv"
    links_file="$tmp_dir/links.tsv"
    warnings_file="$tmp_dir/warnings.txt"
    if ! : > "$nodes_file" || ! : > "$links_file" || ! : > "$warnings_file"; then
        trap - EXIT INT TERM
        rm -rf "$tmp_dir"
        printf '{"ok":false,"code":"scratch_init_failed","errors":["Failed to initialize topology scratch files"],"nodes":[],"links":[],"warnings":[],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        return 0
    fi

    self_name="$(node_name)"
    self_ip="$(node_ip)"
    peer_probes=0
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
            if [ "$peer_probes" -ge "$MAX_TOPOLOGY_PEER_PROBES" ]; then
                status="offline"
                echo "$peer_name skipped after topology probe limit ($MAX_TOPOLOGY_PEER_PROBES)" >> "$warnings_file"
            else
                peer_probes=$((peer_probes + 1))
                identity="$(fetch_peer "$peer_ip" identity || true)"
                if [ "$(json_get "$identity" '@.ok')" = "true" ]; then
                    neighbors="$(fetch_peer "$peer_ip" neighbors || true)"
                else
                    status="offline"
                    echo "$peer_name did not answer topology API at $peer_ip" >> "$warnings_file"
                fi
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
    trap - EXIT INT TERM
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
