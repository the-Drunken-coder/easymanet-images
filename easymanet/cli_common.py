"""Shared CLI helpers."""

import os
import sys

import typer

from . import __version__
from .download import check_easymanet_update
from .validate import ValidationResult


def print_header(text: str) -> None:
    typer.secho(text, bold=True)


def maybe_show_update_notice() -> None:
    if os.environ.get("EASYMANET_SKIP_UPDATE_CHECK", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return
    update = check_easymanet_update()
    if update:
        typer.secho(
            f"EasyMANET {update} is available (you have {__version__}). "
            f"Run: {_upgrade_command()}",
            fg=typer.colors.YELLOW,
        )


def _upgrade_command() -> str:
    in_venv = bool(os.environ.get("VIRTUAL_ENV")) or (
        getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    )
    if in_venv:
        return "pip3 install --upgrade easymanet"
    return "pipx upgrade easymanet"


def print_errors_and_warnings(result: ValidationResult) -> int:
    exit_code = 0
    if result.errors:
        typer.secho(f"\n{len(result.errors)} error(s):", fg=typer.colors.RED)
        for e in result.errors:
            typer.secho(f"  ✗ {e}", fg=typer.colors.RED)
        exit_code = 1
    if result.warnings:
        typer.secho(f"\n{len(result.warnings)} warning(s):", fg=typer.colors.YELLOW)
        for w in result.warnings:
            typer.secho(f"  ⚠ {w}", fg=typer.colors.YELLOW)
    if result.valid and not result.warnings:
        typer.secho("✓ Config is valid", fg=typer.colors.GREEN)
    elif result.valid:
        typer.secho("✓ Config is valid (with warnings)", fg=typer.colors.GREEN)
    return exit_code
