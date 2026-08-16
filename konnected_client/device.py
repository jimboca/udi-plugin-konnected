"""REST + SSE client for a single Konnected ESPHome device."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote, urljoin

import requests
from udi_interface import LOGGER

from const import DEFAULT_HTTP_TIMEOUT, DEFAULT_RECONNECT_DELAY, DEFAULT_SSE_READY_TIMEOUT
from . import stream_debug
from .models import (
    DeviceType,
    SEM_DOOR,
    SEM_LIGHT,
    SEM_LOCK,
    classify_device,
    semantic_entity_map,
)

StateCallback = Callable[[str, dict], None]
# callback(semantic_key or entity_id, event_dict)


class KonnectedDevice:
    """Discover entities via SSE and issue REST commands.

    Follows Konnected's recommended endpoint-discovery pattern so firmware
    renames and ESPHome URL format changes do not break the plugin.
    """

    def __init__(self, host: str, port: int = 80):
        host = host.strip()
        if not host:
            raise ValueError('host is required')
        # Allow host:port in the host string
        if '://' in host:
            raise ValueError('host must be hostname or IP, not a URL')
        if ':' in host and host.count(':') == 1 and not host.startswith('['):
            host, port_s = host.rsplit(':', 1)
            port = int(port_s)
        self.host = host
        self.port = port
        self.base_url = f'http://{self.host}:{self.port}' if self.port != 80 else f'http://{self.host}'

        self._entity_paths: Dict[str, str] = {}  # entity_id → REST path
        self._semantic: Dict[str, str] = {}      # semantic → entity_id
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._online = False
        self._device_type = DeviceType.UNKNOWN
        self._state_callback: Optional[StateCallback] = None
        # Separate sessions: requests.Session is not thread-safe, and sharing one
        # between a long-lived SSE stream and REST query/command calls starves
        # iter_lines (TCP stays up, door events never reach setDriver).
        self._rest = requests.Session()
        self._sse = requests.Session()
        self._last_error: Optional[str] = None
        self._pending: Dict[str, dict] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────

    @property
    def online(self) -> bool:
        return self._online

    @property
    def discovery_ready(self) -> bool:
        """True once the initial SSE entity burst has been classified."""
        return self._ready.is_set()

    @property
    def device_type(self) -> DeviceType:
        return self._device_type

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def has_light(self) -> bool:
        return SEM_LIGHT in self._semantic

    @property
    def has_lock(self) -> bool:
        return SEM_LOCK in self._semantic

    def set_state_callback(self, callback: Optional[StateCallback]) -> None:
        self._state_callback = callback

    def start(self, wait_for_ready: bool = True, timeout: float = DEFAULT_SSE_READY_TIMEOUT) -> bool:
        """Start background SSE listener. Returns True if initial discovery succeeded."""
        if self._thread and self._thread.is_alive():
            if wait_for_ready:
                return self._ready.wait(timeout=timeout)
            return True

        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._sse_loop,
            name=f'konnected-sse-{self.host}',
            daemon=True,
        )
        self._thread.start()
        if not wait_for_ready:
            return True
        ok = self._ready.wait(timeout=timeout)
        if not ok:
            self._last_error = f'SSE discovery timed out for {self.host}'
            LOGGER.warning(self._last_error)
        return ok

    def stop(self) -> None:
        self._stop.set()
        self._online = False
        for sess in (self._sse, self._rest):
            try:
                sess.close()
            except Exception:
                pass

    # ── entity helpers ─────────────────────────────────────────────────────

    def entity_ids(self) -> list:
        with self._lock:
            return list(self._entity_paths.keys())

    def get_path(self, entity_id: str) -> Optional[str]:
        with self._lock:
            return self._entity_paths.get(entity_id)

    def semantic_path(self, semantic: str) -> Optional[str]:
        with self._lock:
            eid = self._semantic.get(semantic)
            if not eid:
                return None
            return self._entity_paths.get(eid)

    def semantic_entity(self, semantic: str) -> Optional[str]:
        with self._lock:
            return self._semantic.get(semantic)

    # ── REST ───────────────────────────────────────────────────────────────

    def get_state(self, semantic: str) -> Optional[dict]:
        path = self.semantic_path(semantic)
        if not path:
            return None
        try:
            r = self._rest.get(
                urljoin(self.base_url + '/', path.lstrip('/')),
                timeout=DEFAULT_HTTP_TIMEOUT,
            )
            if r.status_code == 404:
                self._last_error = f'404 for {path}; entity may have been renamed'
                LOGGER.warning('%s — rediscovery will run on next SSE reconnect', self._last_error)
                return None
            if r.status_code != 200:
                self._last_error = f'GET {path} → HTTP {r.status_code}'
                stream_debug.log(self.host, 'REST GET fail', self._last_error)
                return None
            data = r.json()
            stream_debug.log(self.host, f'REST GET {path}', data)
            return data
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.debug('GET %s failed: %s', path, exc)
            return None

    def post_action(self, semantic: str, action: str, params: Optional[dict] = None) -> bool:
        path = self.semantic_path(semantic)
        if not path:
            LOGGER.warning('No path for semantic=%s on %s', semantic, self.host)
            return False
        url = urljoin(self.base_url + '/', f'{path.lstrip("/")}/{action}')
        try:
            r = self._rest.post(url, params=params or {}, timeout=DEFAULT_HTTP_TIMEOUT)
            if r.status_code == 404:
                self._last_error = f'404 for {url}'
                LOGGER.warning(self._last_error)
                return False
            if r.status_code not in (200, 204):
                self._last_error = f'POST {url} → HTTP {r.status_code}'
                LOGGER.warning(self._last_error)
                return False
            stream_debug.log(
                self.host,
                f'REST POST {path}/{action}',
                {'status': r.status_code, 'params': params or {}},
            )
            return True
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.error('POST %s failed: %s', url, exc)
            return False

    def open_door(self) -> bool:
        return self.post_action(SEM_DOOR, 'open')

    def close_door(self) -> bool:
        return self.post_action(SEM_DOOR, 'close')

    def stop_door(self) -> bool:
        return self.post_action(SEM_DOOR, 'stop')

    def turn_on_light(self) -> bool:
        return self.post_action(SEM_LIGHT, 'turn_on')

    def turn_off_light(self) -> bool:
        return self.post_action(SEM_LIGHT, 'turn_off')

    def lock(self) -> bool:
        return self.post_action(SEM_LOCK, 'lock')

    def unlock(self) -> bool:
        return self.post_action(SEM_LOCK, 'unlock')

    def resync(self) -> bool:
        return self.post_action('resync', 'press')

    def probe(self) -> bool:
        """Lightweight reachability check (web root or events)."""
        try:
            r = self._rest.get(self.base_url + '/', timeout=DEFAULT_HTTP_TIMEOUT)
            return r.status_code < 500
        except Exception as exc:
            self._last_error = str(exc)
            return False

    # ── SSE ────────────────────────────────────────────────────────────────

    def _sse_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_sse()
            except Exception as exc:
                self._online = False
                self._last_error = str(exc)
                LOGGER.warning('SSE disconnected from %s: %s', self.host, exc)
                if self._state_callback:
                    try:
                        self._state_callback('_online', {'online': False})
                    except Exception:
                        LOGGER.exception('online callback failed')
            if self._stop.is_set():
                break
            time.sleep(DEFAULT_RECONNECT_DELAY)

    def _connect_sse(self) -> None:
        LOGGER.info('Connecting SSE to %s/events', self.base_url)
        # Reset discovery for each connection
        with self._lock:
            self._entity_paths.clear()
            self._semantic.clear()
        self._ready.clear()

        with self._sse.get(
            self.base_url + '/events',
            stream=True,
            timeout=(DEFAULT_HTTP_TIMEOUT, None),
            headers={'Accept': 'text/event-stream'},
        ) as resp:
            resp.raise_for_status()
            self._online = True
            self._last_error = None  # clear transient disconnect errors
            if self._state_callback:
                try:
                    self._state_callback('_online', {'online': True})
                except Exception:
                    LOGGER.exception('online callback failed')

            burst_done = {'value': False}
            burst_timer: list = [None]  # mutable cell for Timer
            BURST_IDLE = 0.75  # allow full ESPHome initial state burst

            def _mark_ready(force: bool = False):
                if self._stop.is_set() or burst_done['value']:
                    return
                # Wait for a cover entity when possible — early idle can fire mid-burst.
                with self._lock:
                    ids = list(self._entity_paths.keys())
                sem = semantic_entity_map(ids)
                if SEM_DOOR not in sem and not force and ids:
                    _bump_timer()
                    return
                burst_done['value'] = True
                self._finalize_discovery()
                self._ready.set()

            def _bump_timer():
                t = burst_timer[0]
                if t is not None:
                    t.cancel()
                burst_timer[0] = threading.Timer(BURST_IDLE, lambda: _mark_ready(False))
                burst_timer[0].daemon = True
                burst_timer[0].start()

            try:
                for raw in resp.iter_lines(decode_unicode=True):
                    if self._stop.is_set():
                        return

                    if not raw:
                        continue

                    line = (
                        raw.strip()
                        if isinstance(raw, str)
                        else raw.decode('utf-8', 'replace').strip()
                    )
                    if not line.startswith('data:'):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict) or (
                        'id' not in event and 'name_id' not in event
                    ):
                        continue

                    entity_id = event.get('name_id') or event['id']
                    rest_path = self._id_to_rest_path(entity_id)
                    with self._lock:
                        self._entity_paths[entity_id] = rest_path

                    stream_debug.log(self.host, 'SSE recv', event)

                    if burst_done['value']:
                        self._dispatch(entity_id, event)
                    else:
                        self._pending_dispatch(entity_id, event)
                        _bump_timer()
            finally:
                t = burst_timer[0]
                if t is not None:
                    t.cancel()
                if not burst_done['value'] and self._entity_paths:
                    _mark_ready(force=True)

            # stream ended
            self._online = False

    def _pending_dispatch(self, entity_id: str, event: dict) -> None:
        self._pending[entity_id] = event

    def _finalize_discovery(self) -> None:
        with self._lock:
            ids = list(self._entity_paths.keys())
            self._device_type = classify_device(ids)
            self._semantic = semantic_entity_map(ids)
        if self._state_callback:
            try:
                self._state_callback('_ready', {})
            except Exception:
                LOGGER.exception('ready callback failed')
        LOGGER.info(
            'Discovered %d entities on %s (%s): %s',
            len(ids),
            self.host,
            self._device_type.value,
            ', '.join(sorted(self._semantic.keys())),
        )
        pending = self._pending
        self._pending = {}
        for entity_id, event in pending.items():
            self._dispatch(entity_id, event)

    def _dispatch(self, entity_id: str, event: dict) -> None:
        if not self._state_callback:
            return
        # Prefer semantic key when known
        semantic = None
        with self._lock:
            for sem, eid in self._semantic.items():
                if eid == entity_id:
                    semantic = sem
                    break
        key = semantic or entity_id
        try:
            self._state_callback(key, event)
        except Exception:
            LOGGER.exception('State callback failed for %s on %s', key, self.host)

    @staticmethod
    def _id_to_rest_path(entity_id: str) -> str:
        if '/' in entity_id:
            domain, _, name = entity_id.partition('/')
            return f'/{domain}/{quote(name, safe="")}'
        return KonnectedDevice._legacy_id_to_rest_path(entity_id)

    @staticmethod
    def _legacy_id_to_rest_path(legacy_id: str) -> str:
        multi = {
            'binary-sensor': 'binary_sensor',
            'text-sensor': 'text_sensor',
            'alarm-control-panel': 'alarm_control_panel',
        }
        for prefix, domain in multi.items():
            if legacy_id.startswith(prefix + '-'):
                return f'/{domain}/{legacy_id[len(prefix) + 1:]}'
        parts = legacy_id.split('-', 1)
        if len(parts) == 2:
            return f'/{parts[0]}/{parts[1]}'
        return f'/{legacy_id}'
