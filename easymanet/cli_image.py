"""Image management and build commands."""

from typing import Optional

import typer

from .build import (
    DEFAULT_BOARD,
    DEFAULT_OPENMANET_REPO,
    DEFAULT_OPENMANET_VERSION,
    DEFAULT_TARGET,
    BuildError,
    build_image,
)
from .cli_common import maybe_show_update_notice, print_header
from .download import get_cached_image, get_image_config, normalize_sha256, set_image_config
from .format import human_size


def register_image_commands(image_app: typer.Typer) -> None:
    @image_app.callback(invoke_without_command=True)
    def image_cmd(
        ctx: typer.Context,
        target: str = typer.Option(
            "rpi4-mm6108-spi", "--target", "-t", help="Target hardware"
        ),
        set_url: Optional[str] = typer.Option(
            None, "--set-url", help="Set the download URL for the target"
        ),
        set_version: Optional[str] = typer.Option(
            None, "--set-version", help="Set the version label"
        ),
        set_sha256: Optional[str] = typer.Option(
            None, "--set-sha256", help="Set the expected SHA-256 checksum"
        ),
        show: bool = typer.Option(
            False, "--show", help="Show current image config"
        ),
    ):
        """Manage base image download URLs and cache."""
        del show
        if ctx.invoked_subcommand:
            return

        if set_url:
            if not set_sha256:
                typer.secho(
                    "--set-url requires --set-sha256 so downloaded firmware can be verified.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            try:
                sha256 = normalize_sha256(set_sha256)
            except ValueError as e:
                typer.secho(str(e), fg=typer.colors.RED)
                raise typer.Exit(1) from e
            set_image_config(target, set_url, set_version or "custom", sha256=sha256)
            typer.secho(f"Image URL set for {target}:", fg=typer.colors.GREEN)
            typer.echo(f"  URL: {set_url}")
            typer.echo(f"  Version: {set_version or 'custom'}")
            typer.echo(f"  SHA256: {sha256}")
            return

        info = get_image_config(target)
        cached = get_cached_image(target)

        if not info and not cached:
            typer.secho(f"No image configured for {target}.", fg=typer.colors.YELLOW)
            typer.echo("")
            typer.echo("Configure one with:")
            typer.echo("  easymanet image --set-url <URL> --set-sha256 <SHA256>")
            return

        print_header(f"Image config: {target}")
        if info:
            typer.echo(f"  URL:     {info.get('url', '(none)')}")
            typer.echo(f"  Version: {info.get('version', '(none)')}")
            typer.echo(f"  SHA256:  {info.get('sha256', '(none)')}")
            typer.echo(f"  Desc:    {info.get('description', '')}")
        if cached:
            size = cached.stat().st_size
            typer.echo(f"  Cached:  {cached} ({human_size(size)})")
        else:
            typer.echo("  Cached:  none")

    @image_app.command(name="build")
    def image_build_cmd(
        output_dir: str = typer.Option(
            "dist", "--output-dir", "-o", help="Directory to copy the built image into"
        ),
        openmanet_version: str = typer.Option(
            DEFAULT_OPENMANET_VERSION,
            "--openmanet-version",
            help="OpenMANET/OpenWrt tag or branch to build",
        ),
        board: str = typer.Option(
            DEFAULT_BOARD,
            "--board",
            help="OpenMANET board profile passed to openmanet_setup.sh",
        ),
        target: str = typer.Option(
            DEFAULT_TARGET,
            "--target",
            "-t",
            help="Expected firmware artifact target suffix",
        ),
        repo_url: str = typer.Option(
            DEFAULT_OPENMANET_REPO,
            "--repo-url",
            help="OpenMANET/OpenWrt git repository URL",
        ),
        jobs: Optional[int] = typer.Option(
            None,
            "--jobs",
            "-j",
            help="Parallel make jobs inside the Docker builder",
        ),
        clean: bool = typer.Option(
            False,
            "--clean",
            help="Delete the cached OpenMANET source tree before cloning/building",
        ),
        rebuild_builder: bool = typer.Option(
            False,
            "--rebuild-builder",
            help="Force a rebuild of the Docker builder image",
        ),
        cache_dir: Optional[str] = typer.Option(
            None,
            "--cache-dir",
            help="Host directory to mount as the OpenMANET build cache instead of a Docker volume",
        ),
    ):
        """Build an EasyMANET-flavored OpenMANET image in Docker."""
        maybe_show_update_notice()
        print_header("Image Build")
        typer.echo(f"  Repo:         {repo_url}")
        typer.echo(f"  Version:      {openmanet_version}")
        typer.echo(f"  Board:        {board}")
        typer.echo(f"  Target:       {target}")
        typer.echo(f"  Output dir:   {output_dir}")
        if cache_dir:
            typer.echo(f"  Cache dir:    {cache_dir}")
        typer.echo("  Overlay:      provisioning/openwrt-overlay")
        typer.echo()

        try:
            artifact = build_image(
                output_dir=output_dir,
                openmanet_version=openmanet_version,
                board=board,
                target=target,
                repo_url=repo_url,
                jobs=jobs,
                clean=clean,
                rebuild_builder=rebuild_builder,
                cache_dir=cache_dir,
            )
        except BuildError as e:
            typer.secho(f"Build error: {e}", fg=typer.colors.RED)
            raise typer.Exit(1) from e

        typer.secho("Build complete.", fg=typer.colors.GREEN)
        typer.echo(f"  Image: {artifact}")
