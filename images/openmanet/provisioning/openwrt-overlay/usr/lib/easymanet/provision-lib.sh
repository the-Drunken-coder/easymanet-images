#!/bin/sh
# Shared EasyMANET provisioning helpers (JSON parsing, radio detection).

_easymanet_provision_json_file() {
    if [ -n "${PROVISION_JSON:-}" ]; then
        printf '%s' "$PROVISION_JSON"
        return 0
    fi
    if [ -n "${EASYMANET_PROVISION_JSON:-}" ]; then
        printf '%s' "$EASYMANET_PROVISION_JSON"
        return 0
    fi
    printf '%s' "/etc/easymanet/provision.json"
}

json_path() {
    path="@"
    for key in "$@"; do
        path="${path}.${key}"
    done
    printf '%s' "$path"
}

json_val() {
    jsonfilter -i "$(_easymanet_provision_json_file)" -e "$(json_path "$@")" 2>/dev/null || true
}

json_bool() {
    case "$(json_val "$@")" in
        1|true|TRUE|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

find_morse_radio() {
    radio="$(uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.type='morse'$/\1/p" | head -n 1)"
    if [ -n "$radio" ]; then
        printf '%s' "$radio"
        return 0
    fi

    uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.hwmode='11ah'$/\1/p" | head -n 1
}

find_local_ap_radio() {
    for radio in $(uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.type='mac80211'$/\1/p"); do
        path="$(uci -q get wireless."$radio".path || true)"
        band="$(uci -q get wireless."$radio".band || true)"
        case "$path" in
            *mmc_host*|*mmc1*)
                if [ "$band" = "2g" ] || [ "$band" = "5g" ]; then
                    printf '%s' "$radio"
                    return 0
                fi
                ;;
        esac
    done

    for radio in $(uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.type='mac80211'$/\1/p"); do
        band="$(uci -q get wireless."$radio".band || true)"
        if [ "$band" = "2g" ]; then
            printf '%s' "$radio"
            return 0
        fi
    done

    uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.type='mac80211'$/\1/p" | head -n 1
}

delete_ifaces_for_radio() {
    radio="$1"
    for iface in $(uci show wireless | sed -n "s/^wireless\.\([^.=]*\)\.device='$radio'$/\1/p"); do
        uci -q delete wireless."$iface"
    done
}
