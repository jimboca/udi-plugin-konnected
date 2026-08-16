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
    """Log one device message when Debug + Stream is active.

    Uses INFO so lines appear under Debug + Stream even when PG3's logger
    level name mapping is quirky for custom levels.
    """
    if not _enabled:
        return
    LOGGER.info('%s %s %s', host, direction, detail)
