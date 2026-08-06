"""Device typing and semantic entity mapping for Konnected GDO variants."""

from __future__ import annotations

from enum import Enum
from typing import Dict, Iterable, Optional, Tuple


class DeviceType(str, Enum):
    """Hardware / firmware family."""

    UNKNOWN = 'unknown'
    BLAQ = 'blaq'       # GDOv2-Q — Security+ wireline
    WHITE = 'white'     # GDOv2-S / GDOv1-S — dry contact + sensors


# Semantic keys used by the plugin (not ESPHome entity names)
SEM_DOOR = 'door'
SEM_LIGHT = 'light'
SEM_LOCK = 'lock'
SEM_MOTION = 'motion'
SEM_OBSTRUCTION = 'obstruction'
SEM_SYNCED = 'synced'
SEM_MOTOR = 'motor'
SEM_WALL_BUTTON = 'wall_button'
SEM_OPENINGS = 'openings'
SEM_DEVICE_ID = 'device_id'
SEM_WIFI = 'wifi'
SEM_LEARN = 'learn'
SEM_RESYNC = 'resync'
SEM_WIRED_SENSOR = 'wired_sensor'
SEM_RANGE = 'range'


def _entity_parts(entity_id: str) -> Tuple[str, str]:
    """Return (domain, name_lower) from a new- or legacy-format entity id."""
    if '/' in entity_id:
        domain, _, name = entity_id.partition('/')
        return domain, name.lower()
    # Legacy: binary-sensor-motion → try multi-word domains
    for prefix, domain in (
        ('binary-sensor-', 'binary_sensor'),
        ('text-sensor-', 'text_sensor'),
        ('alarm-control-panel-', 'alarm_control_panel'),
    ):
        if entity_id.startswith(prefix):
            return domain, entity_id[len(prefix):].replace('_', ' ').lower()
    if '-' in entity_id:
        domain, _, rest = entity_id.partition('-')
        return domain, rest.replace('_', ' ').lower()
    return '', entity_id.lower()


def classify_device(entity_ids: Iterable[str]) -> DeviceType:
    """Infer blaQ vs White from discovered entity set.

    blaQ exposes Security+ extras (Synced, Lock, Garage Light, openings).
    White exposes dry-contact / range sensing and typically no Security+ lock.
    """
    ids = list(entity_ids)
    domains_names = [_entity_parts(e) for e in ids]

    has_synced = any(d == 'binary_sensor' and 'sync' in n for d, n in domains_names)
    has_lock = any(d == 'lock' for d, n in domains_names)
    has_light = any(d == 'light' for d, n in domains_names)
    has_openings = any(d == 'sensor' and 'opening' in n for d, n in domains_names)
    has_wired = any(
        d == 'binary_sensor' and ('wired' in n or 'garage door input' in n)
        for d, n in domains_names
    )
    has_range = any(
        d == 'sensor' and ('range' in n or 'distance' in n)
        for d, n in domains_names
    )

    # Prefer explicit White signals first (user may add MQTT/custom entities)
    if has_wired or has_range:
        if not (has_synced or has_lock):
            return DeviceType.WHITE

    if has_synced or has_lock or (has_light and has_openings):
        return DeviceType.BLAQ

    if has_light or has_lock:
        return DeviceType.BLAQ

    return DeviceType.UNKNOWN


def semantic_entity_map(entity_ids: Iterable[str]) -> Dict[str, str]:
    """Map semantic keys → entity_id for the first matching entity.

    Matching is by domain + name substring so custom renames still work when
    the name still contains the expected keyword (e.g. 'Garage Door').
    """
    rules = (
        (SEM_DOOR, 'cover', ('garage', 'door', '')),
        (SEM_LIGHT, 'light', ('light', 'garage')),
        (SEM_LOCK, 'lock', ('lock', '')),
        (SEM_MOTION, 'binary_sensor', ('motion',)),
        (SEM_OBSTRUCTION, 'binary_sensor', ('obstruction',)),
        (SEM_SYNCED, 'binary_sensor', ('sync',)),
        (SEM_MOTOR, 'binary_sensor', ('motor',)),
        (SEM_WALL_BUTTON, 'binary_sensor', ('wall', 'button')),
        (SEM_OPENINGS, 'sensor', ('opening',)),
        (SEM_DEVICE_ID, 'text_sensor', ('device id', 'device_id')),
        (SEM_WIFI, 'sensor', ('wifi signal', 'wifi_signal')),
        (SEM_LEARN, 'switch', ('learn',)),
        (SEM_RESYNC, 'button', ('re-sync', 'resync', 're sync')),
        (SEM_WIRED_SENSOR, 'binary_sensor', ('wired',)),
        (SEM_RANGE, 'sensor', ('range', 'distance')),
    )

    result: Dict[str, str] = {}
    parsed = [(_entity_parts(e), e) for e in entity_ids]

    for sem, domain, needles in rules:
        if sem in result:
            continue
        for (d, name), entity_id in parsed:
            if d != domain:
                continue
            # empty needle = first entity of that domain
            if needles == ('',) or any(n == '' or n in name for n in needles):
                # For cover, prefer names that look like a door when multiple exist
                if sem == SEM_DOOR and needles != ('',):
                    if not any(n in name for n in ('garage', 'door') if n):
                        # accept any cover if no better match later
                        pass
                result[sem] = entity_id
                break

    # Second pass for cover: if none matched keywords, take first cover
    if SEM_DOOR not in result:
        for (d, _name), entity_id in parsed:
            if d == 'cover':
                result[SEM_DOOR] = entity_id
                break

    return result


def device_type_index(device_type: DeviceType) -> int:
    from const import IX_DEVTYPE_BLAQ, IX_DEVTYPE_UNKNOWN, IX_DEVTYPE_WHITE

    return {
        DeviceType.BLAQ: IX_DEVTYPE_BLAQ,
        DeviceType.WHITE: IX_DEVTYPE_WHITE,
        DeviceType.UNKNOWN: IX_DEVTYPE_UNKNOWN,
    }.get(device_type, IX_DEVTYPE_UNKNOWN)


def parse_door_state(event: dict) -> Optional[int]:
    """Map cover SSE/REST payload to IoX door index."""
    from const import (
        IX_DOOR_CLOSED,
        IX_DOOR_CLOSING,
        IX_DOOR_OPEN,
        IX_DOOR_OPENING,
        IX_DOOR_UNKNOWN,
    )

    op = str(event.get('current_operation') or '').upper()
    if op == 'OPENING':
        return IX_DOOR_OPENING
    if op == 'CLOSING':
        return IX_DOOR_CLOSING

    state = str(event.get('state') or '').upper()
    if state == 'OPEN':
        return IX_DOOR_OPEN
    if state == 'CLOSED':
        return IX_DOOR_CLOSED
    if state in ('STOPPED', 'STOP'):
        from const import IX_DOOR_STOPPED
        return IX_DOOR_STOPPED
    return IX_DOOR_UNKNOWN


def parse_on_off_bool(event: dict) -> Optional[bool]:
    """Return True/False for binary_sensor / light style payloads."""
    if 'value' in event and isinstance(event['value'], bool):
        return event['value']
    state = str(event.get('state') or '').upper()
    if state in ('ON', 'DETECTED'):
        return True
    if state in ('OFF', 'CLEAR'):
        return False
    return None


def parse_lock_locked(event: dict) -> Optional[bool]:
    state = str(event.get('state') or '').upper()
    if state == 'LOCKED':
        return True
    if state == 'UNLOCKED':
        return False
    if 'value' in event and isinstance(event['value'], bool):
        return event['value']
    return None
