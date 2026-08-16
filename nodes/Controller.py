"""Controller node for udi-plugin-konnected."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

import markdown2
from udi_interface import LOGGER, LOG_HANDLER, Custom, Node

from const import (
    DEFAULT_MDNS_SECONDS,
    ISY_FALSE,
    ISY_TRUE,
    NOTICE_DEVICE_PREFIX,
    NOTICE_DISCOVER,
    NOTICE_HOSTS,
    NOTICE_MDNS,
    NOTICE_MQTT,
    NOTICE_OFFLINE_GRACE_SEC,
    NOTICE_UNKNOWN_PREFIX,
    NS_DEVICE_TYPE,
    NS_FRIENDLY_NAME,
    NS_HAS_LIGHT,
    NS_HOST,
    PARAM_CHANGE_NODE_NAMES,
    PARAM_HOSTS,
    UOM_BOOLEAN,
    UOM_RAW,
)
from konnected_client import DeviceType, KonnectedDevice, browse_konnected_devices
from konnected_client import stream_debug
from konnected_client.mqtt_health import (
    NOTICE_MQTT_CERT,
    NOTICE_MQTT_FLAPPING,
    check_mqtt_client_cert,
)
from nodes.GarageDoor import GarageDoor
from nodes.Light import Light

# MQTT watchdog: publish a PG3 notice on brief reconnects; log while down.
_MQTT_WATCH_INTERVAL_SEC = 2.0
_MQTT_FLAP_NOTICE_AFTER = 2  # disconnects before flapping notice
_MQTT_STABLE_CLEAR_SEC = 45.0  # clear mqtt notice after this much healthy time
_MQTT_DOWN_LOG_EVERY_SEC = 30.0


def _normalize_host(raw: str) -> str:
    host = raw.strip()
    if host.startswith('http://'):
        host = host[len('http://'):]
    elif host.startswith('https://'):
        host = host[len('https://'):]
    return host.rstrip('/')


def _parse_hosts(value) -> List[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r'[\s,;]+', text)
    out = []
    for p in parts:
        h = _normalize_host(p)
        if h and h not in out:
            out.append(h)
    return out


def host_to_address(host: str) -> str:
    """Stable IoX node address from host (max 14 chars)."""
    digest = hashlib.sha1(host.encode('utf-8')).hexdigest()[:10]
    return f'gdo{digest}'


def light_address_for(gdo_addr: str) -> str:
    """Child light address derived from GDO address (max 14 chars)."""
    # gdo + 10 hex = 13; keep first 11 + 'lt' → 13
    return gdo_addr[:11] + 'lt'


# Auto-generated names from early plugin builds — safe to replace with device name.
_AUTO_NODE_NAME_RE = re.compile(
    r'^Konnected\s+(Blaq|White|GDO)\s+',
    re.IGNORECASE,
)


def _sanitize_node_name(name: str) -> str:
    """Trim and clamp a device/IoX display name."""
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', (name or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned)
    # Strip .local hostname suffix when mDNS falls back to hostname
    if cleaned.lower().endswith('.local'):
        cleaned = cleaned[:-6]
    return cleaned[:64]


class Controller(Node):
    id = 'controller'
    hint = 0x01090000

    def __init__(self, poly, primary, address, name):
        super().__init__(poly, primary, address, name)
        self.poly = poly
        self.Notices = poly.Notices
        self.Params = Custom(poly, 'customparams')
        self.Data = Custom(poly, 'customdata')
        self.NsData = Custom(poly, 'customns')

        self.hosts: List[str] = []
        # host → user-assigned device name (mDNS friendly_name / saved NsData)
        self.host_names: Dict[str, str] = {}
        self.change_node_names = False
        self.devices: Dict[str, KonnectedDevice] = {}
        self.gdo_nodes: Dict[str, GarageDoor] = {}
        self.light_nodes: Dict[str, Light] = {}
        self._params_ready = False
        self._started = False
        self._suppress_param_side_effects = False
        self.hb = 0
        # Serialize addNode: PG3 rejects child nodes until the parent ACK lands.
        self.n_queue: List[str] = []
        self.add_node_timeout = 30.0
        self._discover_lock = threading.Lock()
        self._discover_thread: Optional[threading.Thread] = None
        self._stopping = False
        self._mqtt_watch_thread: Optional[threading.Thread] = None
        self._mqtt_notice_active = False
        self._mqtt_cert_ok, self._mqtt_cert_detail = check_mqtt_client_cert()
        # host → time.time() when API first went offline (for notice grace).
        self._device_offline_since: Dict[str, float] = {}
        # host → True after we force-published a clear following reconnect.
        self._device_notice_force_cleared: Dict[str, bool] = {}

        poly.subscribe(poly.START, self.handler_start, address)
        poly.subscribe(poly.STOP, self.handler_stop)
        poly.subscribe(poly.POLL, self.handler_poll)
        # DISCOVER must not run on the Command thread — wait_for_node_done would
        # deadlock that thread (it is also the MQTT input processor).
        poly.subscribe(poly.DISCOVER, self.discover_async)
        poly.subscribe(poly.CUSTOMPARAMS, self.handler_params)
        poly.subscribe(poly.CUSTOMDATA, self.handler_data)
        poly.subscribe(poly.CUSTOMNS, self.handler_nsdata)
        poly.subscribe(poly.CONFIGDONE, self.handler_config_done)
        poly.subscribe(poly.LOGLEVEL, self.handler_log_level)
        poly.subscribe(poly.ADDNODEDONE, self.handler_addnode_done)

        poly.ready()
        # Do not use conn_status='ST' — PG3 reports that as UOM 25 (raw 0/1/2).
        # Node Server Online is a boolean (UOM 2) True/False driver we own.
        poly.addNode(self)
        self._start_mqtt_watchdog()

    # ── handlers ───────────────────────────────────────────────────────────

    def handler_start(self):
        LOGGER.info('Konnected controller starting')
        # Drop controller ADDNODEDONE so the first GDO wait is not a false match.
        self.n_queue.clear()
        self.setDriver('ST', ISY_TRUE, uom=UOM_BOOLEAN, force=True, report=True)
        self.setDriver('GV0', 0, uom=UOM_RAW, force=True)
        self.setDriver('GV1', 0, uom=UOM_RAW, force=True)
        self._load_config_doc()
        # Wait briefly for params
        for _ in range(15):
            if self._params_ready:
                break
            time.sleep(0.2)
        self._started = True
        self.sync_notices()
        # Full mDNS Discover on every start (same path as the Discover button)
        LOGGER.info('Running Discover on startup…')
        self.discover()
        self._rehydrate_nodes()
        self.heartbeat()
        LOGGER.info('Konnected controller started')

    def handler_stop(self):
        LOGGER.info('Konnected controller stopping')
        self._stopping = True
        try:
            self.setDriver('ST', ISY_FALSE, uom=UOM_BOOLEAN, force=True, report=True)
        except Exception:
            LOGGER.debug('Could not clear ST on stop', exc_info=True)
        for device in list(self.devices.values()):
            try:
                device.stop()
            except Exception:
                LOGGER.exception('Error stopping device %s', device.host)
        self.devices.clear()

    def handler_poll(self, polltype):
        if polltype == 'longPoll':
            self.heartbeat()
            self._check_connections()
        elif polltype == 'shortPoll':
            # Re-assert healthy notices so a stale PG3 Notices.load echo cannot
            # leave "Connection lost — reconnecting…" up while the API is live.
            for host, device in list(self.devices.items()):
                if device.online:
                    self._refresh_device_notice(host, device)
            for node in self.gdo_nodes.values():
                node.reset_motion_if_stale()
                node.check_offline_stale()
            for node in self.light_nodes.values():
                node.check_offline_stale()

    def handler_params(self, params):
        LOGGER.debug('customparams: %s', params)
        self.Params.load(params)
        self.hosts = _parse_hosts(self.Params.get(PARAM_HOSTS))
        cnn = str(self.Params.get(PARAM_CHANGE_NODE_NAMES, 'false')).strip().lower()
        self.change_node_names = cnn in ('1', 'true', 'yes', 'on')
        self._params_ready = True
        if self._suppress_param_side_effects:
            # Echo from _persist_hosts during Discover — avoid re-entrancy
            self._suppress_param_side_effects = False
            self.sync_notices()
            return
        self.sync_notices()
        if self._started:
            # Manual hosts edit: connect without a full mDNS browse
            self._connect_all()
            self._ensure_nodes_for_online()

    def handler_data(self, data):
        LOGGER.debug('customdata: %s', data)
        if data is not None:
            self.Data.load(data)

    def handler_nsdata(self, key, data):
        # CUSTOMNS publishes (key, value) — e.g. getAll key='customns'.
        LOGGER.debug('customns: key=%s data=%s', key, data)
        if data is not None and isinstance(data, dict):
            self.NsData.load(data)
            self._load_host_names_from_nsdata()

    def _load_host_names_from_nsdata(self) -> None:
        """Seed host_names from previously saved friendly names."""
        for addr in list(self.NsData.keys()):
            meta = self.NsData.get(addr)
            if not isinstance(meta, dict) or meta.get('kind') != 'gdo':
                continue
            host = meta.get(NS_HOST)
            friendly = _sanitize_node_name(meta.get(NS_FRIENDLY_NAME) or '')
            if host and friendly and host not in self.host_names:
                self.host_names[host] = friendly

    def handler_config_done(self):
        LOGGER.debug('config done')
        self.poly.addLogLevel('DEBUG_STREAM', 9, 'Debug + API Stream')

    def handler_log_level(self, level):
        """PG3 Logger Level — Debug + API Stream dumps native entity states."""
        LOGGER.info('log level: %s', level)
        name = ''
        lvl = 30
        if isinstance(level, dict):
            name = str(level.get('name') or '').upper()
            try:
                lvl = int(level.get('level', 30))
            except (TypeError, ValueError):
                lvl = 30
        # Custom levels use numbers below standard DEBUG (10).
        if lvl < 10:
            LOG_HANDLER.set_basic_config(True, logging.DEBUG)
        else:
            LOG_HANDLER.set_basic_config(True, logging.WARNING)
        stream_debug.set_enabled(name == 'DEBUG_STREAM' or lvl == 9)

    def handler_addnode_done(self, data):
        LOGGER.debug('addNode done: %s', data)
        addr = None
        if isinstance(data, dict):
            addr = data.get('address')
        else:
            addr = getattr(data, 'address', None)
        if addr is not None:
            self.n_queue.append(str(addr).lower())

    def wait_for_node_done(self, address=None, timeout_sec=None) -> bool:
        """Block until ADDNODEDONE for *address* (or any address if None)."""
        address = str(address or '').lower()
        if timeout_sec is None:
            timeout_sec = self.add_node_timeout
        deadline = time.time() + max(0.1, float(timeout_sec))
        while time.time() < deadline:
            if address:
                for i, queued in enumerate(self.n_queue):
                    if queued == address:
                        self.n_queue.pop(i)
                        return True
            elif self.n_queue:
                self.n_queue.pop(0)
                return True
            time.sleep(0.05)
        LOGGER.warning(
            'wait_for_node_done timed out after %ss for %s',
            timeout_sec,
            address or '<any>',
        )
        return False

    # ── notices / docs ─────────────────────────────────────────────────────

    def _notice_set(self, key: str, message: str) -> None:
        if message:
            self.Notices[key] = message
        else:
            self.Notices.delete(key)

    def _notice_clear(self, key: str) -> None:
        self.Notices.delete(key)

    def _notice_clear_prefix(self, prefix: str, keep: Optional[set] = None) -> None:
        """Delete notice keys starting with prefix, optionally keeping some keys."""
        keep = keep or set()
        for key in list(self.Notices.keys()):
            if isinstance(key, str) and key.startswith(prefix) and key not in keep:
                self.Notices.delete(key)

    def sync_notices(self):
        if not self.hosts:
            self._notice_set(
                NOTICE_HOSTS,
                'No devices configured. Click Discover to find Konnected garage '
                'door openers on the LAN (mDNS), or set custom parameter "hosts" '
                'to IP address(es).',
            )
        else:
            self._notice_clear(NOTICE_HOSTS)
        # Refresh MQTT cert status; set notice only while MQTT can publish.
        self._mqtt_cert_ok, self._mqtt_cert_detail = check_mqtt_client_cert()
        if not self._mqtt_cert_ok and self.poly.isConnected():
            self._publish_mqtt_notice(NOTICE_MQTT_CERT)

    def _start_mqtt_watchdog(self) -> None:
        if self._mqtt_watch_thread is not None and self._mqtt_watch_thread.is_alive():
            return
        self._mqtt_watch_thread = threading.Thread(
            target=self._mqtt_watchdog_loop,
            name='konnected-mqtt-watch',
            daemon=True,
        )
        self._mqtt_watch_thread.start()

    def _publish_mqtt_notice(self, message: str) -> None:
        """Set the mqtt notice when the PG3 link can accept it."""
        if not self.poly.isConnected():
            return
        try:
            self._notice_set(NOTICE_MQTT, message)
            self._mqtt_notice_active = True
        except Exception:
            LOGGER.debug('Could not publish MQTT notice', exc_info=True)

    def _clear_mqtt_notice(self) -> None:
        if not self._mqtt_notice_active:
            return
        if not self.poly.isConnected():
            return
        try:
            self._notice_clear(NOTICE_MQTT)
            self._mqtt_notice_active = False
        except Exception:
            LOGGER.debug('Could not clear MQTT notice', exc_info=True)

    def _mqtt_watchdog_loop(self) -> None:
        """Watch PG3 MQTT; log clearly and surface Notices on brief reconnects.

        Notices require MQTT, so while the link is down we can only log. On each
        reconnect we publish a notice if the client cert is bad or the link has
        been flapping — otherwise Discover/device failures look like GDO issues.
        """
        was_connected = False
        disconnect_count = 0
        down_since: Optional[float] = None
        up_since: Optional[float] = None
        last_down_log = 0.0

        while not self._stopping:
            try:
                connected = bool(self.poly.isConnected())
            except Exception:
                connected = False

            now = time.time()
            if connected and not was_connected:
                LOGGER.info(
                    'PG3 MQTT connected — checking whether to surface MQTT notice'
                )
                up_since = now
                down_since = None
                self._mqtt_cert_ok, self._mqtt_cert_detail = check_mqtt_client_cert()
                if not self._mqtt_cert_ok:
                    LOGGER.error(
                        'MQTT health FAILED (PG3 link, not a Konnected GDO issue): %s',
                        self._mqtt_cert_detail,
                    )
                    self._publish_mqtt_notice(NOTICE_MQTT_CERT)
                elif disconnect_count >= _MQTT_FLAP_NOTICE_AFTER:
                    LOGGER.error(
                        'PG3 MQTT was flapping (%d disconnects). This is an MQTT '
                        'issue between the Node Server and Polyglot — not a '
                        'Konnected garage-door LAN failure.',
                        disconnect_count,
                    )
                    self._publish_mqtt_notice(NOTICE_MQTT_FLAPPING)
                disconnect_count = 0
            elif not connected and was_connected:
                disconnect_count += 1
                down_since = now
                up_since = None
                LOGGER.error(
                    'PG3 MQTT disconnected (count=%d). Discover/device updates '
                    'pause until MQTT is stable. This is usually a TLS/cert or '
                    'broker problem — not the Konnected GDO itself. %s',
                    disconnect_count,
                    self._mqtt_cert_detail
                    if not self._mqtt_cert_ok
                    else 'Re-check .cert/.key against ud.ca.cert after UDX updates.',
                )
            elif not connected:
                if down_since is None:
                    down_since = now
                if now - last_down_log >= _MQTT_DOWN_LOG_EVERY_SEC:
                    last_down_log = now
                    LOGGER.error(
                        'PG3 MQTT still down for %.0fs — cannot reach Polyglot '
                        '(notices/Discover blocked). Not a Konnected device issue. %s',
                        now - down_since,
                        self._mqtt_cert_detail
                        if not self._mqtt_cert_ok
                        else '',
                    )
            else:
                # Connected and was connected — clear notice after stable period
                if (
                    self._mqtt_notice_active
                    and self._mqtt_cert_ok
                    and up_since is not None
                    and now - up_since >= _MQTT_STABLE_CLEAR_SEC
                    and self._started
                ):
                    LOGGER.info('PG3 MQTT stable — clearing mqtt notice')
                    self._clear_mqtt_notice()

            was_connected = connected
            time.sleep(_MQTT_WATCH_INTERVAL_SEC)

    def notice_device(self, host: str, message: str, force_publish: bool = False):
        """Per-device error notice; empty message clears it.

        force_publish: when clearing and the key is already absent locally,
        still push the notices document so PG3 cannot keep a stale entry after
        a Custom.load race re-introduced the key in memory-only or UI-only.
        """
        key = NOTICE_DEVICE_PREFIX + host_to_address(host)
        if message:
            self._notice_set(key, f'{host}: {message}')
            return
        if key in self.Notices:
            self._notice_clear(key)
        elif force_publish:
            try:
                self.Notices._save()
            except Exception:
                LOGGER.debug('Could not force-publish notices clear for %s', host, exc_info=True)

    @staticmethod
    def _unknown_notice_key(entry: dict) -> str:
        mac = (entry.get('mac') or '').strip()
        if mac:
            return NOTICE_UNKNOWN_PREFIX + mac[-12:]
        host = (entry.get('host') or entry.get('ip') or 'device').replace(':', '_')
        return NOTICE_UNKNOWN_PREFIX + host_to_address(host)

    def _publish_unknown_devices(self, others: List[dict]) -> None:
        """Notice unsupported Konnected mDNS hits; clear ones no longer seen."""
        keep = set()
        for entry in others:
            key = self._unknown_notice_key(entry)
            keep.add(key)
            name = entry.get('friendly_name') or entry.get('host') or 'device'
            host = entry.get('host') or entry.get('ip') or '?'
            project = entry.get('project_name') or '(no project_name)'
            self._notice_set(
                key,
                f'Unsupported Konnected device "{name}" at {host} '
                f'(project={project}). This plugin supports GDO blaQ / White only; '
                f'alarm panels and other Konnected products are ignored.',
            )
        self._notice_clear_prefix(NOTICE_UNKNOWN_PREFIX, keep=keep)

    @staticmethod
    def _friendly_offline_message(raw: Optional[str]) -> str:
        """Map noisy connection exceptions to a short reconnecting notice."""
        text = (raw or '').strip()
        low = text.lower()
        if not text:
            return 'Offline — reconnecting…'
        if any(
            s in low
            for s in (
                'connection reset',
                'connection broken',
                'connection aborted',
                'broken pipe',
                'timed out',
                'timeout',
                'remotely closed',
                'errno 54',
                'errno 32',
            )
        ):
            return 'Connection lost — reconnecting…'
        if text.startswith('(') and 'Connection' in text:
            return 'Connection lost — reconnecting…'
        return text

    def _refresh_device_notice(
        self,
        host: str,
        device: Optional[KonnectedDevice],
        force_clear_publish: bool = False,
    ) -> None:
        """Set or clear the per-host notice from current client state."""
        if device is None:
            self._device_offline_since.pop(host, None)
            self.notice_device(host, 'Not connected')
            return
        if not device.online:
            since = self._device_offline_since.setdefault(host, time.time())
            self._device_notice_force_cleared.pop(host, None)
            # Brief API drops reconnect in seconds — do not flash a Notice.
            if time.time() - since < NOTICE_OFFLINE_GRACE_SEC:
                self.notice_device(host, '')
                return
            self.notice_device(host, self._friendly_offline_message(device.last_error))
            return

        self._device_offline_since.pop(host, None)
        # API is back — drop any prior disconnect notice immediately.
        # Skip "no cover" until discovery finishes so we do not flash a false error.
        if not device.discovery_ready:
            do_force = force_clear_publish or not self._device_notice_force_cleared.get(host)
            self.notice_device(host, '', force_publish=do_force)
            if do_force:
                self._device_notice_force_cleared[host] = True
            return
        if device.semantic_entity('door') is None:
            self.notice_device(
                host,
                'Connected but no garage door cover entity found '
                '(unsupported or custom firmware?).',
            )
            return
        if device.device_type == DeviceType.UNKNOWN:
            self.notice_device(
                host,
                'Garage door found but device type is unknown — '
                'basic open/close may work; blaQ/White features may be missing.',
            )
            return
        # Healthy
        do_force = force_clear_publish or not self._device_notice_force_cleared.get(host)
        self.notice_device(host, '', force_publish=do_force)
        if do_force:
            self._device_notice_force_cleared[host] = True

    def _load_config_doc(self):
        cfg = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'CONFIG.md')
        if os.path.isfile(cfg):
            try:
                self.poly.setCustomParamsDoc(
                    markdown2.markdown_path(cfg, extras=['tables', 'fenced-code-blocks'])
                )
            except Exception:
                LOGGER.exception('Failed to publish CONFIG.md')

    def heartbeat(self):
        self.hb = 0 if self.hb else 1
        self.reportCmd('DON' if self.hb else 'DOF', 2)

    # ── device clients ─────────────────────────────────────────────────────

    def get_device(self, host: str) -> Optional[KonnectedDevice]:
        return self.devices.get(host)

    def _connect_all(self):
        # Stop clients for removed hosts
        for host in list(self.devices.keys()):
            if host not in self.hosts:
                LOGGER.info('Removing device client for %s', host)
                self.devices[host].stop()
                del self.devices[host]
                self.notice_device(host, '')

        for host in self.hosts:
            if host in self.devices:
                continue
            self._start_device(host)

        self._update_counts()

    def _start_device(self, host: str) -> Optional[KonnectedDevice]:
        LOGGER.info('Starting Konnected client for %s', host)
        try:
            device = KonnectedDevice(host)
        except ValueError as exc:
            self.notice_device(host, str(exc))
            return None

        def _cb(key, event, _host=host):
            self._on_device_event(_host, key, event)

        device.set_state_callback(_cb)
        self.devices[host] = device
        ok = device.start(wait_for_ready=True)
        if ok:
            LOGGER.info(
                'Connected to %s as %s (light=%s lock=%s)',
                host,
                device.device_type.value,
                device.has_light,
                device.has_lock,
            )
        else:
            self.notice_device(
                host, device.last_error or 'Failed to connect / discover entities'
            )
            return device
        self._refresh_device_notice(host, device)
        return device

    def _check_connections(self):
        for host, device in list(self.devices.items()):
            if not device.online:
                LOGGER.info(
                    'Device %s offline — leaving native API reconnect to client thread',
                    host,
                )
            self._refresh_device_notice(host, device)
        self._update_counts()

    def _update_counts(self):
        total = len(self.devices)
        online = sum(1 for d in self.devices.values() if d.online)
        self.setDriver('GV0', total, uom=UOM_RAW)
        self.setDriver('GV1', online, uom=UOM_RAW)

    def _on_device_event(self, host: str, key: str, event: dict):
        gdo = self.gdo_nodes.get(host)
        if gdo:
            gdo.on_device_event(key, event)
        light = self.light_nodes.get(host)
        if light:
            light.on_device_event(key, event)
        if key in ('_online', '_ready'):
            if key == '_online':
                self._update_counts()
            device = self.devices.get(host)
            if device is not None:
                # On reconnect, force a notices publish so PG3 drops a stale
                # "Connection lost" even if Custom.load raced the clear.
                online_event = key == '_online' and bool(event.get('online'))
                self._refresh_device_notice(
                    host, device, force_clear_publish=online_event
                )

    # ── discover / nodes ───────────────────────────────────────────────────

    def discover_async(self, *args, **kwargs):
        """Run Discover on a worker thread (safe from Command-thread deadlock)."""
        t = self._discover_thread
        if t is not None and t.is_alive():
            LOGGER.info('Discover already running — ignoring duplicate request')
            return
        self._discover_thread = threading.Thread(
            target=self.discover,
            args=args,
            kwargs=kwargs,
            name='konnected-discover',
            daemon=True,
        )
        self._discover_thread.start()

    def discover(self, *args, **kwargs):
        if not self._discover_lock.acquire(blocking=False):
            LOGGER.info('Discover already in progress — skipping')
            return False
        try:
            return self._discover_body(*args, **kwargs)
        finally:
            self._discover_lock.release()

    def _discover_body(self, *args, **kwargs):
        LOGGER.info('Discover starting (configured hosts=%s)', self.hosts)
        self._notice_clear(NOTICE_DISCOVER)
        self._notice_clear(NOTICE_MDNS)

        # 1) mDNS browse for all Konnected devices (_konnected._tcp)
        mdns_hosts: List[str] = []
        others: List[dict] = []
        try:
            gdos, others, mdns_err = browse_konnected_devices(timeout=DEFAULT_MDNS_SECONDS)
        except Exception as exc:
            LOGGER.exception('mDNS browse failed')
            gdos, others, mdns_err = [], [], str(exc)

        if mdns_err:
            self._notice_set(
                NOTICE_MDNS,
                f'{mdns_err} You can still set custom parameter "hosts" manually.',
            )
        else:
            self._notice_clear(NOTICE_MDNS)

        # Unsupported Konnected gear (alarm panels, etc.)
        self._publish_unknown_devices(others)

        for entry in gdos:
            host = _normalize_host(entry.get('host') or entry.get('ip') or '')
            if not host:
                continue
            if host not in mdns_hosts:
                mdns_hosts.append(host)
            friendly = _sanitize_node_name(entry.get('friendly_name') or '')
            # Prefer true friendly_name over bare hostname/IP fallbacks
            if friendly and friendly != host and friendly != _normalize_host(
                entry.get('ip') or ''
            ):
                self.host_names[host] = friendly
            elif friendly and host not in self.host_names:
                self.host_names[host] = friendly

        if mdns_hosts:
            LOGGER.info(
                'mDNS discovered %d GDO host(s): %s (names=%s)',
                len(mdns_hosts),
                mdns_hosts,
                {h: self.host_names.get(h) for h in mdns_hosts},
            )
            self._notice_clear(NOTICE_DISCOVER)
            merged = list(self.hosts)
            for h in mdns_hosts:
                if h not in merged:
                    merged.append(h)
            if merged != self.hosts:
                self.hosts = merged
                self._persist_hosts()
        elif not self.hosts:
            extra = ''
            if others:
                extra = (
                    f' Found {len(others)} other Konnected device(s) that are not '
                    f'supported garage door openers (see Notices).'
                )
            self._notice_set(
                NOTICE_DISCOVER,
                'No Konnected garage door openers found via mDNS. '
                'Confirm a blaQ/White is online, or set custom parameter "hosts" to its IP.'
                + extra,
            )
            self.sync_notices()
            self._update_counts()
            return False
        else:
            # Manual hosts only — clear "none found" if we can still connect them
            self._notice_clear(NOTICE_DISCOVER)

        # 2) Connect and create IoX nodes
        self.sync_notices()
        self._connect_all()
        self._ensure_nodes_for_online()
        # Drop device notices for hosts removed from configuration
        active = {NOTICE_DEVICE_PREFIX + host_to_address(h) for h in self.hosts}
        self._notice_clear_prefix(NOTICE_DEVICE_PREFIX, keep=active)
        self._update_counts()
        LOGGER.info('Discover finished (hosts=%s, unsupported=%d)', self.hosts, len(others))
        return True

    def _persist_hosts(self) -> None:
        """Write hosts back to customparams without re-entering discover."""
        value = ','.join(self.hosts)
        current = str(self.Params.get(PARAM_HOSTS) or '').strip()
        if current == value:
            return
        # Cleared when the resulting CUSTOMPARAMS event hits handler_params
        self._suppress_param_side_effects = True
        self.Params[PARAM_HOSTS] = value

    def _ensure_nodes_for_online(self) -> None:
        for host in self.hosts:
            device = self.devices.get(host)
            if device is None:
                self._refresh_device_notice(host, None)
                continue
            if not device.online and not device.start(wait_for_ready=True):
                self._refresh_device_notice(host, device)
                continue
            if device.device_type == DeviceType.WHITE:
                LOGGER.info(
                    'Host %s looks like GDO White — cover control will work; '
                    'White-specific sensors land in a later release.',
                    host,
                )
            if device.semantic_entity('door') is None:
                self._refresh_device_notice(host, device)
                continue
            self._ensure_nodes(host, device)
            self._refresh_device_notice(host, device)

    def _rehydrate_nodes(self):
        """Recreate GDO/light node classes from saved customns + live devices."""
        # First: hosts we already connected
        for host, device in self.devices.items():
            if device.online and device.semantic_entity('door'):
                self._ensure_nodes(host, device)
                continue
            # Offline but known from prior discover — still create shell nodes
            meta_addr = host_to_address(host)
            meta = self.NsData.get(meta_addr)
            if isinstance(meta, dict) and meta.get('kind') == 'gdo':
                self._ensure_nodes(host, device)

        # Second: any saved hosts still in hosts list
        for addr in list(self.NsData.keys()):
            meta = self.NsData.get(addr)
            if not isinstance(meta, dict) or meta.get('kind') != 'gdo':
                continue
            host = meta.get(NS_HOST)
            if not host or host not in self.hosts or host in self.gdo_nodes:
                continue
            device = self.devices.get(host)
            if device is None:
                device = self._start_device(host)
            if device is not None:
                self._ensure_nodes(host, device)

    def _device_friendly_name(self, host: str) -> str:
        """Best known user-assigned device name for *host*."""
        for source in (
            self.host_names.get(host),
            (self.NsData.get(host_to_address(host)) or {}).get(NS_FRIENDLY_NAME)
            if isinstance(self.NsData.get(host_to_address(host)), dict)
            else None,
        ):
            name = _sanitize_node_name(source or '')
            if name:
                return name
        return ''

    def _fallback_gdo_name(self, host: str, device: KonnectedDevice) -> str:
        dtype = (
            device.device_type.value.title()
            if device.device_type != DeviceType.UNKNOWN
            else 'GDO'
        )
        return f'Konnected {dtype} {host.split(":")[0]}'

    def _resolve_gdo_name(
        self, host: str, device: KonnectedDevice, addr: str
    ) -> Tuple[str, bool]:
        """Return ``(name, rename)`` for the GDO node.

        Prefers the Konnected/mDNS friendly name the user assigned on the
        device. Keeps a custom IoX name unless ``change_node_names`` is set
        or the existing name is our old IP-based auto default.
        """
        friendly = self._device_friendly_name(host)
        existing = self.poly.getNodeNameFromDb(addr)
        fallback = self._fallback_gdo_name(host, device)

        if existing:
            if friendly and (
                self.change_node_names or _AUTO_NODE_NAME_RE.match(existing)
            ):
                return friendly, True
            return existing, False
        if friendly:
            return friendly, False
        return fallback, False

    def _ensure_nodes(self, host: str, device: KonnectedDevice):
        gdo_addr = host_to_address(host)
        name, rename = self._resolve_gdo_name(host, device, gdo_addr)
        if host not in self.gdo_nodes:
            self._add_gdo_node(host, name, rename=rename)
        elif rename and self.gdo_nodes[host].name != name:
            LOGGER.info('Renaming GDO %s: %r → %r', gdo_addr, self.gdo_nodes[host].name, name)
            try:
                self.poly.renameNode(gdo_addr, name)
                self.gdo_nodes[host].name = name
            except Exception:
                LOGGER.exception('Failed to rename GDO node %s', gdo_addr)

        gdo = self.gdo_nodes[host]
        if name and name != self._fallback_gdo_name(host, device):
            self.host_names[host] = name
        self.NsData[gdo_addr] = {
            'kind': 'gdo',
            NS_HOST: host,
            NS_DEVICE_TYPE: device.device_type.value,
            NS_HAS_LIGHT: bool(device.has_light),
            NS_FRIENDLY_NAME: self.host_names.get(host) or name,
        }

        light_addr = light_address_for(gdo_addr)
        light_name = f'{gdo.name} Light'
        if device.has_light:
            if host not in self.light_nodes:
                light = self._add_light_node(
                    host, gdo, light_addr, light_name, rename=rename
                )
                if light is None:
                    # Older installs added GDO under the controller (non-primary).
                    # PG3 rejects children of non-primary parents — recreate once.
                    LOGGER.warning(
                        'Light add failed for %s; recreating GDO as primary node',
                        light_addr,
                    )
                    gdo = self._recreate_gdo_as_primary(host, name, rename=rename)
                    if gdo is not None:
                        self._add_light_node(
                            host, gdo, light_addr, light_name, rename=rename
                        )
            elif rename:
                light = self.light_nodes[host]
                if light.name != light_name:
                    try:
                        self.poly.renameNode(light_addr, light_name)
                        light.name = light_name
                    except Exception:
                        LOGGER.exception('Failed to rename light node %s', light_addr)
            if host in self.light_nodes:
                self.NsData[light_addr] = {
                    'kind': 'light',
                    NS_HOST: host,
                }
        elif host in self.light_nodes:
            LOGGER.info('Device %s no longer has light — leaving existing light node', host)

    def _add_gdo_node(self, host: str, name: str, rename: bool = False) -> GarageDoor:
        addr = host_to_address(host)
        node = GarageDoor(self, addr, name, host)
        # Register before wait so API/_ready can update drivers during addNode.
        self.gdo_nodes[host] = node
        self.poly.addNode(node, rename=rename or self.change_node_names)
        if not self.wait_for_node_done(addr):
            if self.poly.getNodeNameFromDb(addr):
                LOGGER.warning(
                    'ADDNODEDONE timed out for GDO %s but node exists in PG3 — continuing',
                    addr,
                )
            else:
                LOGGER.error(
                    'Timed out waiting for GDO node %s — light child may fail', addr
                )
        # Do not rely only on START (can lag); apply cached API state now.
        try:
            node.query()
        except Exception:
            LOGGER.exception('Initial query failed for GDO %s', addr)
        return node

    def _recreate_gdo_as_primary(
        self, host: str, name: str, rename: bool = False
    ) -> Optional[GarageDoor]:
        """Delete and re-add GDO as a self-primary so light children can attach."""
        addr = host_to_address(host)
        self.gdo_nodes.pop(host, None)
        self.light_nodes.pop(host, None)
        try:
            LOGGER.info('Deleting GDO %s to recreate as primary', addr)
            self.poly.delNode(addr)
        except Exception:
            LOGGER.exception('delNode failed for %s', addr)
        # Brief pause so PG3/IoX drop the old non-primary row before re-add.
        time.sleep(0.5)
        return self._add_gdo_node(host, name, rename=rename)

    def _add_light_node(
        self,
        host: str,
        gdo: GarageDoor,
        addr: str,
        name: str,
        rename: bool = False,
    ) -> Optional[Light]:
        # Parent must be a primary node (self-primary GDO) before child addnode.
        node = Light(self, gdo, addr, name, host)
        self.poly.addNode(node, rename=rename or self.change_node_names)
        if not self.wait_for_node_done(addr):
            if self.poly.getNodeNameFromDb(addr):
                LOGGER.warning(
                    'ADDNODEDONE timed out for light %s but node exists — continuing',
                    addr,
                )
            else:
                LOGGER.error(
                    'Timed out waiting for light node %s — will retry on next Discover',
                    addr,
                )
                return None
        self.light_nodes[host] = node
        try:
            node.query()
        except Exception:
            LOGGER.exception('Initial query failed for light %s', addr)
        return node

    def query(self, command=None):
        for node in self.gdo_nodes.values():
            node.query()
        for node in self.light_nodes.values():
            node.query()
        self.reportDrivers()

    def cmd_discover(self, command=None):
        self.discover_async()

    drivers = [
        {'driver': 'ST', 'value': ISY_FALSE, 'uom': UOM_BOOLEAN, 'name': 'Node Server Online'},
        {'driver': 'GV0', 'value': 0, 'uom': UOM_RAW, 'name': 'Devices Configured'},
        {'driver': 'GV1', 'value': 0, 'uom': UOM_RAW, 'name': 'Devices Online'},
    ]
    commands = {
        'QUERY': query,
        'DISCOVER': cmd_discover,
    }
