"""Image checksum and payload integrity helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import zlib

SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")


def normalize_sha256(value: str) -> str:
    digest = value.strip()
    if digest.lower().startswith("sha256:"):
        digest = digest.split(":", 1)[1].strip()
    if not SHA256_PATTERN.match(digest):
        raise ValueError("SHA-256 checksum must be 64 hexadecimal characters")
    return digest.lower()


def image_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_image_sha256(path: Path, expected_sha256: str) -> None:
    expected = normalize_sha256(expected_sha256)
    actual = image_sha256(path)
    if actual != expected:
        raise OSError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def valid_image_payload(path: Path, filename: str) -> bool:
    named_path = Path(filename)
    suffix = named_path.suffix.lower()
    if suffix == ".img":
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    if suffix != ".gz" or not named_path.stem.lower().endswith(".img"):
        return False
    try:
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        total = 0
        with path.open("rb") as f:
            while not decompressor.eof:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(decompressor.decompress(chunk))
    except (OSError, zlib.error):
        return False
    return decompressor.eof and total > 0
