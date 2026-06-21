"""Disk image payload validation helpers."""

from __future__ import annotations

from pathlib import Path
import zlib


def gzip_decompressed_bytes(image_path: Path) -> int:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    with image_path.open("rb") as f:
        while not decompressor.eof:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            total += len(decompressor.decompress(chunk))

    if not decompressor.eof:
        raise zlib.error("compressed image ended before the gzip stream completed")
    return total


def check_gzip_payload(image_path: Path) -> int:
    total = gzip_decompressed_bytes(image_path)
    if total == 0:
        raise zlib.error("compressed image did not contain a disk image payload")
    return total
