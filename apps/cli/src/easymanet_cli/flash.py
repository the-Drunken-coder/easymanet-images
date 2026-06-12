"""Flash command presentation for the EasyMANET CLI."""

from __future__ import annotations

from typing import Optional

import typer

from easymanet.flash import (
    CUSTOM_IMAGE_VERSION,
    REDACTED_VALUE,
    FlashEvent,
    FlashOptions,
    FlashResult,
    flash_ssh_note,
    redact_provision_for_display,
    render_provision_for_display,
    resolve_base_image,
    resolve_flash_ssh_enabled,
    run_flash_workflow,
)

from .common import maybe_show_update_notice, print_header


def register_flash_command(app: typer.Typer) -> None:
    @app.command()
    def flash(
        config: str = typer.Option(
            ..., "--config", "-c", help="Path to fleet.yml config file"
        ),
        node: str = typer.Option(
            ..., "--node", "-n", help="Node name to provision"
        ),
        device: str = typer.Option(
            ..., "--device", "-d", help="Target device path (e.g., /dev/disk4)"
        ),
        base_image: Optional[str] = typer.Option(
            None,
            "--base-image",
            "-i",
            help="Path to OpenMANET base image (.img or .img.gz) — auto-downloaded if omitted",
        ),
        image_sha256: Optional[str] = typer.Option(
            None,
            "--image-sha256",
            help="Expected SHA-256 for --base-image or downloaded --image-url firmware.",
        ),
        image_url: Optional[str] = typer.Option(
            None,
            "--image-url",
            help="HTTPS URL to download the base image from; requires --image-sha256.",
        ),
        download: bool = typer.Option(
            False, "--download", help="Force re-download of the latest base image"
        ),
        no_download: bool = typer.Option(
            False, "--no-download", help="Skip auto-download; requires --base-image"
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Skip confirmation prompt"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show plan without writing anything"
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Override blocking disk safety checks (system disk, large fixed disk, device not in default list)",
        ),
        no_eject: bool = typer.Option(
            False, "--no-eject", help="Do not eject disk after flashing"
        ),
        skip_overlay_wipe: bool = typer.Option(
            False,
            "--skip-overlay-wipe",
            help="Skip wiping stale OpenWrt overlay data after writing the image (not recommended)",
        ),
        enable_ssh: bool = typer.Option(
            False,
            "--enable-ssh",
            help="Enable SSH (dropbear) on this node at first boot.",
        ),
        disable_ssh: bool = typer.Option(
            False,
            "--disable-ssh",
            help="Disable SSH at first boot, including on gate nodes.",
        ),
        show_secrets: bool = typer.Option(
            False,
            "--show-secrets",
            help="Print secret values in the resolved provision.json preview.",
        ),
    ):
        """Flash an OpenMANET image and stage node config on the boot partition."""
        run_flash(
            config=config,
            node=node,
            device=device,
            base_image=base_image,
            image_sha256=image_sha256,
            image_url=image_url,
            download=download,
            no_download=no_download,
            yes=yes,
            dry_run=dry_run,
            force=force,
            no_eject=no_eject,
            skip_overlay_wipe=skip_overlay_wipe,
            enable_ssh=enable_ssh,
            disable_ssh=disable_ssh,
            show_secrets=show_secrets,
        )


def run_flash(
    *,
    config: str,
    node: str,
    device: str,
    base_image: Optional[str] = None,
    image_sha256: Optional[str] = None,
    image_url: Optional[str] = None,
    download: bool = False,
    no_download: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    force: bool = False,
    no_eject: bool = False,
    skip_overlay_wipe: bool = False,
    enable_ssh: bool = False,
    disable_ssh: bool = False,
    show_secrets: bool = False,
) -> None:
    """Run the shared flash workflow and present it for the CLI."""
    maybe_show_update_notice()
    result = run_flash_workflow(
        FlashOptions(
            config=config,
            node=node,
            device=device,
            base_image=base_image,
            image_sha256=image_sha256,
            image_url=image_url,
            download=download,
            no_download=no_download,
            yes=yes,
            dry_run=dry_run,
            force=force,
            no_eject=no_eject,
            skip_overlay_wipe=skip_overlay_wipe,
            enable_ssh=enable_ssh,
            disable_ssh=disable_ssh,
            show_secrets=show_secrets,
        ),
        emit=_print_flash_event,
    )
    if not result.ok:
        _print_result_errors(result)
        raise typer.Exit(result.exit_code)


def _print_flash_event(event: FlashEvent) -> None:
    if event.event_type == "plan":
        _print_plan_event(event)
        return
    if event.event_type == "warning":
        typer.secho(event.message, fg=typer.colors.YELLOW)
        return
    if event.event_type == "error":
        typer.secho(event.message, fg=typer.colors.RED)
        return
    if event.event_type == "inject_started":
        typer.echo()
        print_header("Writing boot-partition payload")
        return
    if event.event_type == "inject_result":
        ok = bool(event.data.get("ok"))
        status = "✓" if ok else "✗"
        color = typer.colors.GREEN if ok else typer.colors.RED
        typer.secho(f"  {status} {event.message}", fg=color)
        return
    if event.event_type == "complete":
        typer.secho(event.message, fg=typer.colors.GREEN)
        return
    if event.message:
        color = typer.colors.YELLOW if event.level == "warning" else None
        typer.secho(event.message, fg=color)


def _print_plan_event(event: FlashEvent) -> None:
    plan = event.data.get("plan", {})
    print_header("Flash Plan")
    typer.echo(f"  Config:       {plan.get('config', '')}")
    typer.echo(f"  Node:         {plan.get('node', '')}")
    typer.echo(f"  Hostname:     {plan.get('hostname', '')}")
    typer.echo(f"  Role:         {plan.get('role', '')}")
    typer.echo(f"  Target:       {plan.get('target', '')}")
    typer.echo(f"  Base image:   {plan.get('base_image', '')}")
    typer.echo(f"  Device:       {plan.get('device', '')}")
    typer.echo(f"  Boot payload: {plan.get('boot_payload', '')}")
    typer.echo(f"  SSH:          {plan.get('ssh', '')}")

    disk = plan.get("disk") or {}
    if disk:
        typer.echo("  Disk details:")
        typer.echo(f"    Model:      {disk.get('model', '')}")
        typer.echo(f"    Size:       {disk.get('size_human', '')}")
        typer.echo(f"    Removable:  {'yes' if disk.get('removable') else 'no'}")
        mounted = disk.get("mounted") or []
        typer.echo(f"    Mounted:    {', '.join(mounted) if mounted else '(none)'}")
        for warning in disk.get("blocking_warnings") or []:
            typer.secho(f"    {warning}", fg=typer.colors.RED)
    typer.echo()

    print_header("Resolved provision.json")
    typer.echo(str(event.data.get("provision_display", "")))
    if plan.get("secrets_redacted", True):
        typer.secho(
            "  Secrets redacted. Use --show-secrets to print the full payload.",
            fg=typer.colors.BLUE,
        )
    typer.echo()
    typer.echo(str(event.data.get("dry_run_info", "")))
    typer.echo()


def _print_result_errors(result: FlashResult) -> None:
    seen = set()
    for error in result.errors:
        if error in seen:
            continue
        seen.add(error)
        typer.secho(error, fg=typer.colors.RED)
