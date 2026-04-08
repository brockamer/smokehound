"""Shared utility functions."""

from __future__ import annotations

import re

import click

_WINDOW_RE = re.compile(r"^(\d+(?:\.\d+)?)([hmd])$")


def parse_window(window: str) -> float:
    """Parse a time window string like '4h', '24h', '7d', '30m' into seconds."""
    m = _WINDOW_RE.match(window)
    if not m:
        raise click.BadParameter(f"Unknown window format: {window!r}. Use e.g. '4h', '24h', '7d'.")
    value, unit = float(m.group(1)), m.group(2)
    multipliers = {"h": 3600, "d": 86400, "m": 60}
    return value * multipliers[unit]
