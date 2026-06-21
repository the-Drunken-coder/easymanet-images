"""Compatibility shim for image command registration.

The Typer command surface is owned by :mod:`easymanet_cli.image`. Keep this
module importable for older internal callers that still import
``easymanet_image.cli.register_image_commands``.
"""

from easymanet.download import get_cached_image, get_image_config
from easymanet_cli.common import maybe_show_update_notice
from easymanet_cli.image import register_image_commands
from easymanet_image.build import build_image

__all__ = [
    "build_image",
    "get_cached_image",
    "get_image_config",
    "maybe_show_update_notice",
    "register_image_commands",
]
