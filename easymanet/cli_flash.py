"""Flash command and image resolution helpers."""

import json
from pathlib import Path
from typing import Any, Optional

import typer

from .cli_common import maybe_show_update_notice, print_header
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
from .render import render, render_dict
from .validate import validate


SECRET_FIELD_NAMES = {"password", "root_password_hash"}
SECRET_LIST_FIELD_NAMES = {"ssh_authorized_keys"}
REDACTED_VALUE = "<redacted>"
CUSTOM_IMAGE_VERSION = "custom"


def redact_provision_for_display(value: Any, field_name: str = "") -> Any:
    """Return a display-safe copy of provision.json data."""
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
    manifest: Manifest,
    node: str,
    *,
    ssh_enabled: Optional[bool] = None,
    show_secrets: bool = False,
) -> str:
    if show_secrets:
        return render(manifest, node, ssh_enabled=ssh_enabled)
    provision = render_dict(manifest, node, ssh_enabled=ssh_enabled)
    return json.dumps(redact_provision_for_display(provision), indent=2)


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


def resolve_base_image(
    target: str,
    base_image: Optional[str],
    image_sha256: Optional[str],
    image_url: Optional[str],
    download: bool,
    no_download: bool,
    dry_run: bool,
) -> str:
    normalized_sha256: Optional[str] = None
    if image_sha256:
        try:
            normalized_sha256 = normalize_sha256(image_sha256)
        except ValueError as e:
            typer.secho(f"Invalid --image-sha256: {e}", fg=typer.colors.RED)
            raise typer.Exit(1) from e

    if base_image:
        if normalized_sha256:
            try:
                verify_image_sha256(Path(base_image), normalized_sha256)
            except OSError as e:
                typer.secho(f"Base image checksum error: {e}", fg=typer.colors.RED)
                raise typer.Exit(1) from e
            typer.secho("Base image SHA-256 verified.", fg=typer.colors.GREEN)
        else:
            typer.secho(
                "Warning: local --base-image was not verified with --image-sha256.",
                fg=typer.colors.YELLOW,
            )
        return base_image

    if image_url:
        if not normalized_sha256:
            typer.secho(
                "--image-url requires --image-sha256 so downloaded firmware can be verified.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        set_image_config(
            target,
            image_url,
            version=CUSTOM_IMAGE_VERSION,
            sha256=normalized_sha256,
        )
        typer.secho(f"Saved image URL for {target}. Run --download to fetch now.", fg=typer.colors.BLUE)
        if not download:
            typer.secho(
                "Image URL saved but not downloaded. Re-run with --download to fetch the image, "
                "or add --base-image to use a local file.",
                fg=typer.colors.BLUE,
            )
            raise typer.Exit(1)

    if no_download:
        typer.secho(
            "--no-download requires --base-image. No base image provided.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    if dry_run:
        cached = get_cached_image(target)
        if cached:
            return str(cached)
        return f"<auto-download for {target}>"

    if download:
        latest = check_latest_version(target)
        if not latest:
            typer.secho(
                f"No image URL configured for target '{target}'. "
                f"Configure one with --image-url or specify --base-image.\n"
                f"  easymanet flash --image-url https://example.com/image.img.gz "
                f"--image-sha256 <SHA256> ...",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        if not latest.sha256:
            typer.secho(
                f"No SHA-256 checksum configured or found for target '{target}'. "
                f"Use --image-url with --image-sha256 or configure one with "
                f"`easymanet image --set-url ... --set-sha256 ...`.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        path = download_image(target, latest.version, latest.url, latest.sha256, force=True)
        return str(path)

    latest = check_latest_version(target)
    if not latest:
        typer.secho(
            f"No image configured for target '{target}' and no --base-image given.\n"
            f"\n"
            f"Configure an image URL with:\n"
            f"  easymanet flash --image-url <URL> --image-sha256 <SHA256> ...\n"
            f"\n"
            f"Or download an image and pass it with:\n"
            f"  easymanet flash --base-image <path-to-image> --image-sha256 <SHA256> ...\n"
            f"\n"
            f"OpenMANET firmware releases can be downloaded from:\n"
            f"  https://github.com/OpenMANET/firmware/releases",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    if not latest.sha256:
        typer.secho(
            f"No SHA-256 checksum configured or found for target '{target}'. "
            f"Use --image-url with --image-sha256 or configure one with "
            f"`easymanet image --set-url ... --set-sha256 ...`.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    cached = get_cached_image(target, sha256=latest.sha256, url=latest.url)
    if cached:
        typer.secho(f"Using verified cached image: {cached}", fg=typer.colors.BLUE)
    else:
        cached = download_image(target, latest.version, latest.url, latest.sha256)
    return str(cached)


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
        check_platform()
        if enable_ssh and disable_ssh:
            typer.secho(
                "Cannot use --enable-ssh and --disable-ssh together.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        if download and no_download:
            typer.secho(
                "Cannot use --download and --no-download together.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
        if not yes and not dry_run:
            typer.secho(
                "--yes is required to flash. Use --dry-run to preview first.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(1)

        maybe_show_update_notice()

        try:
            manifest = load_manifest(config)
        except ManifestError as e:
            typer.secho(f"Error: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)

        result = validate(manifest, node_name=node)
        if result.errors:
            typer.secho("Config validation failed:", fg=typer.colors.RED)
            for e in result.errors:
                typer.secho(f"  ✗ {e}", fg=typer.colors.RED)
            raise typer.Exit(1)

        resolved = render_dict(manifest, node)
        target = resolved["node"]["target"]
        role = resolved["node"]["role"]
        ssh_enabled = resolve_flash_ssh_enabled(
            enable_ssh=enable_ssh, disable_ssh=disable_ssh
        )

        image_path = resolve_base_image(
            target,
            base_image,
            image_sha256,
            image_url,
            download,
            no_download,
            dry_run,
        )

        print_header("Flash Plan")
        typer.echo(f"  Config:       {config}")
        typer.echo(f"  Node:         {node}")
        typer.echo(f"  Hostname:     {resolved['node']['hostname']}")
        typer.echo(f"  Role:         {role}")
        typer.echo(f"  Target:       {target}")
        typer.echo(f"  Base image:   {image_path}")
        typer.echo(f"  Device:       {device}")
        typer.echo("  Boot payload: /easymanet/provision.json")
        typer.echo(
            f"  SSH:          {flash_ssh_note(role, enable_ssh=enable_ssh, disable_ssh=disable_ssh)}"
        )
        typer.echo()

        try:
            disk = lookup_device(device)
            if disk:
                typer.echo("  Disk details:")
                typer.echo(f"    Model:      {disk.model}")
                typer.echo(f"    Size:       {disk.size_human}")
                typer.echo(f"    Removable:  {'yes' if disk.removable else 'no'}")
                mounted_str = ", ".join(disk.mounted) if disk.mounted else "(none)"
                typer.echo(f"    Mounted:    {mounted_str}")
                for w in disk.blocking_warnings:
                    typer.secho(f"    {w}", fg=typer.colors.RED)
                typer.echo()
            assert_flash_allowed(device, force=force)
        except ValueError as e:
            typer.secho(f"  Flash safety: {e}", fg=typer.colors.RED)
            if not dry_run:
                raise typer.Exit(1)
            typer.echo()

        print_header("Resolved provision.json")
        print(
            render_provision_for_display(
                manifest,
                node,
                ssh_enabled=ssh_enabled,
                show_secrets=show_secrets,
            )
        )
        if not show_secrets:
            typer.secho(
                "  Secrets redacted. Use --show-secrets to print the full payload.",
                fg=typer.colors.BLUE,
            )
        print()

        typer.echo(inject_dry_run_info(manifest, node))
        print()

        if dry_run:
            typer.secho("Dry run complete. No changes were made.", fg=typer.colors.GREEN)
            return

        try:
            check_privileges(device)
        except PrivilegeError as e:
            typer.secho(str(e), fg=typer.colors.RED)
            raise typer.Exit(1)

        try:
            flash_image(
                device=device,
                image_path=image_path,
                force=force,
                skip_overlay_wipe=skip_overlay_wipe,
            )
        except FlashError as e:
            typer.secho(f"Flash error: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)

        typer.echo()
        print_header("Writing boot-partition payload")
        try:
            results = inject(
                device=device,
                manifest=manifest,
                node_name=node,
                ssh_enabled=ssh_enabled,
            )
            for path, ok in results:
                status = "✓" if ok else "✗"
                color = typer.colors.GREEN if ok else typer.colors.RED
                typer.secho(f"  {status} {path}", fg=color)
        except InjectError as e:
            typer.secho(f"Boot payload error: {e}", fg=typer.colors.RED)
            typer.secho(
                "Image was written but boot-partition provisioning failed. "
                "Re-run the full flash command (same --base-image or cached image) after fixing the issue.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(1)

        if not finish_flash(device, eject=not no_eject):
            raise typer.Exit(1)
        typer.secho(
            f"\nDone. Insert the drive into the Raspberry Pi for {node} and boot.",
            fg=typer.colors.GREEN,
        )
