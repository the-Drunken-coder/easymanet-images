#!/bin/sh
# Drive the Raspberry Pi green/ACT LED from public internet reachability.

set -u

: "${LED_ROOT:=/sys/class/leds}"
: "${EASYMANET_LED_INTERVAL:=10}"
: "${EASYMANET_LED_TARGETS:=1.1.1.1 8.8.8.8}"
: "${EASYMANET_LED_LOG:=/var/log/easymanet-led-status.log}"
: "${EASYMANET_LED_PING_TIMEOUT:=2}"

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

case "$EASYMANET_LED_INTERVAL" in
    ""|0|*[!0-9]*)
        EASYMANET_LED_INTERVAL=10
        ;;
esac

case "$EASYMANET_LED_PING_TIMEOUT" in
    ""|0|*[!0-9]*)
        EASYMANET_LED_PING_TIMEOUT=2
        ;;
esac

log() {
    mkdir -p "$(dirname "$EASYMANET_LED_LOG")" 2>/dev/null || true
    printf '[%s] %s\n' "$(date)" "$*" >> "$EASYMANET_LED_LOG" 2>/dev/null || true
}

led_path_by_name() {
    name="$1"
    if [ -d "$LED_ROOT/$name" ]; then
        printf '%s\n' "$LED_ROOT/$name"
        return 0
    fi
    return 1
}

detect_led() {
    if [ -n "${EASYMANET_LED_NAME:-}" ]; then
        led_path_by_name "$EASYMANET_LED_NAME"
        return $?
    fi

    for name in ACT act led0; do
        led_path_by_name "$name" && return 0
    done

    for led in "$LED_ROOT"/*; do
        [ -d "$led" ] || continue
        case "$(basename "$led" | tr '[:upper:]' '[:lower:]')" in
            *green*|*act*)
                printf '%s\n' "$led"
                return 0
                ;;
        esac
    done

    return 1
}

set_led() {
    led="$1"
    value="$2"

    if [ -e "$led/trigger" ]; then
        echo none > "$led/trigger" 2>/dev/null || true
    fi
    if [ ! -e "$led/brightness" ]; then
        log "LED $(basename "$led") has no brightness control"
        return 1
    fi
    if echo "$value" > "$led/brightness" 2>/dev/null; then
        return 0
    fi

    log "failed to set LED $(basename "$led") brightness=$value"
    return 1
}

has_internet() {
    for target in $EASYMANET_LED_TARGETS; do
        if ping -c 1 -W "$EASYMANET_LED_PING_TIMEOUT" "$target" >/dev/null 2>&1; then
            return 0
        fi
    done
    return 1
}

run_once() {
    if has_internet; then
        set_led "$LED_PATH" 1 || true
        log "internet=up led=$(basename "$LED_PATH") state=on"
        return 0
    fi

    set_led "$LED_PATH" 0 || true
    log "internet=down led=$(basename "$LED_PATH") state=off"
    return 1
}

LED_PATH="$(detect_led || true)"
if [ -z "$LED_PATH" ]; then
    if [ -n "${EASYMANET_LED_NAME:-}" ]; then
        log "requested LED $EASYMANET_LED_NAME not found under $LED_ROOT"
    else
        log "no green/ACT LED candidate found under $LED_ROOT"
    fi
    exit 0
fi

log "starting led status mode=$MODE led=$(basename "$LED_PATH") interval=$EASYMANET_LED_INTERVAL targets=$EASYMANET_LED_TARGETS"

if [ "$MODE" = "once" ]; then
    run_once
    exit $?
fi

while true; do
    run_once || true
    sleep "$EASYMANET_LED_INTERVAL"
done
