"""EasyMANET CLI — zero-touch OpenMANET provisioning and imaging.

Commands:
    easymanet disks                    List removable disks
    easymanet validate --config FILE   Validate fleet config
    easymanet render --config FILE     Render resolved provision.json
    easymanet flash --config FILE ...  Flash an image and stage node config
"""

from typing import Optional

import typer

from easymanet.disks import list_disks
from easymanet.manifest import ManifestError, load_manifest
from easymanet.platform import check_platform
from easymanet.render import render
from easymanet.validate import validate
from easymanet.workspace import (
    ensure_workspace,
    fleet_file_records,
    fleets_dir,
    resolve_fleet_config,
    workspace_payload,
)
from easymanet_image.cli import register_image_commands

from .common import print_errors_and_warnings
from .flash import register_flash_command

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
    config_path = resolve_fleet_config(config)
    try:
        manifest = load_manifest(str(config_path))
    except ManifestError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1) from e

    result = validate(manifest, node_name=node)
    typer.secho(f"Validating: {config_path}", bold=True)
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
    config_path = resolve_fleet_config(config)
    try:
        manifest = load_manifest(str(config_path))
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


@app.command(name="init")
def init_cmd():
    """Create the shared EasyMANET workspace in Documents."""
    payload = workspace_payload()
    typer.secho("EasyMANET workspace ready.", fg=typer.colors.GREEN)
    typer.echo(f"  Root:        {payload['root']}")
    typer.echo(f"  Fleets:      {payload['fleets_dir']}")
    typer.echo(f"  Images:      {payload['images_dir']}")
    typer.echo(f"  Diagnostics: {payload['diagnostics_dir']}")
    typer.echo(f"  Builds:      {payload['builds_dir']}")


@app.command(name="fleets")
def fleets_cmd():
    """List fleet files in the shared EasyMANET workspace."""
    records = fleet_file_records()
    typer.echo(f"Fleets folder: {fleets_dir()}")
    if not records:
        typer.secho("No fleet files found.", fg=typer.colors.YELLOW)
        typer.echo("Add .yml or .yaml files to the Fleets folder above.")
        return
    for record in records:
        typer.echo(f"  {record['relative_path']}")


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
    ensure_workspace()
    app()


if __name__ == "__main__":
    main()
