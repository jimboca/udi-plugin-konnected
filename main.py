#!/usr/bin/env python3
"""
Konnected Node Server for Polyglot v3.
Local REST/SSE control of Konnected GDO blaQ (White planned).
"""

import sys
from pathlib import Path

import markdown2
from udi_interface import Interface, LOGGER

from konnected_client.mqtt_health import log_mqtt_health
from nodes import VERSION, Controller, GarageDoor, Light

PLUGIN_DIR = Path(__file__).resolve().parent


def load_config_doc(polyglot):
    cfg_md = PLUGIN_DIR / 'CONFIG.md'
    if not cfg_md.is_file():
        return
    try:
        polyglot.setCustomParamsDoc(
            markdown2.markdown_path(
                str(cfg_md),
                extras=['tables', 'fenced-code-blocks'],
            )
        )
    except Exception:
        LOGGER.exception('Failed to convert/set CONFIG.md as custom params doc')


if __name__ == '__main__':
    try:
        # Diagnose MQTT certs before Interface connects — failures look like
        # "device offline" but are PG3 MQTT/TLS, not Konnected LAN issues.
        log_mqtt_health(LOGGER, PLUGIN_DIR)
        polyglot = Interface([Controller, GarageDoor, Light])
        polyglot.start(VERSION)
        load_config_doc(polyglot)
        polyglot.updateProfile()
        Controller(polyglot, 'controller', 'controller', 'Konnected Controller')
        polyglot.runForever()
    except (KeyboardInterrupt, SystemExit):
        LOGGER.warning('Received interrupt or exit...')
        polyglot.stop()
    except Exception:
        LOGGER.exception('Konnected NodeServer failed')
        sys.exit(1)
    sys.exit(0)
