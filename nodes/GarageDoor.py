"""Garage door opener node for Konnected GDO blaQ (and future White)."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Optional

from udi_interface import LOGGER, Node

from const import (
    ISY_FALSE,
    ISY_TRUE,
    IX_ACTIVE,
    IX_CLEAR,
    IX_DOOR_UNKNOWN,
    IX_LOCK_LOCKED,
    IX_LOCK_UNLOCKED,
    IX_MOTION_CLEAR,
    IX_MOTION_DETECTED,
    IX_SYNCED_NO,
    IX_SYNCED_YES,
    IX_UNKNOWN,
    MOTION_EVENT_MASK_TIME,
    MOTION_STATE_RESET_TIME,
    OFFLINE_STALE_SHORTPOLLS,
    UOM_BOOLEAN,
    UOM_INDEX,
)
from konnected_client.models import (
    SEM_DOOR,
    SEM_LOCK,
    SEM_MOTION,
    SEM_OBSTRUCTION,
    SEM_SYNCED,
    device_type_index,
    parse_door_state,
    parse_lock_locked,
    parse_on_off_bool,
)

if TYPE_CHECKING:
    from konnected_client.device import KonnectedDevice
    from nodes.Controller import Controller


class GarageDoor(Node):
    id = 'gdo'
    hint = 0x01120100  # Residential / Barrier / Garage Door Opener

    def __init__(self, controller: 'Controller', address: str, name: str, host: str):
        # Self-primary so a Garage Light child can attach. PG3 rejects children
        # whose primaryNode is a non-primary leaf under the controller.
        super().__init__(controller.poly, address, address, name)
        self.controller = controller
        self.host = host
        self._cmd_local = False
        self._start_time = time.time()
        self._last_motion = 0.0
        self._offline_polls = 0
        self._stale_unknown = False
        self.poly.subscribe(self.poly.START, self.start, address)

    def start(self):
        LOGGER.info('Starting GarageDoor %s (%s)', self.address, self.host)
        device = self.controller.get_device(self.host)
        if device is None:
            LOGGER.warning('No device client for %s yet', self.host)
            self.setDriver('GV0', ISY_FALSE, uom=UOM_BOOLEAN, force=True)
            return
        self._apply_device_meta(device)
        self.setDriver('GV0', ISY_TRUE if device.online else ISY_FALSE, uom=UOM_BOOLEAN, force=True)
        # Initial SSE burst often arrives before this node exists — REST-query now.
        self.query()

    def _apply_device_meta(self, device: 'KonnectedDevice') -> None:
        self.setDriver('GV5', device_type_index(device.device_type), uom=UOM_INDEX, force=True)

    def on_device_event(self, key: str, event: dict) -> None:
        """Called from controller when SSE delivers an update for this host."""
        if key == '_online':
            online = bool(event.get('online'))
            self.setDriver('GV0', ISY_TRUE if online else ISY_FALSE, uom=UOM_BOOLEAN)
            if online:
                self._offline_polls = 0
                self._stale_unknown = False
                device = self.controller.get_device(self.host)
                if device:
                    self._apply_device_meta(device)
                    # Never REST-query on the SSE thread — blocks event reads.
                    threading.Thread(
                        target=self.query,
                        name=f'konnected-query-{self.address}',
                        daemon=True,
                    ).start()
            else:
                # Keep last-known Door State / sensors until shortPoll says stale.
                self._offline_polls = 0
            return

        if key == SEM_DOOR:
            try:
                old = int(self.getDriver('ST'))
            except (TypeError, ValueError):
                old = IX_DOOR_UNKNOWN
            new = parse_door_state(event)
            if new is None:
                new = IX_DOOR_UNKNOWN
            self.setDriver('ST', new, uom=UOM_INDEX)
            if not self._cmd_local:
                # External change → DON/DOF for programs
                if new in (1, 4) and old == 0:  # open/opening from closed
                    self.reportCmd('DON')
                elif new == 0 and old in (1, 2, 3, 4):
                    self.reportCmd('DOF')
            else:
                self._cmd_local = False
            return

        if key == SEM_OBSTRUCTION:
            active = parse_on_off_bool(event)
            if active is None:
                self.setDriver('GV1', IX_UNKNOWN, uom=UOM_INDEX)
            else:
                self.setDriver('GV1', IX_ACTIVE if active else IX_CLEAR, uom=UOM_INDEX)
            return

        if key == SEM_LOCK:
            locked = parse_lock_locked(event)
            if locked is None:
                self.setDriver('GV2', IX_UNKNOWN, uom=UOM_INDEX)
            else:
                self.setDriver('GV2', IX_LOCK_LOCKED if locked else IX_LOCK_UNLOCKED, uom=UOM_INDEX)
            return

        if key == SEM_MOTION:
            active = parse_on_off_bool(event)
            if active is True:
                if time.time() > self._start_time + MOTION_EVENT_MASK_TIME:
                    self._last_motion = time.time()
                    self.setDriver('GV3', IX_MOTION_DETECTED, uom=UOM_INDEX)
                    LOGGER.info('Motion detected on %s — DON3', self.address)
                    self.reportCmd('DON3')
                else:
                    self.setDriver('GV3', IX_MOTION_CLEAR, uom=UOM_INDEX)
            elif active is False:
                self.setDriver('GV3', IX_MOTION_CLEAR, uom=UOM_INDEX)
                self._last_motion = 0.0
            else:
                self.setDriver('GV3', IX_UNKNOWN, uom=UOM_INDEX)
            return

        if key == SEM_SYNCED:
            active = parse_on_off_bool(event)
            if active is None:
                self.setDriver('GV4', IX_UNKNOWN, uom=UOM_INDEX)
            else:
                self.setDriver('GV4', IX_SYNCED_YES if active else IX_SYNCED_NO, uom=UOM_INDEX)
            return

    def reset_motion_if_stale(self) -> None:
        if self._last_motion and time.time() > self._last_motion + MOTION_STATE_RESET_TIME:
            LOGGER.info('Clearing stale motion on %s', self.address)
            self.setDriver('GV3', IX_MOTION_CLEAR, uom=UOM_INDEX)
            self._last_motion = 0.0

    def check_offline_stale(self) -> None:
        """After N consecutive shortPolls offline, mark drivers Unknown.

        N = OFFLINE_STALE_SHORTPOLLS so the delay tracks the user-set PG3
        shortPoll interval (default 30s → ~60s before Unknown).
        """
        device = self._device()
        if device and device.online:
            self._offline_polls = 0
            self._stale_unknown = False
            return
        self._offline_polls += 1
        if self._offline_polls < OFFLINE_STALE_SHORTPOLLS or self._stale_unknown:
            return
        LOGGER.info(
            'GDO %s offline for %d shortPoll(s) — marking Door State/sensors Unknown',
            self.address,
            self._offline_polls,
        )
        self.setDriver('ST', IX_DOOR_UNKNOWN, uom=UOM_INDEX)
        self.setDriver('GV1', IX_UNKNOWN, uom=UOM_INDEX)
        self.setDriver('GV2', IX_UNKNOWN, uom=UOM_INDEX)
        self.setDriver('GV3', IX_UNKNOWN, uom=UOM_INDEX)
        self.setDriver('GV4', IX_UNKNOWN, uom=UOM_INDEX)
        self._stale_unknown = True

    def _device(self) -> Optional['KonnectedDevice']:
        return self.controller.get_device(self.host)

    def _cmd_fail(self, message: str) -> None:
        self.controller.notice_device(self.host, message)

    def _cmd_ok(self) -> None:
        self.controller._refresh_device_notice(self.host, self._device())

    def cmd_open(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot open')
            return
        LOGGER.info('Open %s', self.address)
        if device.open_door():
            self._cmd_local = True
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Open failed')

    def cmd_close(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot close')
            return
        LOGGER.info('Close %s', self.address)
        if device.close_door():
            self._cmd_local = True
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Close failed')

    def cmd_stop(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot stop')
            return
        LOGGER.info('Stop %s', self.address)
        if device.stop_door():
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Stop failed')

    def cmd_lock(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot lock')
            return
        if not device.has_lock:
            self._cmd_fail('Lock not available on this device')
            return
        LOGGER.info('Lock remotes %s', self.address)
        if device.lock():
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Lock failed')

    def cmd_unlock(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot unlock')
            return
        if not device.has_lock:
            self._cmd_fail('Lock not available on this device')
            return
        LOGGER.info('Unlock remotes %s', self.address)
        if device.unlock():
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Unlock failed')

    def cmd_resync(self, command=None):
        device = self._device()
        if not device or not device.online:
            self._cmd_fail('Device offline — cannot re-sync')
            return
        LOGGER.info('Re-sync %s', self.address)
        if device.resync():
            self._cmd_ok()
        else:
            self._cmd_fail(device.last_error or 'Re-sync failed')

    def query(self, command=None):
        device = self._device()
        if device and device.online:
            for sem in (SEM_DOOR, SEM_OBSTRUCTION, SEM_LOCK, SEM_MOTION, SEM_SYNCED):
                state = device.get_state(sem)
                if state:
                    self.on_device_event(sem, state)
        self.reportDrivers()

    drivers = [
        {'driver': 'ST', 'value': IX_DOOR_UNKNOWN, 'uom': UOM_INDEX, 'name': 'Door State'},
        {'driver': 'GV0', 'value': ISY_FALSE, 'uom': UOM_BOOLEAN, 'name': 'Online'},
        {'driver': 'GV1', 'value': IX_UNKNOWN, 'uom': UOM_INDEX, 'name': 'Obstruction'},
        {'driver': 'GV2', 'value': IX_UNKNOWN, 'uom': UOM_INDEX, 'name': 'Lockout'},
        {'driver': 'GV3', 'value': IX_UNKNOWN, 'uom': UOM_INDEX, 'name': 'Motion'},
        {'driver': 'GV4', 'value': IX_UNKNOWN, 'uom': UOM_INDEX, 'name': 'Synced'},
        {'driver': 'GV5', 'value': 0, 'uom': UOM_INDEX, 'name': 'Device Type'},
    ]
    commands = {
        'DON': cmd_open,
        'DOF': cmd_close,
        'STOP': cmd_stop,
        'LOCK': cmd_lock,
        'UNLOCK': cmd_unlock,
        'RESYNC': cmd_resync,
        'QUERY': query,
    }
