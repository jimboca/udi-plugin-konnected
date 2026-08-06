"""mDNS / Zeroconf discovery for Konnected ESPHome devices."""

from __future__ import annotations

import socket
import sys
import time
from typing import Dict, List, Optional, Tuple

from udi_interface import LOGGER

# Konnected advertises this service even when the ESPHome native API is disabled.
SERVICE_TYPE = '_konnected._tcp.local.'

# project_name TXT substrings that identify garage door openers (not alarm panels)
GDO_PROJECT_MARKERS = (
    'garage-door',
    'garage_door',
    'gdov2-q',
    'gdov2-s',
    'gdov1-s',
    'gdov2_q',
    'gdov2_s',
    'gdov1_s',
)

DEFAULT_BROWSE_SECONDS = 5.0


def _txt_decode(properties: Optional[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not properties:
        return out
    for key, value in properties.items():
        k = key.decode('utf-8', 'replace') if isinstance(key, bytes) else str(key)
        if value is None:
            out[k] = ''
        elif isinstance(value, bytes):
            out[k] = value.decode('utf-8', 'replace')
        else:
            out[k] = str(value)
    return out


def is_garage_door_project(project_name: str) -> bool:
    """Return True for GDO firmware; False for alarm panels / other Konnected gear.

    Empty project_name is treated as unknown (not a confirmed GDO) so callers can
    notice it; SSE cover detection is the final gate for adding nodes.
    """
    name = (project_name or '').lower().strip()
    if not name:
        return False
    return any(marker in name for marker in GDO_PROJECT_MARKERS)


def _entry_from_info(name: str, info) -> Optional[dict]:
    txt = _txt_decode(info.properties)
    project = txt.get('project_name', '')

    addresses = []
    try:
        addresses = list(info.parsed_addresses())
    except Exception:
        pass
    if not addresses and info.addresses:
        for raw in info.addresses:
            try:
                if len(raw) == 4:
                    addresses.append(socket.inet_ntoa(raw))
                elif len(raw) == 16:
                    addresses.append(socket.inet_ntop(socket.AF_INET6, raw))
            except Exception:
                continue

    ipv4 = [a for a in addresses if ':' not in a]
    host_ip = ipv4[0] if ipv4 else (addresses[0] if addresses else None)
    hostname = (info.server or '').rstrip('.')
    host = host_ip or hostname
    if not host:
        LOGGER.warning('mDNS service %s had no address', name)
        return None

    port = int(info.port or 80)
    is_gdo = is_garage_door_project(project)
    return {
        'host': host if port == 80 else f'{host}:{port}',
        'ip': host_ip or host,
        'port': port,
        'hostname': hostname,
        'name': name,
        'friendly_name': txt.get('friendly_name') or hostname or host,
        'project_name': project,
        'project_version': txt.get('project_version', ''),
        'mac': txt.get('mac', ''),
        'esphome_version': txt.get('esphome_version', ''),
        'is_gdo': is_gdo,
    }


def browse_konnected_devices(
    timeout: float = DEFAULT_BROWSE_SECONDS,
) -> Tuple[List[dict], List[dict], Optional[str]]:
    """Browse `_konnected._tcp`.

    Returns ``(gdos, others, error)`` where ``others`` are Konnected devices that
    are not recognized garage door projects (e.g. alarm panels), and ``error`` is
    a user-facing message when the browse could not run.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        msg = (
            'Python package "zeroconf" is not installed — mDNS discovery unavailable. '
            'On FreeBSD: pkg install py311-zeroconf (or pip install zeroconf).'
        )
        LOGGER.error(msg)
        return [], [], msg

    found: Dict[str, dict] = {}

    def _on_service_state_change(**kwargs):
        zeroconf = kwargs.get('zeroconf')
        service_type = kwargs.get('service_type')
        name = kwargs.get('name')
        state_change = kwargs.get('state_change')
        if (
            zeroconf is None
            or not name
            or not service_type
            or state_change is not ServiceStateChange.Added
        ):
            return
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=3000)
        except Exception:
            LOGGER.exception('mDNS get_service_info failed for %s', name)
            return
        if info is None:
            return

        entry = _entry_from_info(name, info)
        if entry is None:
            return
        key = entry['mac'] or f"{entry['host']}"
        found[key] = entry
        kind = 'GDO' if entry['is_gdo'] else 'other'
        LOGGER.info(
            'mDNS found Konnected %s: %s at %s (%s)',
            kind,
            entry['friendly_name'],
            entry['host'],
            entry['project_name'] or 'unknown project',
        )

    def _zeroconf_kwargs(unicast: bool = False) -> dict:
        """Ctor kwargs that avoid FreeBSD errno 49 (loopback reply path).

        Bare ``Zeroconf()`` uses ``InterfaceChoice.All``, which includes
        ``127.0.0.1``. On FreeBSD/macOS, asyncio then logs
        ``Can't assign requested address`` when mDNS tries to reply via
        loopback. Prefer non-loopback IPv4 (or Default + V4Only).
        """
        kw: dict = {'unicast': unicast}
        try:
            from zeroconf import InterfaceChoice, IPVersion
            from zeroconf._utils.net import get_all_addresses
        except ImportError:
            return kw

        bsdish = sys.platform.startswith(('freebsd', 'darwin'))
        non_loopback = [
            a
            for a in get_all_addresses()
            if a and not str(a).startswith('127.') and ':' not in str(a)
        ]
        if non_loopback:
            kw['interfaces'] = non_loopback
            kw['ip_version'] = IPVersion.V4Only
        elif bsdish:
            kw['interfaces'] = InterfaceChoice.Default
            kw['ip_version'] = IPVersion.V4Only
        return kw

    def _open_zeroconf():
        # Prefer multicast; fall back to unicast when 5353 is already taken
        # (common when HomeKit Hub / another mDNS client owns the port).
        try:
            return Zeroconf(**_zeroconf_kwargs(unicast=False))
        except OSError as exc:
            LOGGER.warning(
                'mDNS multicast bind failed (%s); retrying with unicast=True', exc
            )
            return Zeroconf(**_zeroconf_kwargs(unicast=True))

    try:
        zc = _open_zeroconf()
    except Exception as exc:
        msg = f'mDNS could not start ({exc}). You can still set hosts manually.'
        LOGGER.exception(msg)
        return [], [], msg

    try:
        ServiceBrowser(zc, SERVICE_TYPE, handlers=[_on_service_state_change])
        time.sleep(max(0.5, float(timeout)))
    finally:
        try:
            zc.close()
        except Exception:
            pass

    gdos = [e for e in found.values() if e.get('is_gdo')]
    others = [e for e in found.values() if not e.get('is_gdo')]
    return gdos, others, None


def browse_konnected_gdos(timeout: float = DEFAULT_BROWSE_SECONDS) -> List[dict]:
    """Compatibility helper — returns only recognized GDO descriptors."""
    gdos, _others, _err = browse_konnected_devices(timeout=timeout)
    return gdos
