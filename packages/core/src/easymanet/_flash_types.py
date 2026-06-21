"""Structured flash workflow types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ._flash_display import redact_provision_for_display, render_provision_for_display


class FlashErrorCode(str, Enum):
    OK = "ok"
    OPTIONS = "options"
    PLATFORM = "platform"
    MANIFEST = "manifest"
    VALIDATION = "validation"
    IMAGE = "image"
    DISK_SAFETY = "disk_safety"
    PRIVILEGE_REQUIRED = "privilege_required"
    FLASH = "flash"
    INJECT = "inject"
    FINISH = "finish"
    INTERNAL = "internal"


@dataclass(frozen=True)
class FlashOptions:
    config: str
    node: str
    device: str
    base_image: Optional[str] = None
    image_sha256: Optional[str] = None
    image_url: Optional[str] = None
    download: bool = False
    no_download: bool = False
    yes: bool = False
    dry_run: bool = False
    force: bool = False
    no_eject: bool = False
    skip_overlay_wipe: bool = False
    enable_ssh: bool = False
    disable_ssh: bool = False
    show_secrets: bool = False


@dataclass(frozen=True)
class FlashEvent:
    event_type: str
    message: str = ""
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.data)
        if isinstance(data.get("provision"), dict):
            data["provision"] = redact_provision_for_display(data["provision"])
            if "provision_display" not in data:
                data["provision_display"] = render_provision_for_display(data["provision"])
        payload = {
            "type": "event",
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
        }
        payload.update(data)
        return payload


@dataclass
class FlashResult:
    ok: bool
    exit_code: int
    code: FlashErrorCode
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: list[FlashEvent] = field(default_factory=list)
    config_path: str = ""
    node: str = ""
    device: str = ""
    image: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    provision: dict[str, Any] = field(default_factory=dict)
    provision_display: str = ""
    dry_run_info: str = ""
    inject_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(
        self,
        *,
        include_events: bool = False,
        include_secrets: bool = False,
    ) -> dict[str, Any]:
        provision = dict(self.provision)
        provision_display = self.provision_display
        if not include_secrets:
            provision = redact_provision_for_display(provision)
            if provision:
                provision_display = render_provision_for_display(provision)
        payload: dict[str, Any] = {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "code": self.code.value,
            "errors": self.errors,
            "warnings": self.warnings,
            "config_path": self.config_path,
            "node": self.node,
            "device": self.device,
            "image": self.image,
            "plan": self.plan,
            "provision": provision,
            "provision_display": provision_display,
            "dry_run_info": self.dry_run_info,
            "inject_results": self.inject_results,
            "sudo_command": "",
        }
        if include_events:
            payload["events"] = [event.to_dict() for event in self.events]
        return payload


class FlashWorkflowError(Exception):
    def __init__(
        self,
        code: FlashErrorCode,
        message: str,
        *,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.errors = errors or [message]
        self.warnings = warnings or []
        self.exit_code = exit_code


FlashEventCallback = Callable[[FlashEvent], None]
