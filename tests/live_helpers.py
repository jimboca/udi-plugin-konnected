"""Helpers for live LAN smoke tests (Discover + REST/SSE connect)."""

from __future__ import annotations

import os
from typing import List, Optional

from konnected_client import DeviceType, KonnectedDevice, browse_konnected_gdos
from konnected_client.models import SEM_DOOR, SEM_LIGHT, SEM_LOCK, SEM_SYNCED


def resolve_smoke_hosts(mdns_timeout: float = 5.0) -> List[dict]:
    """Return discovered GDO descriptors, or a single host from KONNECTED_HOST."""
    env_host = (os.environ.get('KONNECTED_HOST') or '').strip()
    if env_host:
        return [{
            'host': env_host,
            'ip': env_host.split(':')[0],
            'port': int(env_host.split(':')[1]) if ':' in env_host else 80,
            'friendly_name': env_host,
            'project_name': os.environ.get('KONNECTED_PROJECT', ''),
            'mac': '',
            'source': 'env',
        }]
    found = browse_konnected_gdos(timeout=mdns_timeout)
    for entry in found:
        entry['source'] = 'mdns'
    return found


def require_smoke_host(mdns_timeout: float = 5.0) -> dict:
    """Return one GDO host or raise pytest.skip / fail based on env."""
    import pytest

    hosts = resolve_smoke_hosts(mdns_timeout=mdns_timeout)
    if hosts:
        return hosts[0]
    force = os.environ.get('KONNECTED_SMOKE', '').strip().lower() in ('1', 'true', 'yes')
    msg = (
        'No Konnected GDO found via mDNS and KONNECTED_HOST unset. '
        'Set KONNECTED_HOST=ip or ensure a blaQ is online on the LAN.'
    )
    if force:
        pytest.fail(msg)
    pytest.skip(msg)


def smoke_connect(host: str, ready_timeout: float = 15.0, attempts: int = 2) -> KonnectedDevice:
    """Connect REST/SSE (read-only). Caller must device.stop()."""
    last_err = 'unknown'
    for attempt in range(1, attempts + 1):
        device = KonnectedDevice(host)
        ok = device.start(wait_for_ready=True, timeout=ready_timeout)
        if ok and device.semantic_entity(SEM_DOOR):
            return device
        last_err = device.last_error or (
            f'SSE ready but no door entity (attempt {attempt}/{attempts}); '
            f'entities={device.entity_ids()}'
        )
        device.stop()
    raise RuntimeError(last_err)


def assert_blaq_basics(device: KonnectedDevice) -> None:
    """Read-only assertions for a typical blaQ after SSE discovery."""
    assert device.online, 'device should be online after start'
    assert device.semantic_entity(SEM_DOOR), f'missing door; entities={device.entity_ids()}'
    door = device.get_state(SEM_DOOR)
    assert door is not None, 'GET cover state failed'
    assert str(door.get('state', '')).upper() in ('OPEN', 'CLOSED'), door

    # blaQ usually has these; tolerate missing light/lock on odd firmware
    if device.device_type == DeviceType.BLAQ or device.has_light or device.has_lock:
        assert device.device_type in (DeviceType.BLAQ, DeviceType.UNKNOWN)

    if device.semantic_entity(SEM_SYNCED):
        synced = device.get_state(SEM_SYNCED)
        assert synced is not None
        assert str(synced.get('state', '')).upper() in ('ON', 'OFF')

    if device.has_light:
        light = device.get_state(SEM_LIGHT)
        assert light is not None
        assert str(light.get('state', '')).upper() in ('ON', 'OFF')

    if device.has_lock:
        lock = device.get_state(SEM_LOCK)
        assert lock is not None
