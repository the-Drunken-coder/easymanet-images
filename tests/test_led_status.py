"""Behavior tests for the EasyMANET LED internet status script."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "images"
    / "openmanet"
    / "provisioning"
    / "openwrt-overlay"
    / "usr"
    / "lib"
    / "easymanet"
    / "led-status.sh"
)


def _make_led(root: Path, name: str) -> Path:
    led = root / name
    led.mkdir(parents=True)
    (led / "trigger").write_text("timer\n")
    (led / "brightness").write_text("0\n")
    return led


def _write_fake_ping(bin_dir: Path) -> None:
    ping = bin_dir / "ping"
    ping.write_text(
        """#!/bin/sh
echo "$@" >> "$PING_LOG"
case "${PING_MODE:-fail}" in
  success)
    exit 0
    ;;
  target)
    for arg in "$@"; do
      if [ "$arg" = "${PING_SUCCEEDS_FOR:-}" ]; then
        exit 0
      fi
    done
    exit 1
    ;;
  *)
    exit 1
    ;;
esac
"""
    )
    ping.chmod(0o755)


def _run_once(tmp_path: Path, led_root: Path, extra_env: dict | None = None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_ping(bin_dir)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "LED_ROOT": str(led_root),
            "EASYMANET_LED_LOG": str(tmp_path / "led-status.log"),
            "PING_LOG": str(tmp_path / "ping.log"),
        }
    )
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["sh", str(SCRIPT), "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_led_status_detects_act_before_fallbacks_and_never_pwr_by_default(tmp_path):
    led_root = tmp_path / "leds"
    _make_led(led_root, "PWR")
    _make_led(led_root, "led0")
    act = _make_led(led_root, "ACT")

    result = _run_once(tmp_path, led_root, {"PING_MODE": "success"})

    assert result.returncode == 0, result.stderr + result.stdout
    assert (act / "trigger").read_text() == "none\n"
    assert (act / "brightness").read_text() == "1\n"
    assert (led_root / "led0" / "brightness").read_text() == "0\n"
    assert (led_root / "PWR" / "brightness").read_text() == "0\n"


def test_led_status_turns_led_off_when_all_targets_fail(tmp_path):
    led_root = tmp_path / "leds"
    act = _make_led(led_root, "ACT")
    (act / "brightness").write_text("1\n")

    result = _run_once(tmp_path, led_root, {"PING_MODE": "fail"})

    assert result.returncode == 1
    assert (act / "trigger").read_text() == "none\n"
    assert (act / "brightness").read_text() == "0\n"
    ping_log = (tmp_path / "ping.log").read_text()
    assert "1.1.1.1" in ping_log
    assert "8.8.8.8" in ping_log


def test_led_status_honors_explicit_led_name_even_for_pwr(tmp_path):
    led_root = tmp_path / "leds"
    act = _make_led(led_root, "ACT")
    pwr = _make_led(led_root, "PWR")

    result = _run_once(
        tmp_path,
        led_root,
        {"PING_MODE": "success", "EASYMANET_LED_NAME": "PWR"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (pwr / "brightness").read_text() == "1\n"
    assert (act / "brightness").read_text() == "0\n"


def test_led_status_honors_target_override(tmp_path):
    led_root = tmp_path / "leds"
    act = _make_led(led_root, "ACT")

    result = _run_once(
        tmp_path,
        led_root,
        {
            "PING_MODE": "target",
            "PING_SUCCEEDS_FOR": "8.8.8.8",
            "EASYMANET_LED_TARGETS": "203.0.113.10 8.8.8.8",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (act / "brightness").read_text() == "1\n"
    ping_log = (tmp_path / "ping.log").read_text()
    assert "203.0.113.10" in ping_log
    assert "8.8.8.8" in ping_log


def test_led_status_missing_led_exits_cleanly(tmp_path):
    led_root = tmp_path / "leds"
    led_root.mkdir()

    result = _run_once(tmp_path, led_root, {"PING_MODE": "success"})

    assert result.returncode == 0, result.stderr + result.stdout
    assert "no green/ACT LED candidate" in (tmp_path / "led-status.log").read_text()
