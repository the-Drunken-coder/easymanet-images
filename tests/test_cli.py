"""Tests for CLI helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

import easymanet.flash as core_flash
from easymanet_cli import flash as cli_flash
from easymanet_cli.flash import (
    CUSTOM_IMAGE_VERSION,
    REDACTED_VALUE,
    redact_provision_for_display,
    resolve_base_image,
    resolve_flash_ssh_enabled,
)
from easymanet_cli import image as cli_image


FIELD_FLEET_YAML = """\
version: 1

mesh:
  id: field
  password: test-password
  channel: 42
  bandwidth_mhz: 2
  country: US

defaults:
  target: rpi4-mm6108-spi
  local_ap:
    enabled: true
    password: test-local-password
  management:
    root_password_hash: ""
    ssh_authorized_keys:
      - "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKm8abcdefgh"

nodes:
  point01:
    role: point
    hostname: point01
    ip: 10.41.2.1
    local_ap:
      ssid: point01-local
"""


def test_resolve_flash_ssh_disable_overrides_gate():
    assert resolve_flash_ssh_enabled(enable_ssh=False, disable_ssh=True) is False


def test_resolve_flash_ssh_enable_overrides_point():
    assert resolve_flash_ssh_enabled(enable_ssh=True, disable_ssh=False) is True


def test_resolve_flash_ssh_role_defaults():
    assert resolve_flash_ssh_enabled(enable_ssh=False, disable_ssh=False) is None


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

    from easymanet_cli.app import app

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

    from easymanet_cli.app import app

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

    from easymanet_cli.app import app

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


def test_diagnostics_run_command_prints_summary(monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_run_diagnostics(config=""):
        del config
        return {"ok": True, "summary": "EasyMANET Diagnostics\nSupport code: EM-OK\n"}

    monkeypatch.setattr(cli_app, "run_diagnostics", fake_run_diagnostics)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "run", "--config", "field"])

    assert result.exit_code == 0
    assert "Support code: EM-OK" in result.output


def test_diagnostics_run_command_exits_nonzero_on_failure(monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_run_diagnostics(config=""):
        del config
        return {
            "ok": False,
            "summary": "EasyMANET Diagnostics\nSupport code: EM-API-DOWN\n",
            "errors": ["node API unavailable"],
        }

    monkeypatch.setattr(cli_app, "run_diagnostics", fake_run_diagnostics)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "run", "--config", "field"])

    assert result.exit_code == 1
    assert "EM-API-DOWN" in result.output
    assert "node API unavailable" in result.output


def test_diagnostics_bundle_command_prints_bundle_path(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_export_support_bundle(config=""):
        del config
        return {
            "ok": True,
            "summary": "EasyMANET Diagnostics\n",
            "bundle_path": str(tmp_path / "EasyMANET" / "Diagnostics" / "support.zip"),
        }

    monkeypatch.setattr(cli_app, "export_support_bundle", fake_export_support_bundle)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "bundle", "--config", "field"])

    assert result.exit_code == 0
    assert str(Path(tmp_path / "EasyMANET" / "Diagnostics" / "support.zip")) in result.output


def test_diagnostics_bundle_command_exits_nonzero_on_failure(monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_export_support_bundle(config=""):
        del config
        return {"ok": False, "summary": "EasyMANET Diagnostics\n", "errors": ["bundle failed"]}

    monkeypatch.setattr(cli_app, "export_support_bundle", fake_export_support_bundle)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "bundle", "--config", "field"])

    assert result.exit_code == 1
    assert "bundle failed" in result.output


def test_diagnostics_import_boot_report_command_prints_imported_paths(monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_import_boot_report(source):
        return {"ok": True, "target": "/workspace/Diagnostics/imported", "imported": [source]}

    monkeypatch.setattr(cli_app, "import_boot_report", fake_import_boot_report)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "import-boot-report", "--source", "/Volumes/boot"])

    assert result.exit_code == 0
    assert "/workspace/Diagnostics/imported" in result.output
    assert "/Volumes/boot" in result.output


def test_diagnostics_import_boot_report_command_exits_nonzero_on_failure(monkeypatch):
    from typer.testing import CliRunner
    import easymanet_cli.app as cli_app

    def fake_import_boot_report(source):
        del source
        return {"ok": False, "errors": ["no boot reports found"]}

    monkeypatch.setattr(cli_app, "import_boot_report", fake_import_boot_report)

    result = CliRunner().invoke(cli_app.app, ["diagnostics", "import-boot-report", "--source", "/Volumes/boot"])

    assert result.exit_code == 1
    assert "no boot reports found" in result.output


def test_flash_base_image_rejects_malformed_sha256(tmp_path, capsys):
    image = tmp_path / "openmanet.img.gz"
    image.write_bytes(b"firmware")

    with pytest.raises(core_flash.FlashWorkflowError) as exc:
        resolve_base_image(
            "rpi4-mm6108-spi",
            str(image),
            "not-a-sha256",
            None,
            False,
            False,
            False,
        )

    assert "Invalid --image-sha256" in str(exc.value)


def test_flash_image_url_rejects_malformed_sha256(capsys):
    with pytest.raises(core_flash.FlashWorkflowError) as exc:
        resolve_base_image(
            "rpi4-mm6108-spi",
            None,
            "not-a-sha256",
            "https://example.invalid/openmanet.img.gz",
            False,
            False,
            False,
        )

    assert "Invalid --image-sha256" in str(exc.value)


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

    monkeypatch.setattr(core_flash, "set_image_config", fake_set_image_config)

    with pytest.raises(core_flash.FlashWorkflowError):
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
    monkeypatch.setattr(core_flash, "check_latest_version", lambda _target: None)

    with pytest.raises(core_flash.FlashWorkflowError) as exc:
        resolve_base_image(
            "rpi4-mm6108-spi",
            None,
            None,
            None,
            True,
            False,
            False,
        )

    assert "--image-sha256 <SHA256>" in str(exc.value)


def test_image_empty_config_hint_mentions_sha256(monkeypatch):
    from typer.testing import CliRunner

    from easymanet_cli.app import app

    monkeypatch.setattr(cli_image, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(cli_image, "get_image_config", lambda _target: None)
    monkeypatch.setattr(cli_image, "get_cached_image", lambda _target: None)

    result = CliRunner().invoke(app, ["image"])

    assert result.exit_code == 0
    assert "easymanet image --set-url <URL> --set-sha256 <SHA256>" in result.output


def test_cli_init_and_fleets_use_shared_workspace(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from easymanet_cli.app import app
    from easymanet.workspace import WORKSPACE_ENV

    workspace = tmp_path / "EasyMANET"
    monkeypatch.setenv(WORKSPACE_ENV, str(workspace))
    result = CliRunner().invoke(app, ["init"])

    assert result.exit_code == 0
    assert "EasyMANET workspace ready." in result.output
    assert (workspace / "Fleets").is_dir()

    (workspace / "Fleets" / "field.yml").write_text(FIELD_FLEET_YAML)
    result = CliRunner().invoke(app, ["fleets"])

    assert result.exit_code == 0
    assert f"Fleets folder: {workspace / 'Fleets'}" in result.output
    assert "field.yml" in result.output


def test_cli_validate_resolves_fleet_name_from_workspace(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from easymanet_cli.app import app
    from easymanet.workspace import WORKSPACE_ENV, ensure_workspace

    workspace = tmp_path / "EasyMANET"
    monkeypatch.setenv(WORKSPACE_ENV, str(workspace))
    ensure_workspace()
    fleet = workspace / "Fleets" / "field.yml"
    fleet.write_text(FIELD_FLEET_YAML)

    result = CliRunner().invoke(
        app,
        ["validate", "--config", "field", "--node", "point01"],
    )

    assert result.exit_code == 0
    assert f"Validating: {fleet}" in result.output


def test_flash_exits_when_finish_flash_reports_eject_failure(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from easymanet_cli.app import app

    image = tmp_path / "openmanet.img.gz"
    image.write_bytes(b"firmware")
    finish_calls = []

    monkeypatch.setattr(cli_flash, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(core_flash, "check_platform", lambda: None)
    monkeypatch.setattr(core_flash, "load_manifest", lambda _config: object())
    monkeypatch.setattr(
        core_flash,
        "validate",
        lambda *_args, **_kwargs: type("Result", (), {"errors": [], "warnings": []})(),
    )
    monkeypatch.setattr(
        core_flash,
        "resolve_provision",
        lambda *_args, **_kwargs: SimpleNamespace(
            node=SimpleNamespace(
                target="rpi4-mm6108-spi",
                role="point",
                hostname="point01",
            ),
            to_dict=lambda: {
                "version": 1,
                "mesh": {},
                "node": {
                    "target": "rpi4-mm6108-spi",
                    "role": "point",
                    "hostname": "point01",
                },
                "management": {},
            },
        ),
    )
    monkeypatch.setattr(core_flash, "lookup_device", lambda _device: None)
    monkeypatch.setattr(core_flash, "assert_flash_allowed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core_flash, "inject_dry_run_info", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(core_flash, "check_privileges", lambda _device: None)
    monkeypatch.setattr(core_flash, "flash_image", lambda **_kwargs: None)
    monkeypatch.setattr(core_flash, "inject", lambda **_kwargs: [("/easymanet/provision.json", True)])

    def fake_finish_flash(device, eject=True, **_kwargs):
        finish_calls.append((device, eject))
        return False

    monkeypatch.setattr(core_flash, "finish_flash", fake_finish_flash)

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

    from easymanet_cli import image as cli_image
    from easymanet_image.build import BuildError
    from easymanet_cli.app import app

    def fail_build(**kwargs):
        del kwargs
        raise BuildError("docker is missing")

    monkeypatch.setattr(cli_image, "maybe_show_update_notice", lambda: None)
    monkeypatch.setattr(cli_image, "build_image", fail_build)

    result = CliRunner().invoke(app, ["image", "build", "--output-dir", "dist"])

    assert result.exit_code == 1
    assert "Build error: docker is missing" in result.output


def test_image_cli_compatibility_shim_exposes_register_command():
    import easymanet_image.cli as shim
    from easymanet_image.cli import register_image_commands as shim_register
    from easymanet_cli.image import register_image_commands

    assert shim_register is register_image_commands
    for name in (
        "maybe_show_update_notice",
        "get_image_config",
        "get_cached_image",
        "build_image",
    ):
        assert hasattr(shim, name)
