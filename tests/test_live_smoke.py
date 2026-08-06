"""Live LAN smoke: mDNS Discover + REST/SSE connect to a Konnected GDO.

Read-only — does not open/close the door.

Run (default; skips if no device):
  PYTHONPATH=. python3 -m pytest tests/test_live_smoke.py -v

Force failure if none found:
  KONNECTED_SMOKE=1 PYTHONPATH=. python3 -m pytest tests/test_live_smoke.py -v

Pin a host (skip mDNS):
  KONNECTED_HOST=192.168.1.18 PYTHONPATH=. python3 -m pytest tests/test_live_smoke.py -v
"""

from __future__ import annotations

import pytest

from konnected_client import DeviceType, browse_konnected_gdos
from konnected_client.models import SEM_DOOR
from tests.live_helpers import (
    assert_blaq_basics,
    require_smoke_host,
    resolve_smoke_hosts,
    smoke_connect,
)


@pytest.mark.live
def test_mdns_discovers_konnected_gdo():
    """Browse `_konnected._tcp` and expect at least one GDO when LAN has one.

    Skips when KONNECTED_HOST is set (explicit IP path covers connect instead).
    """
    import os

    if (os.environ.get('KONNECTED_HOST') or '').strip():
        pytest.skip('KONNECTED_HOST set — mDNS browse covered by connect smoke')
    hosts = resolve_smoke_hosts(mdns_timeout=6.0)
    if not hosts:
        require_smoke_host(mdns_timeout=0.1)  # skip/fail consistently
    entry = hosts[0]
    assert entry.get('host') or entry.get('ip')
    project = (entry.get('project_name') or '').lower()
    if project:
        assert 'garage' in project or 'gdov' in project, project
    # Prefer seeing blaQ project when present
    mdns = browse_konnected_gdos(timeout=1.0)  # already browsed; quick empty OK
    assert isinstance(mdns, list)


@pytest.mark.live
def test_discover_and_connect_blaq_readonly():
    """Discover (mDNS or KONNECTED_HOST), SSE-connect, read door/light/synced."""
    entry = require_smoke_host(mdns_timeout=6.0)
    host = entry['host']
    device = smoke_connect(host, ready_timeout=15.0)
    try:
        assert device.semantic_entity(SEM_DOOR)
        assert_blaq_basics(device)
        # Classification should land on blaQ for GDOv2-Q firmware
        project = (entry.get('project_name') or '').lower()
        if 'gdov2-q' in project or 'gdov2_q' in project:
            assert device.device_type == DeviceType.BLAQ
            assert device.has_light
            assert device.has_lock
    finally:
        device.stop()
