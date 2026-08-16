"""Toggle verbose Konnected device traffic logging (Debug + Stream)."""

from __future__ import annotations

from typing import Any

from udi_interface import LOGGER

_enabled = False


def set_enabled(on: bool) -> None:
    global _enabled
    was = _enabled
    _enabled = bool(on)
    if _enabled != was:
        LOGGER.info('Debug + Stream device logging %s', 'ON' if _enabled else 'OFF')


def enabled() -> bool:
    return _enabled


def log(host: str, direction: str, detail: Any) -> None:
    """Log one device message when Debug + Stream is active."""
    if not _enabled:
        return
    LOGGER.debug('%s %s %s', host, direction, detail)
