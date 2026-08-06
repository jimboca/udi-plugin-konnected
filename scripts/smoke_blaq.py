#!/usr/bin/env python3
"""CLI smoke: mDNS discover + read-only REST/SSE connect to a Konnected GDO.

Usage:
  PYTHONPATH=. python3 scripts/smoke_blaq.py
  PYTHONPATH=. python3 scripts/smoke_blaq.py --host 192.168.1.18
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG = ROOT / 'smoke_last.txt'


def out(msg: str) -> None:
    print(msg, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(msg + '\n')


def main() -> int:
    LOG.write_text('start\n', encoding='utf-8')
    from konnected_client.models import SEM_DOOR, SEM_LIGHT, SEM_LOCK, SEM_SYNCED
    from tests.live_helpers import assert_blaq_basics, resolve_smoke_hosts, smoke_connect

    parser = argparse.ArgumentParser(description='Konnected blaQ read-only smoke test')
    parser.add_argument('--host', default='', help='Device IP (skip mDNS)')
    parser.add_argument('--mdns-timeout', type=float, default=6.0)
    args = parser.parse_args()
    if args.host:
        os.environ['KONNECTED_HOST'] = args.host

    hosts = resolve_smoke_hosts(mdns_timeout=args.mdns_timeout)
    if not hosts:
        out('FAIL: no Konnected GDO found (mDNS empty and no --host / KONNECTED_HOST)')
        return 1

    entry = hosts[0]
    host = entry['host']
    out(
        'OK discover source=%s host=%s name=%s project=%s'
        % (entry.get('source'), host, entry.get('friendly_name'), entry.get('project_name'))
    )

    device = smoke_connect(host)
    try:
        assert_blaq_basics(device)
        out(
            'OK connected type=%s online=%s entities=%s'
            % (device.device_type.value, device.online, len(device.entity_ids()))
        )
        out('semantic=%s' % (sorted(device._semantic.keys()),))
        for sem in (SEM_DOOR, SEM_LIGHT, SEM_LOCK, SEM_SYNCED):
            st = device.get_state(sem)
            if st is not None:
                out('%s: state=%s op=%s' % (sem, st.get('state'), st.get('current_operation')))
        out('SMOKE PASS (read-only)')
        return 0
    except Exception as exc:
        out('FAIL connect/assert: %s' % (exc,))
        return 2
    finally:
        device.stop()


if __name__ == '__main__':
    sys.exit(main())
