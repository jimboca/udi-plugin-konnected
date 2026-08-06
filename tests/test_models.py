"""Unit tests for Konnected entity mapping (no live device required)."""

from konnected_client.models import (
    DeviceType,
    SEM_DOOR,
    SEM_LIGHT,
    SEM_LOCK,
    SEM_MOTION,
    SEM_OBSTRUCTION,
    SEM_SYNCED,
    classify_device,
    parse_door_state,
    parse_lock_locked,
    parse_on_off_bool,
    semantic_entity_map,
)
from konnected_client.device import KonnectedDevice
from const import IX_DOOR_CLOSED, IX_DOOR_CLOSING, IX_DOOR_OPEN, IX_DOOR_OPENING


BLAQ_ENTITIES = [
    'cover/Garage Door',
    'light/Garage Light',
    'lock/Lock',
    'binary_sensor/Motion',
    'binary_sensor/Obstruction',
    'binary_sensor/Synced',
    'binary_sensor/Motor',
    'sensor/Garage Openings',
    'text_sensor/Device ID',
]

WHITE_ENTITIES = [
    'cover/Garage Door',
    'binary_sensor/Wired Sensor',
    'sensor/Sensor distance',
]


def test_classify_blaq():
    assert classify_device(BLAQ_ENTITIES) == DeviceType.BLAQ


def test_classify_white():
    assert classify_device(WHITE_ENTITIES) == DeviceType.WHITE


def test_semantic_map_blaq():
    m = semantic_entity_map(BLAQ_ENTITIES)
    assert m[SEM_DOOR] == 'cover/Garage Door'
    assert m[SEM_LIGHT] == 'light/Garage Light'
    assert m[SEM_LOCK] == 'lock/Lock'
    assert m[SEM_MOTION] == 'binary_sensor/Motion'
    assert m[SEM_OBSTRUCTION] == 'binary_sensor/Obstruction'
    assert m[SEM_SYNCED] == 'binary_sensor/Synced'


def test_parse_door_states():
    assert parse_door_state({'state': 'CLOSED', 'current_operation': 'IDLE'}) == IX_DOOR_CLOSED
    assert parse_door_state({'state': 'OPEN', 'current_operation': 'IDLE'}) == IX_DOOR_OPEN
    assert parse_door_state({'state': 'OPEN', 'current_operation': 'OPENING'}) == IX_DOOR_OPENING
    assert parse_door_state({'state': 'CLOSED', 'current_operation': 'CLOSING'}) == IX_DOOR_CLOSING


def test_parse_bool_and_lock():
    assert parse_on_off_bool({'state': 'ON', 'value': True}) is True
    assert parse_on_off_bool({'state': 'OFF'}) is False
    assert parse_lock_locked({'state': 'LOCKED'}) is True
    assert parse_lock_locked({'state': 'UNLOCKED'}) is False


def test_rest_path_new_and_legacy():
    assert KonnectedDevice._id_to_rest_path('cover/Garage Door') == '/cover/Garage%20Door'
    assert KonnectedDevice._id_to_rest_path('binary_sensor/Obstruction') == '/binary_sensor/Obstruction'
    assert KonnectedDevice._id_to_rest_path('binary-sensor-motion') == '/binary_sensor/motion'
    assert KonnectedDevice._id_to_rest_path('cover-garage_door') == '/cover/garage_door'


def test_host_address_stable():
    from nodes.Controller import host_to_address

    a = host_to_address('192.168.1.50')
    b = host_to_address('192.168.1.50')
    assert a == b
    assert len(a) <= 14
    assert a.startswith('gdo')


def test_mdns_gdo_project_filter():
    from konnected_client.mdns import is_garage_door_project

    assert is_garage_door_project('konnected.garage-door-gdov2-q')
    assert is_garage_door_project('konnected.garage-door-gdov2-s')
    assert not is_garage_door_project('')  # unknown → unsupported notice path
    assert not is_garage_door_project('konnected.alarm-panel-pro-wifi')
    assert not is_garage_door_project('konnected.alarm-panel-esp8266')
