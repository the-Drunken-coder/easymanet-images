#!/bin/sh
# Render a simple EasyMANET status screen to an attached HDMI console.

set -u

SCRIPT_DIR="${EASYMANET_LIB_DIR:-/usr/lib/easymanet}"
PROVISION_JSON="${EASYMANET_PROVISION_JSON:-/etc/easymanet/provision.json}"
API_PORT="${EASYMANET_API_PORT:-10411}"
FETCH_TIMEOUT="${EASYMANET_API_FETCH_TIMEOUT:-1}"
MAX_TOPOLOGY_PEER_PROBES="${EASYMANET_API_MAX_TOPOLOGY_PEER_PROBES:-8}"
: "${EASYMANET_DISPLAY_INTERVAL:=5}"
: "${EASYMANET_DISPLAY_TTY:=/dev/tty1}"
: "${EASYMANET_DISPLAY_LOG:=/var/log/easymanet-display-status.log}"

MODE="loop"
case "${1:-}" in
    "")
        ;;
    --once)
        MODE="once"
        ;;
    *)
        echo "usage: $0 [--once]" >&2
        exit 2
        ;;
esac

case "$EASYMANET_DISPLAY_INTERVAL" in
    ""|0|*[!0-9]*)
        EASYMANET_DISPLAY_INTERVAL=5
        ;;
esac

log_display() {
    mkdir -p "$(dirname "$EASYMANET_DISPLAY_LOG")" 2>/dev/null || true
    printf '[%s] %s\n' "$(date)" "$*" >> "$EASYMANET_DISPLAY_LOG" 2>/dev/null || true
}

if [ ! -c "$EASYMANET_DISPLAY_TTY" ] && [ "$MODE" != "once" ]; then
    log_display "display tty $EASYMANET_DISPLAY_TTY not available"
    while true; do
        sleep "$EASYMANET_DISPLAY_INTERVAL"
    done
fi

if [ ! -f "$PROVISION_JSON" ]; then
    log_display "provision payload $PROVISION_JSON not available"
    if [ "$MODE" = "once" ]; then
        exit 0
    fi
    while true; do
        sleep "$EASYMANET_DISPLAY_INTERVAL"
    done
fi

# shellcheck source=provision-lib.sh
. "$SCRIPT_DIR/provision-lib.sh"
# shellcheck source=api-lib.sh
. "$SCRIPT_DIR/api-lib.sh"
# shellcheck source=status-lib.sh
. "$SCRIPT_DIR/status-lib.sh"

render_once() {
    if [ -c "$EASYMANET_DISPLAY_TTY" ] && [ -w "$EASYMANET_DISPLAY_TTY" ]; then
        render_status_text > "$EASYMANET_DISPLAY_TTY" 2>> "$EASYMANET_DISPLAY_LOG" || render_status_text 2>> "$EASYMANET_DISPLAY_LOG"
    else
        render_status_text
    fi
}

log_display "starting display status mode=$MODE tty=$EASYMANET_DISPLAY_TTY interval=$EASYMANET_DISPLAY_INTERVAL"

if [ "$MODE" = "once" ]; then
    render_once
    exit 0
fi

while true; do
    render_once
    sleep "$EASYMANET_DISPLAY_INTERVAL"
done
