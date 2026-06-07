"""Tests for CLI helpers."""

import pytest
import typer

from easymanet import cli_flash, cli_image
from easymanet.cli import _resolve_flash_ssh_enabled
from easymanet.cli_flash import (
    CUSTOM_IMAGE_VERSION,
    REDACTED_VALUE,
    redact_provision_for_display,
    resolve_base_image,
)


def test_resolve_flash_ssh_disable_overrides_gate():
    assert _resolve_flash_ssh_enabled(enable_ssh=False, disable_ssh=True) is False


def test_resolve_flash_ssh_enable_overrides_point():
    assert _resolve_flash_ssh_enabled(enable_ssh=True, disable_ssh=False) is True


def test_resolve_flash_ssh_role_defaults():
    assert _resolve_flash_ssh_enabled(enable_ssh=False, disable_ssh=False) is None


def test_redact_provision_for_display_hides_secret_values():
    provision = {
        "version": 1,
        "mesh": {"id": "field", "password": "mesh-secret"},
        "node": {
            "hostname": "point01",
            "local_ap": {"enabled": True, "password": "ap-secret"},
            "gateway": {
                "wifi": {
                    "enabled": True,
                    "ssid": "uplink",
                    "password": "wifi-secret",
                }
            },
        },
        "management": {
            "root_password_hash": "$6$secret",
            "ssh_authorized_keys": ["ssh-ed25519 AAAA", "ssh-rsa BBBB"],
            "ssh_enabled": True,
        },
    }

    redacted = redact_provision_for_display(provision)

    assert redacted["mesh"]["password"] == REDACTED_VALUE
    assert redacted["node"]["local_ap"]["password"] == REDACTED_VALUE
    assert redacted["node"]["gateway"]["wifi"]["password"] == REDACTED_VALUE
    assert redacted["management"]["root_password_hash"] == REDACTED_VALUE
    assert redacted["management"]["ssh_authorized_keys"] == [
        REDACTED_VALUE,
        REDACTED_VALUE,
    ]
    assert redacted["management"]["ssh_enabled"] is True
    assert provision["mesh"]["password"] == "mesh-secret"


def test_flash_ssh_flags_mutually_exclusive():
    from typer.testing import CliRunner

    from easymanet.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "flash",
            "--config",
            "fleet.yml",
            "--node",
            "n1",
            "--device",
            "/dev/disk4",
            "--enable-ssh",
            "--disable-ssh",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Cannot use --enable-ssh and --disable-ssh" in result.output


def test_flash_download_flags_mutually_exclusive():
    from typer.testing import CliRunner

    from easymanet.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "flash",
            "--config",
            "fleet.yml",
            "--node",
            "n1",
            "--device",
            "/dev/disk4",
            "--download",
            "--no-download",
            "--yes",
        ],
    )
    assert result.exit_code == 1
    assert "Cannot use --download and --no-download" in result.output


def test_image_set_url_requires_sha256():
    from typer.testing import CliRunner

    from easymanet.cli import app

    result = CliRunner().invoke(
        app,
        [
            "image",
            "--set-url",
            "https://example.invalid/openmanet.img.gz",
        ],
    )

    assert result.exit_code == 1
    assert "--set-url requires --set-sha256" in result.output


def test_flash_base_image_rejects_malformed_sha256(tmp_path, capsys):
    image = tmp_path / "openmanet.img.gz"
    image.write_bytes(b"firmware")

    with pytest.raises(typer.Exit) as exc:
        resolve_base_image(
            "rpi4-mm6108-spi",
            str(image),
            "not-a-sha256",
            None,
            False,
            False,
            False,
        )

    assert exc.value.exit_code == 1
    assert "Invalid --image-sha256" in capsys.readouterr().out


def test_flash_image_url_rejects_malformed_sha256(capsys):
    with pytest.raises(typer.Exit) as exc:
        resolve_base_image(
            "rpi4-mm6108-spi",
            None,
            "not-a-sha256",
            "https://example.invalid/openmanet.img.gz",
            False,
            False,
            False,
        )

    assert exc.value.exit_code == 1
    assert "Invalid --image-sha256" in capsys.readouterr().out


def test_flash_image_url_uses_custom_version_label(monkeypatch):
    saved = {}

    def fake_set_image_config(target, url, version="", description="", sha256=None):
        saved.update(
            {
                "target": target,
                "url": url,
                "version": version,
                "description": description,
                "sha256": sha256,
            }
        )

    monkeypatch.setattr(cli_flash, "set_image_config", fake_set_image_config)

    with pytest.raises(typer.Exit):
        resolve_base_image(
            "rpi4-mm6108-spi",
            None,
            "a" * 64,
            "https://example.invalid/openmanet.img.gz",
            False,
            False,
            False,
        )

    assert saved["version"] == CUSTOM_IMAGE_VERSION


def test_flash_download_missing_url_hint_mentions_sha256(monkeypatch, capsys):
    monkeypatch.setattr(cli_flash, "check_latest_version", lambda _target: None)

    with pytest.raises(typer.Exit):
        resolve_base_image(
            "rpi4-mm6108-spi",
            None,
            None,
            None,
            True,
            False,
            False,
        )

    assert "--image-sha256 <SHA256>" in capsys.readouterr().out


def test_image_empty_config_hint_mentions_sha256(monkeypatch):
    from typer.testing import CliRunner

    from easymanet.cli import app

    monkeypatch.setattr(cli_image, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(cli_image, "get_image_config", lambda _target: None)
    monkeypatch.setattr(cli_image, "get_cached_image", lambda _target: None)

    result = CliRunner().invoke(app, ["image"])

    assert result.exit_code == 0
    assert "easymanet image --set-url <URL> --set-sha256 <SHA256>" in result.output


def test_flash_exits_when_finish_flash_reports_eject_failure(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from easymanet.cli import app

    image = tmp_path / "openmanet.img.gz"
    image.write_bytes(b"firmware")
    finish_calls = []

    monkeypatch.setattr(cli_flash, "check_platform", lambda: None)
    monkeypatch.setattr(cli_flash, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(cli_flash, "load_manifest", lambda _config: object())
    monkeypatch.setattr(
        cli_flash,
        "validate",
        lambda *_args, **_kwargs: type("Result", (), {"errors": []})(),
    )
    monkeypatch.setattr(
        cli_flash,
        "render_dict",
        lambda *_args, **_kwargs: {
            "node": {
                "target": "rpi4-mm6108-spi",
                "role": "point",
                "hostname": "point01",
            }
        },
    )
    monkeypatch.setattr(cli_flash, "lookup_device", lambda _device: None)
    monkeypatch.setattr(cli_flash, "assert_flash_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_flash, "render_provision_for_display", lambda *_args, **_kwargs: "{}")
    monkeypatch.setattr(cli_flash, "inject_dry_run_info", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli_flash, "check_privileges", lambda _device: None)
    monkeypatch.setattr(cli_flash, "flash_image", lambda **_kwargs: None)
    monkeypatch.setattr(cli_flash, "inject", lambda **_kwargs: [("/easymanet/provision.json", True)])

    def fake_finish_flash(device, eject=True):
        finish_calls.append((device, eject))
        return False

    monkeypatch.setattr(cli_flash, "finish_flash", fake_finish_flash)

    result = CliRunner().invoke(
        app,
        [
            "flash",
            "--config",
            "fleet.yml",
            "--node",
            "n1",
            "--device",
            "/dev/disk4",
            "--base-image",
            str(image),
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert finish_calls == [("/dev/disk4", True)]
    assert "Done. Insert the drive" not in result.output


def test_image_build_chains_build_error(monkeypatch):
    from typer.testing import CliRunner

    from easymanet import cli_image
    from easymanet.build import BuildError
    from easymanet.cli import app

    def fail_build(**kwargs):
        del kwargs
        raise BuildError("docker is missing")

    monkeypatch.setattr(cli_image, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(cli_image, "build_image", fail_build)

    result = CliRunner().invoke(app, ["image", "build", "--output-dir", "dist"])

    assert result.exit_code == 1
    assert "Build error: docker is missing" in result.output
