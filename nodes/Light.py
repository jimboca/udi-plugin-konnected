"""Garage light child node for Konnected GDO devices that expose a light entity."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

from udi_interface import LOGGER, Node

from const import (
    ISY_FALSE,
    ISY_OFF,
    ISY_ON,
    ISY_ONOFF_UNKNOWN,
    ISY_TRUE,
    OFFLINE_STALE_SHORTPOLLS,
    UOM_BOOLEAN,
    UOM_ONOFF,
)
from konnected_client.models import SEM_LIGHT, parse_on_off_bool

if TYPE_CHECKING:
    from konnected_client.device import KonnectedDevice
    from nodes.Controller import Controller
    from nodes.GarageDoor import GarageDoor


class Light(Node):
    id = 'gdolt'
    hint = 0x01021000  # Residential / Controller / Non-Dimming Light

    def __init__(
        self,
        controller: 'Controller',
        primary: 'GarageDoor',
        address: str,
        name: str,
        host: str,
    ):
        super().__init__(controller.poly, primary.address, address, name)
        self.controller = controller
        self.host = host
        self._cmd_local = False
        self._offline_polls = 0
        self._stale_unknown = False
        self.poly.subscribe(self.poly.START, self.start, address)

    def start(self):
        LOGGER.info('Starting Light %s (%s)', self.address, self.host)
        device = self.controller.get_device(self.host)
        self.setDriver(
            'GV0',
            ISY_TRUE if (device and device.online) else ISY_FALSE,
            uom=UOM_BOOLEAN,
            force=True,
        )
        # Initial SSE burst often arrives before this node exists — REST-query now.
        if device and device.online:
            self.query()

    def on_device_event(self, key: str, event: dict) -> None:
        if key == '_online':
            online = bool(event.get('online'))
            self.setDriver('GV0', ISY_TRUE if online else ISY_FALSE, uom=UOM_BOOLEAN)
            if online:
                self._offline_polls = 0
                self._stale_unknown = False
                if self.controller.get_device(self.host):
                    threading.Thread(
                        target=self.query,
                        name=f'konnected-query-{self.address}',
                        daemon=True,
                    ).start()
            else:
                # Keep last-known Light State until shortPoll says stale.
                self._offline_polls = 0
            return

        if key != SEM_LIGHT:
            return

        try:
            old = int(self.getDriver('ST'))
        except (TypeError, ValueError):
            old = ISY_ONOFF_UNKNOWN
        on = parse_on_off_bool(event)
        if on is True:
            new = ISY_ON
        elif on is False:
            new = ISY_OFF
        else:
            new = ISY_ONOFF_UNKNOWN
        self.setDriver('ST', new, uom=UOM_ONOFF)

        if not self._cmd_local:
            if new == ISY_ON and old == ISY_OFF:
                self.reportCmd('DON')
            elif new == ISY_OFF and old == ISY_ON:
                self.reportCmd('DOF')
        else:
            self._cmd_local = False

    def check_offline_stale(self) -> None:
        """After N consecutive shortPolls offline, mark Light State Unknown."""
        device = self._device()
        if device and device.online:
            self._offline_polls = 0
            self._stale_unknown = False
            return
        self._offline_polls += 1
        if self._offline_polls < OFFLINE_STALE_SHORTPOLLS or self._stale_unknown:
            return
        LOGGER.info(
            'Light %s offline for %d shortPoll(s) — marking Light State Unknown',
            self.address,
            self._offline_polls,
        )
        self.setDriver('ST', ISY_ONOFF_UNKNOWN, uom=UOM_ONOFF)
        self._stale_unknown = True

    def _device(self) -> Optional['KonnectedDevice']:
        return self.controller.get_device(self.host)

    def cmd_on(self, command=None):
        device = self._device()
        if not device or not device.online:
            self.controller.notice_device(self.host, 'Device offline — cannot turn light on')
            return
        LOGGER.info('Light ON %s', self.address)
        if device.turn_on_light():
            self._cmd_local = True
            self.controller._refresh_device_notice(self.host, device)
        else:
            self.controller.notice_device(self.host, device.last_error or 'Light on failed')

    def cmd_off(self, command=None):
        device = self._device()
        if not device or not device.online:
            self.controller.notice_device(self.host, 'Device offline — cannot turn light off')
            return
        LOGGER.info('Light OFF %s', self.address)
        if device.turn_off_light():
            self._cmd_local = True
            self.controller._refresh_device_notice(self.host, device)
        else:
            self.controller.notice_device(self.host, device.last_error or 'Light off failed')

    def query(self, command=None):
        device = self._device()
        if device and device.online:
            state = device.get_state(SEM_LIGHT)
            if state:
                self.on_device_event(SEM_LIGHT, state)
        self.reportDrivers()

    drivers = [
        {'driver': 'ST', 'value': ISY_ONOFF_UNKNOWN, 'uom': UOM_ONOFF, 'name': 'Light State'},
        {'driver': 'GV0', 'value': ISY_FALSE, 'uom': UOM_BOOLEAN, 'name': 'Online'},
    ]
    commands = {
        'DON': cmd_on,
        'DFON': cmd_on,
        'DOF': cmd_off,
        'DFOF': cmd_off,
        'QUERY': query,
    }
