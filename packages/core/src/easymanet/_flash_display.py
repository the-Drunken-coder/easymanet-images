"""Display helpers for flash workflow plans."""

from __future__ import annotations

import json
from typing import Any, Optional

SECRET_FIELD_NAMES = {"password", "root_password_hash"}
SECRET_LIST_FIELD_NAMES = {"ssh_authorized_keys"}
REDACTED_VALUE = "<redacted>"


def resolve_flash_ssh_enabled(
    *,
    enable_ssh: bool,
    disable_ssh: bool,
) -> Optional[bool]:
    if disable_ssh:
        return False
    if enable_ssh:
        return True
    return None


def effective_flash_ssh_enabled(
    role: str,
    *,
    enable_ssh: bool,
    disable_ssh: bool,
) -> bool:
    if disable_ssh:
        return False
    if enable_ssh:
        return True
    return role == "gate"


def flash_ssh_note(
    role: str,
    *,
    enable_ssh: bool,
    disable_ssh: bool,
) -> str:
    if disable_ssh:
        return "no (--disable-ssh)"
    if enable_ssh:
        return "yes (--enable-ssh)"
    if role == "gate":
        return "yes (gate role default)"
    return "no (point role default)"


def redact_provision_for_display(value: Any, field_name: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: redact_provision_for_display(child, key)
            for key, child in value.items()
        }
    if isinstance(value, list):
        if field_name in SECRET_LIST_FIELD_NAMES:
            return [REDACTED_VALUE for _item in value]
        return [redact_provision_for_display(item) for item in value]
    if field_name in SECRET_FIELD_NAMES and value:
        return REDACTED_VALUE
    return value


def render_provision_for_display(
    provision: dict[str, Any],
    *,
    show_secrets: bool = False,
) -> str:
    if show_secrets:
        return json.dumps(provision, indent=2)
    return json.dumps(redact_provision_for_display(provision), indent=2)
