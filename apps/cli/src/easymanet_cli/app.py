"""EasyMANET CLI — zero-touch OpenMANET provisioning and imaging.

Commands:
    easymanet disks                    List removable disks
    easymanet validate --config FILE   Validate fleet config
    easymanet render --config FILE     Render resolved provision.json
    easymanet flash --config FILE ...  Flash an image and stage node config
"""

from typing import Optional

import typer

from easymanet.diagnostics import export_support_bundle, import_boot_report, run_diagnostics
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
from easymanet.support_bundle import create_support_bundle
from .common import print_errors_and_warnings
from .flash import register_flash_command
from .image import register_image_commands

app = typer.Typer(
    name="easymanet",
    help="Zero-touch OpenMANET provisioning and imaging",
    no_args_is_help=True,
)
image_app = typer.Typer(help="Manage image URLs, cache, and firmware builds")
diagnostics_app = typer.Typer(help="Collect node diagnostics and support bundles")
app.add_typer(image_app, name="image")
app.add_typer(diagnostics_app, name="diagnostics")

register_flash_command(app)
register_image_commands(image_app)


@diagnostics_app.command(name="run")
def diagnostics_run_cmd(
    config: str = typer.Option("", "--config", "-c", help="Path or workspace name for a fleet config"),
):
    """Collect live EasyMANET diagnostics and print a copyable summary."""
    payload = run_diagnostics(config=config)
    if payload.get("summary"):
        typer.echo(payload["summary"])
    if not payload.get("ok"):
        for error in payload.get("errors", []):
            typer.secho(f"Error: {error}", fg=typer.colors.RED)
    raise typer.Exit(0 if payload.get("ok") else 1)


@diagnostics_app.command(name="bundle")
def diagnostics_bundle_cmd(
    config: str = typer.Option("", "--config", "-c", help="Path or workspace name for a fleet config"),
    node: str = typer.Option("", "--node", "-n", help="Node context to include in a local bundle"),
    boot_report: str = typer.Option("", "--boot-report", help="Boot report file or directory to include"),
    output: str = typer.Option("", "--output", "-o", help="Output .zip path for a local bundle"),
    include_disks: bool = typer.Option(False, "--include-disks", help="Include removable disk inventory"),
):
    """Export a zip support bundle under the shared Diagnostics folder."""
    if node or boot_report or output or include_disks:
        result = create_support_bundle(
            config=config,
            node=node,
            boot_report=boot_report,
            output=output,
            include_disks=include_disks,
        )
        typer.secho("Support bundle exported.", fg=typer.colors.GREEN)
        typer.echo(f"  Path: {result.path}")
        return

    payload = export_support_bundle(config=config)
    if payload.get("summary"):
        typer.echo(payload["summary"])
    if not payload.get("ok"):
        for error in payload.get("errors", []):
            typer.secho(f"Error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"Support bundle: {payload.get('bundle_path', '')}", fg=typer.colors.GREEN)


@diagnostics_app.command(name="import-boot-report")
def diagnostics_import_boot_report_cmd(
    source: str = typer.Option(..., "--source", "-s", help="Mounted boot drive or easymanet report folder"),
):
    """Import offline boot reports into the shared Diagnostics folder."""
    payload = import_boot_report(source=source)
    if not payload.get("ok"):
        for error in payload.get("errors", []):
            typer.secho(f"Error: {error}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"Imported boot reports: {payload.get('target', '')}", fg=typer.colors.GREEN)
    for path in payload.get("imported", []):
        typer.echo(f"  {path}")


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
