"""Unit tests for Controller notice helpers (no live device / PG3 required)."""

from __future__ import annotations

from const import NOTICE_DEVICE_PREFIX, NOTICE_UNKNOWN_PREFIX
from nodes.Controller import Controller, host_to_address


class _FakeNotices(dict):
    def delete(self, key):
        self.pop(key, None)


class _Bare:
    """Minimal stand-in so we can call notice helpers without full Controller init."""

    pass


def _ctl():
    c = _Bare()
    c.Notices = _FakeNotices()
    # Bind methods from Controller
    c._notice_set = Controller._notice_set.__get__(c)
    c._notice_clear = Controller._notice_clear.__get__(c)
    c._notice_clear_prefix = Controller._notice_clear_prefix.__get__(c)
    c.notice_device = Controller.notice_device.__get__(c)
    c._unknown_notice_key = staticmethod(Controller._unknown_notice_key)
    c._publish_unknown_devices = Controller._publish_unknown_devices.__get__(c)
    c._refresh_device_notice = Controller._refresh_device_notice.__get__(c)
    return c


def test_notice_device_set_and_clear():
    c = _ctl()
    c.notice_device('192.168.1.18', 'Offline')
    key = NOTICE_DEVICE_PREFIX + host_to_address('192.168.1.18')
    assert key in c.Notices
    assert 'Offline' in c.Notices[key]
    c.notice_device('192.168.1.18', '')
    assert key not in c.Notices


def test_unknown_devices_notice_and_clear_stale():
    c = _ctl()
    panel = {
        'host': '192.168.1.50',
        'mac': 'aabbccddeeff',
        'friendly_name': 'Alarm Panel',
        'project_name': 'konnected.alarm-panel-pro-wifi',
    }
    gdo_unknown = {
        'host': '192.168.1.51',
        'mac': '112233445566',
        'friendly_name': 'Mystery',
        'project_name': '',
    }
    c._publish_unknown_devices([panel, gdo_unknown])
    k1 = NOTICE_UNKNOWN_PREFIX + 'aabbccddeeff'
    k2 = NOTICE_UNKNOWN_PREFIX + '112233445566'
    assert k1 in c.Notices
    assert 'Unsupported Konnected' in c.Notices[k1]
    assert 'alarm-panel' in c.Notices[k1]
    assert k2 in c.Notices

    # Second discover: only panel remains → mystery notice cleared
    c._publish_unknown_devices([panel])
    assert k1 in c.Notices
    assert k2 not in c.Notices

    # No unsupported devices → all unknown_* cleared
    c._publish_unknown_devices([])
    assert k1 not in c.Notices


def test_refresh_device_notice_healthy_clears():
    class _Dev:
        online = True
        last_error = None
        device_type = type('T', (), {'value': 'blaq'})()

        def semantic_entity(self, sem):
            return 'cover/Garage Door' if sem == 'door' else None

    # Patch DeviceType comparison used in _refresh_device_notice
    from konnected_client import DeviceType

    _Dev.device_type = DeviceType.BLAQ

    c = _ctl()
    c.notice_device('192.168.1.18', 'Offline / reconnecting')
    c._refresh_device_notice('192.168.1.18', _Dev())
    key = NOTICE_DEVICE_PREFIX + host_to_address('192.168.1.18')
    assert key not in c.Notices


def test_refresh_device_notice_no_cover():
    class _Dev:
        online = True
        last_error = None
        device_type = None

        def semantic_entity(self, sem):
            return None

    from konnected_client import DeviceType

    _Dev.device_type = DeviceType.UNKNOWN

    c = _ctl()
    c._refresh_device_notice('192.168.1.99', _Dev())
    key = NOTICE_DEVICE_PREFIX + host_to_address('192.168.1.99')
    assert key in c.Notices
    assert 'no garage door cover' in c.Notices[key]
