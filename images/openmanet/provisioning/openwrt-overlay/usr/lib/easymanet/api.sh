#!/bin/sh
# Read-only EasyMANET mesh API used by gateways and the desktop Mesh tab.

set -u

SCRIPT_DIR="${EASYMANET_LIB_DIR:-/usr/lib/easymanet}"
# shellcheck source=provision-lib.sh
. "$SCRIPT_DIR/provision-lib.sh"

PROVISION_JSON="${EASYMANET_PROVISION_JSON:-/etc/easymanet/provision.json}"
API_PORT="${EASYMANET_API_PORT:-10411}"
FETCH_TIMEOUT="${EASYMANET_API_FETCH_TIMEOUT:-1}"
MAX_TOPOLOGY_PEER_PROBES="${EASYMANET_API_MAX_TOPOLOGY_PEER_PROBES:-8}"

case "$MAX_TOPOLOGY_PEER_PROBES" in
    ""|*[!0-9]*)
        MAX_TOPOLOGY_PEER_PROBES=8
        ;;
esac

# shellcheck source=api-lib.sh
. "$SCRIPT_DIR/api-lib.sh"

case "${EASYMANET_API_TEST_MODE:-}" in
    parse-neighbors)
        parse_batctl_neighbors
        exit 0
        ;;
    parse-originators)
        parse_batctl_originators
        exit 0
        ;;
    json-escape)
        json_string "$(cat)"
        printf '\n'
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
    status)
        emit_header
        if ! (
            # shellcheck source=status-lib.sh
            . "$SCRIPT_DIR/status-lib.sh" && status_json_body
        ); then
            printf '{"ok":false,"schema_version":1,"support_code":"EM-DIAG-PARTIAL","support_level":"warn","warnings":["EasyMANET status endpoint failed; identity, neighbors, and topology remain independent."],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        fi
        ;;
    *)
        emit_header
        printf '{"ok":false,"code":"not_found","errors":["Unknown EasyMANET API endpoint"],"generated_at":%s}\n' "$(json_string "$(generated_at)")"
        ;;
esac
