"""Tests for shared CLI helpers."""

from easymanet_cli import common


def test_upgrade_command_omits_break_system_packages_in_venv(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/easymanet-venv")

    assert common._upgrade_command() == "pip3 install --upgrade easymanet"


def test_upgrade_command_prefers_pipx_outside_venv(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(common.sys, "prefix", "/usr/local")
    monkeypatch.setattr(common.sys, "base_prefix", "/usr/local", raising=False)

    assert common._upgrade_command() == "pipx upgrade easymanet"
