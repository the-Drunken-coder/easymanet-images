"""Tests for privilege detection."""

import pytest

from easymanet.privileges import PrivilegeError, check_privileges, is_running_as_root


def test_check_privileges_allows_root(monkeypatch):
    monkeypatch.setattr("easymanet.privileges.is_running_as_root", lambda: True)
    check_privileges("/dev/sdb")


def test_is_running_as_root_returns_false_without_geteuid(monkeypatch):
    monkeypatch.delattr("easymanet.privileges.os.geteuid", raising=False)

    assert is_running_as_root() is False


def test_check_privileges_allows_writable_device_on_linux(monkeypatch):
    monkeypatch.setattr("easymanet.privileges.is_running_as_root", lambda: False)
    monkeypatch.setattr("easymanet.privileges.is_linux", lambda: True)
    monkeypatch.setattr("easymanet.privileges.can_write_block_device", lambda _d: True)
    check_privileges("/dev/sdb")


def test_check_privileges_requires_writable_device_on_linux(monkeypatch):
    monkeypatch.setattr("easymanet.privileges.is_running_as_root", lambda: False)
    monkeypatch.setattr("easymanet.privileges.is_linux", lambda: True)
    monkeypatch.setattr("easymanet.privileges.can_write_block_device", lambda _d: False)

    with pytest.raises(PrivilegeError, match="Write access"):
        check_privileges("/dev/sdb")


def test_check_privileges_requires_access_otherwise(monkeypatch):
    monkeypatch.setattr("easymanet.privileges.is_running_as_root", lambda: False)
    monkeypatch.setattr("easymanet.privileges.is_linux", lambda: False)
    monkeypatch.setattr("easymanet.privileges.can_write_block_device", lambda _d: False)

    with pytest.raises(PrivilegeError, match="Write access"):
        check_privileges("/dev/disk4")


def test_privilege_error_does_not_include_placeholder_flash_target(monkeypatch):
    monkeypatch.setattr("easymanet.privileges.is_running_as_root", lambda: False)
    monkeypatch.setattr("easymanet.privileges.is_linux", lambda: False)
    monkeypatch.setattr("easymanet.privileges.can_write_block_device", lambda _d: False)

    with pytest.raises(PrivilegeError) as exc_info:
        check_privileges("/dev/disk4")

    message = str(exc_info.value)
    assert "manet01" not in message
    assert "/dev/sdX" not in message
