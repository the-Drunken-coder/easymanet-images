"""EasyMANET CLI — zero-touch OpenMANET provisioning and imaging.

Commands:
    easymanet disks                    List removable disks
    easymanet validate --config FILE   Validate fleet config
    easymanet render --config FILE     Render resolved provision.json
    easymanet flash --config FILE ...  Flash an image and stage node config
"""

from typing import Optional

import typer

from .cli_common import print_errors_and_warnings, print_header
from .cli_flash import register_flash_command, resolve_flash_ssh_enabled
from .cli_image import register_image_commands
from .disks import DiskInfo, list_disks
from .manifest import ManifestError, load_manifest
from .platform import check_platform
from .render import render
from .validate import validate

# Re-export for tests and backward compatibility.
_resolve_flash_ssh_enabled = resolve_flash_ssh_enabled

app = typer.Typer(
    name="easymanet",
    help="Zero-touch OpenMANET provisioning and imaging",
    no_args_is_help=True,
)
image_app = typer.Typer(help="Manage image URLs, cache, and firmware builds")
app.add_typer(image_app, name="image")

register_flash_command(app)
register_image_commands(image_app)


@app.command(name="validate")
def validate_cmd(
    config: str = typer.Option(
        ..., "--config", "-c", help="Path to fleet.yml config file"
    ),
    node: Optional[str] = typer.Option(
        None, "--node", "-n", help="Validate a specific node"
    ),
):
    """Validate a fleet.yml config file."""
    try:
        manifest = load_manifest(config)
    except ManifestError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1) from e

    result = validate(manifest, node_name=node)
    typer.secho(f"Validating: {config}", bold=True)
    if node:
        typer.secho(f"Selected node: {node}")

    exit_code = print_errors_and_warnings(result)
    raise typer.Exit(exit_code)


@app.command(name="render")
def render_cmd(
    config: str = typer.Option(
        ..., "--config", "-c", help="Path to fleet.yml config file"
    ),
    node: str = typer.Option(
        ..., "--node", "-n", help="Node name to render resolved config for"
    ),
):
    """Render the resolved provision.json for a node."""
    try:
        manifest = load_manifest(config)
    except ManifestError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1) from e

    result = validate(manifest, node_name=node)
    if result.errors:
        typer.secho("Config has validation errors:", fg=typer.colors.RED)
        for e in result.errors:
            typer.secho(f"  ✗ {e}", fg=typer.colors.RED)
        raise typer.Exit(1)

    output = render(manifest, node)
    print(output)


@app.command(name="disks")
def disks_cmd(
    all_disks: bool = typer.Option(
        False,
        "--all",
        help="List every block device, not only removable/USB/MMC (Linux) or external (macOS)",
    ),
):
    """List available disks for flashing."""
    check_platform()
    disks = list_disks(include_all=all_disks)

    if not disks:
        msg = "No disks found." if all_disks else "No removable/external disks found."
        typer.secho(msg, fg=typer.colors.YELLOW)
        if not all_disks:
            typer.echo("Use --all to include every block device.")
        return

    for d in disks:
        removable = "yes" if d.removable else "no"
        mounted_str = ", ".join(d.mounted) if d.mounted else "(none)"
        typer.echo(f"  {d.device}")
        typer.echo(f"    Model:      {d.model}")
        typer.echo(f"    Size:       {d.size_human}")
        typer.echo(f"    Removable:  {removable}")
        typer.echo(f"    Mounted:    {mounted_str}")

        for w in d.warnings:
            typer.secho(f"    {w}", fg=typer.colors.RED)

        typer.echo()


def main():
    app()


if __name__ == "__main__":
    main()
