"""Tests for shared CLI helpers."""

from easymanet import cli_common


def test_upgrade_command_omits_break_system_packages_in_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/easymanet-venv")

    assert cli_common._upgrade_command() == "pip3 install --upgrade easymanet"


def test_upgrade_command_prefers_pipx_outside_venv(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(cli_common.sys, "prefix", "/usr/local")
    monkeypatch.setattr(cli_common.sys, "base_prefix", "/usr/local", raising=False)

    assert cli_common._upgrade_command() == "pipx upgrade easymanet"
