"""Structured flash workflow shared by CLI and desktop surfaces."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from .disks import assert_flash_allowed, lookup_device
from .download import (
    check_latest_version,
    download_image,
    get_cached_image,
    normalize_sha256,
    set_image_config,
    verify_image_sha256,
)
from .image import FlashError, finish_flash, flash_image
from .inject import InjectError, inject, inject_dry_run_info
from .manifest import Manifest, ManifestError, load_manifest
from .platform import check_platform
from .privileges import PrivilegeError, check_privileges
from .provision import resolve_provision
from .validate import validate
from .workspace import resolve_fleet_config


SECRET_FIELD_NAMES = {"password", "root_password_hash"}
SECRET_LIST_FIELD_NAMES = {"ssh_authorized_keys"}
REDACTED_VALUE = "<redacted>"
CUSTOM_IMAGE_VERSION = "custom"
LOGGER = logging.getLogger(__name__)


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
        payload = {
            "type": "event",
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
        }
        payload.update(self.data)
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

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
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
            "provision": self.provision,
            "provision_display": self.provision_display,
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


def run_flash_workflow(
    options: FlashOptions,
    emit: FlashEventCallback | None = None,
) -> FlashResult:
    events: list[FlashEvent] = []
    warnings: list[str] = []
    context: dict[str, Any] = {}

    def send(
        event_type: str,
        message: str = "",
        *,
        level: str = "info",
        **data: Any,
    ) -> None:
        event = FlashEvent(event_type, message, level=level, data=data)
        events.append(event)
        if emit:
            emit(event)

    try:
        _validate_options(options)
        _check_platform()

        config_path = resolve_fleet_config(options.config)
        context["config_path"] = str(config_path)
        context["node"] = options.node
        context["device"] = options.device

        manifest = _load_manifest(config_path)
        validation = validate(manifest, node_name=options.node)
        warnings.extend(validation.warnings)
        for warning in validation.warnings:
            send("warning", warning, level="warning")
        if validation.errors:
            raise FlashWorkflowError(
                FlashErrorCode.VALIDATION,
                "Config validation failed",
                errors=["Config validation failed", *validation.errors],
                warnings=warnings,
            )

        ssh_enabled = resolve_flash_ssh_enabled(
            enable_ssh=options.enable_ssh,
            disable_ssh=options.disable_ssh,
        )
        provision = resolve_provision(manifest, options.node, ssh_enabled=ssh_enabled)
        provision_dict = provision.to_dict()
        resolved_node = provision.node
        target = str(resolved_node.target)
        role = str(resolved_node.role)
        image_path, image_details, image_warnings = resolve_base_image(
            target,
            options.base_image,
            options.image_sha256,
            options.image_url,
            options.download,
            options.no_download,
            options.dry_run,
            emit=lambda payload: send(
                str(payload.get("type", "download")),
                str(payload.get("message", "")),
                **{key: value for key, value in payload.items() if key not in {"type", "message"}},
            ),
        )
        warnings.extend(image_warnings)
        for warning in image_warnings:
            send("warning", warning, level="warning")

        disk = _disk_details(options.device)
        try:
            assert_flash_allowed(options.device, force=options.force)
        except ValueError as exc:
            message = f"Flash safety: {exc}"
            if options.dry_run:
                warnings.append(message)
                send("warning", message, level="warning")
            else:
                raise FlashWorkflowError(
                    FlashErrorCode.DISK_SAFETY,
                    message,
                    warnings=warnings,
                ) from exc

        plan = {
            "config": str(config_path),
            "node": options.node,
            "hostname": resolved_node.hostname,
            "role": role,
            "target": target,
            "base_image": image_path,
            "device": options.device,
            "boot_payload": "/easymanet/provision.json",
            "ssh": flash_ssh_note(
                role,
                enable_ssh=options.enable_ssh,
                disable_ssh=options.disable_ssh,
            ),
            "secrets_redacted": not options.show_secrets,
            "disk": disk,
        }
        provision_display = render_provision_for_display(
            provision_dict,
            show_secrets=options.show_secrets,
        )
        dry_run_info = inject_dry_run_info(manifest, options.node)
        send(
            "plan",
            "Flash plan ready.",
            plan=plan,
            provision=provision_dict,
            provision_display=provision_display,
            dry_run_info=dry_run_info,
            image=image_details,
        )

        context.update(
            {
                "image": image_details,
                "plan": plan,
                "provision": provision_dict,
                "provision_display": provision_display,
                "dry_run_info": dry_run_info,
            }
        )

        if options.dry_run:
            send("complete", "Dry run complete. No changes were made.")
            return _result(
                ok=True,
                code=FlashErrorCode.OK,
                events=events,
                warnings=warnings,
                context=context,
            )

        try:
            check_privileges(options.device)
        except PrivilegeError as exc:
            raise FlashWorkflowError(
                FlashErrorCode.PRIVILEGE_REQUIRED,
                str(exc),
                warnings=warnings,
            ) from exc

        try:
            flash_image(
                device=options.device,
                image_path=image_path,
                force=options.force,
                skip_overlay_wipe=options.skip_overlay_wipe,
                emit=lambda payload: send(
                    str(payload.get("type", "flash")),
                    str(payload.get("message", "")),
                    level=str(payload.get("level", "info")),
                    **{
                        key: value
                        for key, value in payload.items()
                        if key not in {"type", "message", "level"}
                    },
                ),
            )
        except FlashError as exc:
            raise FlashWorkflowError(
                FlashErrorCode.FLASH,
                f"Flash error: {exc}",
                warnings=warnings,
            ) from exc

        send("inject_started", "Writing boot-partition payload.")
        try:
            inject_results = [
                {"path": path, "ok": ok}
                for path, ok in inject(
                    device=options.device,
                    manifest=manifest,
                    node_name=options.node,
                    ssh_enabled=ssh_enabled,
                )
            ]
            for item in inject_results:
                send("inject_result", str(item["path"]), ok=bool(item["ok"]))
            context["inject_results"] = inject_results
        except InjectError as exc:
            warnings.append(
                "Image was written but boot-partition provisioning failed: "
                f"{exc}. Check that the boot partition is mounted, writable, "
                "and healthy, then re-run the flash command after fixing the issue."
            )
            raise FlashWorkflowError(
                FlashErrorCode.INJECT,
                "Boot payload error: "
                f"{exc}. Check the boot partition mount, filesystem, and permissions before retrying.",
                warnings=warnings,
            ) from exc

        if not finish_flash(
            options.device,
            eject=not options.no_eject,
            emit=lambda payload: send(
                str(payload.get("type", "finish")),
                str(payload.get("message", "")),
                level=str(payload.get("level", "info")),
                **{
                    key: value
                    for key, value in payload.items()
                    if key not in {"type", "message", "level"}
                },
            ),
        ):
            raise FlashWorkflowError(
                FlashErrorCode.FINISH,
                "Eject failed; sync and eject the disk manually before removing it.",
                warnings=warnings,
            )

        send("complete", f"Done. Insert the drive into the Raspberry Pi for {options.node} and boot.")
        return _result(
            ok=True,
            code=FlashErrorCode.OK,
            events=events,
            warnings=warnings,
            context=context,
        )
    except FlashWorkflowError as exc:
        send("error", exc.message, level="error")
        return _result(
            ok=False,
            code=exc.code,
            exit_code=exc.exit_code,
            errors=exc.errors,
            events=events,
            warnings=exc.warnings or warnings,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 - API boundary returns structured failures.
        message = f"Unexpected flash workflow error: {type(exc).__name__}: {exc}"
        LOGGER.exception("Unexpected flash workflow error")
        send("error", message, level="error")
        return _result(
            ok=False,
            code=FlashErrorCode.INTERNAL,
            exit_code=1,
            errors=[message],
            events=events,
            warnings=warnings,
            context=context,
        )


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


def resolve_base_image(
    target: str,
    base_image: Optional[str],
    image_sha256: Optional[str],
    image_url: Optional[str],
    download: bool,
    no_download: bool,
    dry_run: bool,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    normalized_sha256: Optional[str] = None
    if image_sha256:
        try:
            normalized_sha256 = normalize_sha256(image_sha256)
        except ValueError as exc:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                f"Invalid --image-sha256: {exc}",
            ) from exc

    if base_image:
        if normalized_sha256:
            try:
                verify_image_sha256(Path(base_image), normalized_sha256)
            except OSError as exc:
                raise FlashWorkflowError(
                    FlashErrorCode.IMAGE,
                    f"Base image checksum error: {exc}",
                ) from exc
        else:
            warnings.append("Warning: local --base-image was not verified with --image-sha256.")
        return base_image, _image_payload(path=base_image, sha256=normalized_sha256), warnings

    if image_url:
        if not normalized_sha256:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                "--image-url requires --image-sha256 so downloaded firmware can be verified.",
            )
        set_image_config(
            target,
            image_url,
            version=CUSTOM_IMAGE_VERSION,
            sha256=normalized_sha256,
        )
        if not download:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                "Image URL saved but not downloaded. Re-run with --download to fetch the image, "
                "or add --base-image to use a local file.",
            )

    if no_download:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            "--no-download requires --base-image. No base image provided.",
        )

    if dry_run:
        cached = get_cached_image(target)
        if cached:
            return str(cached), _image_payload(path=str(cached), cached_path=str(cached)), warnings
        return (
            f"<auto-download for {target}>",
            _image_payload(path=f"<auto-download for {target}>"),
            warnings,
        )

    latest = check_latest_version(target)
    if not latest:
        if download:
            raise FlashWorkflowError(
                FlashErrorCode.IMAGE,
                f"No image URL configured for target '{target}'. "
                f"Configure one with --image-url or specify --base-image.\n"
                f"  easymanet flash --image-url https://example.com/image.img.gz "
                f"--image-sha256 <SHA256> ...",
            )
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"No image configured for target '{target}' and no --base-image given.\n"
            f"\n"
            f"Configure an image URL with:\n"
            f"  easymanet flash --image-url <URL> --image-sha256 <SHA256> ...\n"
            f"\n"
            f"Or download an image and pass it with:\n"
            f"  easymanet flash --base-image <path-to-image> --image-sha256 <SHA256> ...",
        )
    if not latest.sha256:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"No SHA-256 checksum configured or found for target '{target}'.",
        )

    if not download:
        cached = get_cached_image(target, sha256=latest.sha256, url=latest.url)
        if cached:
            return (
                str(cached),
                _image_payload(
                    path=str(cached),
                    cached_path=str(cached),
                    version=latest.version,
                    url=latest.url,
                    sha256=latest.sha256,
                ),
                warnings,
            )

    try:
        path = download_image(target, latest.version, latest.url, latest.sha256, force=download, emit=emit)
    except OSError as exc:
        raise FlashWorkflowError(
            FlashErrorCode.IMAGE,
            f"Image download error: {exc}",
        ) from exc
    return (
        str(path),
        _image_payload(
            path=str(path),
            cached_path=str(path),
            version=latest.version,
            url=latest.url,
            sha256=latest.sha256,
        ),
        warnings,
    )


def flash_image_details(*, config: str, node: str) -> dict[str, Any]:
    config_path = resolve_fleet_config(config)
    manifest = load_manifest(str(config_path))
    validation = validate(manifest, node_name=node)
    if validation.errors:
        return {"config_path": str(config_path), "errors": validation.errors}

    provision = resolve_provision(manifest, node)
    target = str(provision.node.target)
    details: dict[str, Any] = {
        "config_path": str(config_path),
        "node": node,
        "target": target,
        "version": "",
        "url": "",
        "sha256": "",
        "cached_path": "",
    }
    latest = check_latest_version(target)
    if latest:
        details.update(
            {
                "version": latest.version,
                "url": latest.url,
                "sha256": latest.sha256 or "",
            }
        )
        if latest.sha256:
            cached = get_cached_image(target, latest.sha256, latest.url)
            details["cached_path"] = str(cached) if cached else ""
    return details


def _validate_options(options: FlashOptions) -> None:
    if options.enable_ssh and options.disable_ssh:
        raise FlashWorkflowError(
            FlashErrorCode.OPTIONS,
            "Cannot use --enable-ssh and --disable-ssh together.",
        )
    if options.download and options.no_download:
        raise FlashWorkflowError(
            FlashErrorCode.OPTIONS,
            "Cannot use --download and --no-download together.",
        )
    if not options.yes and not options.dry_run:
        raise FlashWorkflowError(
            FlashErrorCode.OPTIONS,
            "--yes is required to flash. Use --dry-run to preview first.",
        )


def _check_platform() -> None:
    try:
        check_platform()
    except SystemExit as exc:
        message = str(exc) or "Unsupported platform"
        raise FlashWorkflowError(FlashErrorCode.PLATFORM, message) from exc


def _load_manifest(config_path: Path) -> Manifest:
    try:
        return load_manifest(str(config_path))
    except ManifestError as exc:
        raise FlashWorkflowError(FlashErrorCode.MANIFEST, f"Error: {exc}") from exc


def _disk_details(device: str) -> dict[str, Any]:
    disk = lookup_device(device)
    if not disk:
        return {}
    return {
        "device": disk.device,
        "model": disk.model,
        "size_human": disk.size_human,
        "removable": disk.removable,
        "mounted": disk.mounted,
        "warnings": disk.warnings,
        "blocking_warnings": disk.blocking_warnings,
    }


def _image_payload(
    *,
    path: str,
    cached_path: str = "",
    version: str = "",
    url: str = "",
    sha256: str | None = "",
) -> dict[str, Any]:
    return {
        "path": path,
        "cached_path": cached_path,
        "version": version,
        "url": url,
        "sha256": sha256 or "",
    }


def _result(
    *,
    ok: bool,
    code: FlashErrorCode,
    events: list[FlashEvent],
    warnings: list[str],
    context: dict[str, Any],
    exit_code: int | None = None,
    errors: list[str] | None = None,
) -> FlashResult:
    return FlashResult(
        ok=ok,
        exit_code=0 if ok else (exit_code or 1),
        code=code,
        errors=errors or [],
        warnings=warnings,
        events=events,
        config_path=str(context.get("config_path", "")),
        node=str(context.get("node", "")),
        device=str(context.get("device", "")),
        image=dict(context.get("image", {})),
        plan=dict(context.get("plan", {})),
        provision=dict(context.get("provision", {})),
        provision_display=str(context.get("provision_display", "")),
        dry_run_info=str(context.get("dry_run_info", "")),
        inject_results=list(context.get("inject_results", [])),
    )
