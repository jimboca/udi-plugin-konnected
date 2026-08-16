"""Optional verbose logging of native API state/command traffic."""

from __future__ import annotations

from typing import Any

from udi_interface import LOGGER

_enabled = False


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)
    LOGGER.info('Konnected API stream debug %s', 'ON' if _enabled else 'OFF')


def enabled() -> bool:
    return _enabled


def log(host: str, label: str, payload: Any = None) -> None:
    if not _enabled:
        return
    if payload is None:
        LOGGER.info('%s %s', host, label)
    else:
        LOGGER.info('%s %s %s', host, label, payload)
