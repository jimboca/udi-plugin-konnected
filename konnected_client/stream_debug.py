"""Optional verbose logging of native API state/command traffic."""

from __future__ import annotations

import logging
from typing import Any

from udi_interface import LOGGER

_enabled = False

# Packet-level DEBUG from aioesphomeapi is multi-line and floods debug.log.
# We surface useful traffic via short API state INFO lines instead.
_LIBRARY_LOGGERS = (
    'aioesphomeapi',
    'zeroconf',
)


def quiet_library_loggers(level: int = logging.WARNING) -> None:
    """Keep third-party API/mDNS loggers from drowning plugin logs."""
    for name in _LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(level)


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)
    # Always keep library packet dumps off; stream mode uses our short lines.
    quiet_library_loggers(logging.WARNING)
    LOGGER.info('Konnected API stream debug %s', 'ON' if _enabled else 'OFF')


def enabled() -> bool:
    return _enabled


def log(host: str, label: str, payload: Any = None) -> None:
    if not _enabled:
        return
    if payload is None:
        LOGGER.info('%s %s', host, label)
        return
    if isinstance(payload, dict):
        # Compact: "192.168.1.18 API state door OPEN CLOSING pos=1.0"
        parts = [host, label]
        sem = payload.get('sem')
        if sem is not None:
            parts.append(str(sem))
        state = payload.get('state')
        if state is not None:
            parts.append(str(state))
        op = payload.get('current_operation')
        if op is not None and op != 'IDLE':
            parts.append(str(op))
        if 'position' in payload and payload.get('position') is not None:
            parts.append(f"pos={payload['position']}")
        elif payload.get('value') is not None and state is None:
            parts.append(f"val={payload['value']}")
        LOGGER.info('%s', ' '.join(parts))
        return
    LOGGER.info('%s %s %s', host, label, payload)
