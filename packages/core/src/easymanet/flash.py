"""Structured flash workflow shared by CLI and desktop surfaces.

This module owns flash planning, validation, step ordering, and structured
result/event shapes. It delegates base-image selection to ``_flash_images`` and
destructive media writes to ``image``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._flash_disk import disk_details as _disk_details_impl
from ._flash_display import (
    REDACTED_VALUE,
    effective_flash_ssh_enabled,
    flash_ssh_note,
    redact_provision_for_display,
    render_provision_for_display,
    resolve_flash_ssh_enabled,
)
from ._flash_images import (
    CUSTOM_IMAGE_VERSION,
    flash_image_details,
    resolve_base_image as _resolve_base_image,
)
from ._flash_types import (
    FlashErrorCode,
    FlashEvent,
    FlashEventCallback,
    FlashOptions,
    FlashResult,
    FlashWorkflowError,
)
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


LOGGER = logging.getLogger(__name__)


def resolve_base_image(
    target: str,
    base_image: str | None,
    image_sha256: str | None,
    image_url: str | None,
    download: bool,
    no_download: bool,
    dry_run: bool,
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    """Resolve the image through the public workflow patch point.

    The implementation lives in ``_flash_images``. The dependency arguments
    intentionally come from this module so existing callers and tests can keep
    patching ``easymanet.flash`` when they need to control image resolution.
    """
    return _resolve_base_image(
        target,
        base_image,
        image_sha256,
        image_url,
        download,
        no_download,
        dry_run,
        emit=emit,
        normalize_sha256_fn=normalize_sha256,
        verify_image_sha256_fn=verify_image_sha256,
        set_image_config_fn=set_image_config,
        get_cached_image_fn=get_cached_image,
        check_latest_version_fn=check_latest_version,
        download_image_fn=download_image,
    )


def _disk_details(device: str) -> dict[str, Any]:
    return _disk_details_impl(device, lookup_device_fn=lookup_device)


@dataclass
class _PreparedFlash:
    result: FlashResult
    events: list[FlashEvent]
    warnings: list[str]
    context: dict[str, Any]
    manifest: Manifest | None = None
    image_path: str = ""
    ssh_enabled: bool | None = None


def prepare_flash_workflow(
    options: FlashOptions,
    emit: FlashEventCallback | None = None,
) -> FlashResult:
    """Resolve and validate the inputs needed before a flash writes media."""
    return _prepare_flash_workflow(
        options,
        emit=emit,
        complete_dry_run=options.dry_run,
    ).result


def run_flash_workflow(
    options: FlashOptions,
    emit: FlashEventCallback | None = None,
) -> FlashResult:
    prepared = _prepare_flash_workflow(
        options,
        emit=emit,
        complete_dry_run=options.dry_run,
    )
    if not prepared.result.ok or options.dry_run:
        return prepared.result

    events = prepared.events
    warnings = prepared.warnings
    context = prepared.context
    manifest = prepared.manifest
    image_path = prepared.image_path
    ssh_enabled = prepared.ssh_enabled
    send = _event_sender(events, emit)

    try:
        if manifest is None:
            raise FlashWorkflowError(
                FlashErrorCode.INTERNAL,
                "Flash preparation did not return a manifest",
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
                emit=lambda payload: _forward_media_event(
                    payload,
                    send,
                    default_type="flash",
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
            emit=lambda payload: _forward_media_event(
                payload,
                send,
                default_type="finish",
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


def _prepare_flash_workflow(
    options: FlashOptions,
    *,
    emit: FlashEventCallback | None,
    complete_dry_run: bool,
) -> _PreparedFlash:
    events: list[FlashEvent] = []
    warnings: list[str] = []
    context: dict[str, Any] = {}
    manifest: Manifest | None = None
    image_path = ""
    ssh_enabled: bool | None = None
    send = _event_sender(events, emit)

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

        ssh_override = resolve_flash_ssh_enabled(
            enable_ssh=options.enable_ssh,
            disable_ssh=options.disable_ssh,
        )
        provision = resolve_provision(manifest, options.node, ssh_enabled=ssh_override)
        resolved_node = provision.node
        target = str(resolved_node.target)
        role = str(resolved_node.role)
        ssh_enabled = effective_flash_ssh_enabled(
            role,
            enable_ssh=options.enable_ssh,
            disable_ssh=options.disable_ssh,
        )
        if ssh_override is None:
            provision = resolve_provision(manifest, options.node, ssh_enabled=ssh_enabled)
        provision_dict = provision.to_dict()
        public_provision = redact_provision_for_display(provision_dict)
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
            "ssh_enabled": ssh_enabled,
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
            provision=public_provision,
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
        if options.dry_run and complete_dry_run:
            send("complete", "Dry run complete. No changes were made.")

        return _PreparedFlash(
            result=_result(
                ok=True,
                code=FlashErrorCode.OK,
                events=events,
                warnings=warnings,
                context=context,
            ),
            events=events,
            warnings=warnings,
            context=context,
            manifest=manifest,
            image_path=image_path,
            ssh_enabled=ssh_enabled,
        )
    except FlashWorkflowError as exc:
        send("error", exc.message, level="error")
        return _PreparedFlash(
            result=_result(
                ok=False,
                code=exc.code,
                exit_code=exc.exit_code,
                errors=exc.errors,
                events=events,
                warnings=exc.warnings or warnings,
                context=context,
            ),
            events=events,
            warnings=exc.warnings or warnings,
            context=context,
            manifest=manifest,
            image_path=image_path,
            ssh_enabled=ssh_enabled,
        )
    except Exception as exc:  # noqa: BLE001 - API boundary returns structured failures.
        message = f"Unexpected flash workflow error: {type(exc).__name__}: {exc}"
        LOGGER.exception("Unexpected flash workflow error")
        send("error", message, level="error")
        return _PreparedFlash(
            result=_result(
                ok=False,
                code=FlashErrorCode.INTERNAL,
                exit_code=1,
                errors=[message],
                events=events,
                warnings=warnings,
                context=context,
            ),
            events=events,
            warnings=warnings,
            context=context,
            manifest=manifest,
            image_path=image_path,
            ssh_enabled=ssh_enabled,
        )


def _event_sender(
    events: list[FlashEvent],
    emit: FlashEventCallback | None,
) -> Callable[..., None]:
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

    return send


def _forward_media_event(
    payload: dict[str, Any],
    send: Callable[..., None],
    *,
    default_type: str,
) -> None:
    data = {
        key: value
        for key, value in payload.items()
        if key not in {"type", "message", "level"}
    }
    send(
        str(payload.get("type", default_type)),
        str(payload.get("message", "")),
        level=str(payload.get("level", "info")),
        **data,
    )


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
