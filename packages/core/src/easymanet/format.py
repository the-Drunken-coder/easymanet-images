"""Shared formatting helpers."""


def human_size(n: int) -> str:
    if n < 0:
        raise ValueError("size must be non-negative")
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    return f"{n / 1024**3:.1f} GB"
