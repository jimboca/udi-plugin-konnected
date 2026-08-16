"""Native ESPHome API client for a single Konnected GDO (aioesphomeapi).

Replaces the former REST + SSE web-server client. Home Assistant uses this same
native TCP protocol (:6053); it is far more reliable than ESPHome's HTTP SSE.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from udi_interface import LOGGER

from const import DEFAULT_API_PORT, DEFAULT_API_READY_TIMEOUT, DEFAULT_RECONNECT_DELAY
from . import stream_debug
from .models import (
    DeviceType,
    SEM_DOOR,
    SEM_LIGHT,
    SEM_LOCK,
    SEM_MOTION,
    SEM_OBSTRUCTION,
    SEM_RESYNC,
    SEM_SYNCED,
    classify_device,
    semantic_entity_map,
)

StateCallback = Callable[[str, dict], None]

try:
    from aioesphomeapi import APIClient
    from aioesphomeapi.core import APIConnectionError
    from aioesphomeapi.model import (
        BinarySensorState,
        ButtonInfo,
        CoverOperation,
        CoverState,
        LightState,
        LockCommand,
        LockEntityState,
        LockState,
    )
except ImportError as exc:  # pragma: no cover - install.sh should provide it
    APIClient = None  # type: ignore
    APIConnectionError = Exception  # type: ignore
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _entity_id_from_info(info: Any) -> str:
    """Build a stable domain/name id for semantic mapping (SSE-compatible)."""
    name = getattr(info, 'name', None) or getattr(info, 'object_id', '') or 'unknown'
    cls = type(info).__name__
    domain = {
        'CoverInfo': 'cover',
        'LightInfo': 'light',
        'LockInfo': 'lock',
        'BinarySensorInfo': 'binary_sensor',
        'SensorInfo': 'sensor',
        'ButtonInfo': 'button',
        'SwitchInfo': 'switch',
        'TextSensorInfo': 'text_sensor',
    }.get(cls, cls.replace('Info', '').lower())
    return f'{domain}/{name}'


def _cover_event(state: CoverState) -> dict:
    op = state.current_operation
    if op == CoverOperation.IS_OPENING:
        op_s = 'OPENING'
    elif op == CoverOperation.IS_CLOSING:
        op_s = 'CLOSING'
    else:
        op_s = 'IDLE'
    pos = float(state.position) if state.position is not None else 0.0
    # When idle, position decides open vs closed; while moving, op wins in parse.
    door_state = 'OPEN' if pos >= 0.5 else 'CLOSED'
    return {
        'state': door_state,
        'current_operation': op_s,
        'position': pos,
        'value': pos,
    }


def _binary_event(state: BinarySensorState) -> dict:
    on = bool(state.state)
    return {'state': 'ON' if on else 'OFF', 'value': on}


def _light_event(state: LightState) -> dict:
    on = bool(state.state)
    return {'state': 'ON' if on else 'OFF', 'value': on}


def _lock_event(state: LockEntityState) -> dict:
    st = state.state
    if st in (LockState.LOCKED, LockState.LOCKING):
        return {'state': 'LOCKED', 'value': True}
    if st in (LockState.UNLOCKED, LockState.UNLOCKING, LockState.OPEN, LockState.OPENING):
        return {'state': 'UNLOCKED', 'value': False}
    return {'state': 'UNKNOWN', 'value': None}


class KonnectedDevice:
    """Discover entities and stream state via the ESPHome native API."""

    def __init__(self, host: str, port: int = DEFAULT_API_PORT):
        if _IMPORT_ERROR is not None:
            raise ImportError(
                'aioesphomeapi is required for Konnected native API support'
            ) from _IMPORT_ERROR

        host = host.strip()
        if not host:
            raise ValueError('host is required')
        if '://' in host:
            raise ValueError('host must be hostname or IP, not a URL')
        # host:port — if the old HTTP port (80) was stored, use native API port.
        if ':' in host and host.count(':') == 1 and not host.startswith('['):
            host, port_s = host.rsplit(':', 1)
            parsed = int(port_s)
            port = DEFAULT_API_PORT if parsed in (80, 443) else parsed

        self.host = host
        self.port = port
        self.base_url = f'http://{self.host}'  # legacy attribute for notices/logs

        self._entity_paths: Dict[str, str] = {}  # entity_id → placeholder path
        self._semantic: Dict[str, str] = {}  # semantic → entity_id
        self._keys: Dict[str, int] = {}  # semantic → native API key
        self._info_by_key: Dict[int, Any] = {}
        self._states: Dict[str, dict] = {}  # semantic → last event dict
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[APIClient] = None
        self._online = False
        self._device_type = DeviceType.UNKNOWN
        self._state_callback: Optional[StateCallback] = None
        self._last_error: Optional[str] = None
        self._friendly_name: Optional[str] = None
        self._project_name: Optional[str] = None
        # Ordered queue so setDriver/MQTT never runs on the API asyncio thread
        # (blocking the loop can starve pings and provoke connection resets).
        self._cb_queue: queue.Queue[Optional[Tuple[str, dict]]] = queue.Queue()
        self._cb_thread: Optional[threading.Thread] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    @property
    def online(self) -> bool:
        return self._online

    @property
    def discovery_ready(self) -> bool:
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

    def _ensure_cb_worker(self) -> None:
        if self._cb_thread and self._cb_thread.is_alive():
            return
        self._cb_thread = threading.Thread(
            target=self._cb_worker,
            name=f'konnected-cb-{self.host}',
            daemon=True,
        )
        self._cb_thread.start()

    def _cb_worker(self) -> None:
        while True:
            item = self._cb_queue.get()
            if item is None:
                break
            key, event = item
            cb = self._state_callback
            if cb is None:
                continue
            try:
                cb(key, event)
            except Exception:
                LOGGER.exception(
                    'State callback failed for %s on %s', key, self.host
                )

    def _emit(self, key: str, event: dict) -> None:
        """Queue node updates off the API asyncio thread (ordered)."""
        if self._state_callback is None:
            return
        self._ensure_cb_worker()
        self._cb_queue.put((key, event))

    def start(self, wait_for_ready: bool = True, timeout: float = DEFAULT_API_READY_TIMEOUT) -> bool:
        """Start background native-API listener. Returns True if discovery succeeded."""
        if self._thread and self._thread.is_alive():
            if wait_for_ready:
                return self._ready.wait(timeout=timeout)
            return True

        self._stop.clear()
        self._ready.clear()
        self._ensure_cb_worker()
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f'konnected-api-{self.host}',
            daemon=True,
        )
        self._thread.start()
        if not wait_for_ready:
            return True
        ok = self._ready.wait(timeout=timeout)
        if not ok:
            self._last_error = f'Native API discovery timed out for {self.host}'
            LOGGER.warning(self._last_error)
        return ok

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_disconnect(), loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=8.0)
        self._online = False
        self._thread = None
        self._loop = None
        self._client = None
        # Drain callback worker
        self._cb_queue.put(None)
        if self._cb_thread and self._cb_thread.is_alive():
            self._cb_thread.join(timeout=2.0)
        self._cb_thread = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_run())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _async_run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._async_session()
            except Exception as exc:
                self._last_error = str(exc)
                LOGGER.warning('Native API session ended on %s: %s', self.host, exc)
            with self._lock:
                self._states.clear()
            await self._set_online(False)
            if self._stop.is_set():
                break
            await asyncio.sleep(DEFAULT_RECONNECT_DELAY)

    async def _async_disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.disconnect(force=True)
            except Exception:
                LOGGER.debug('disconnect failed for %s', self.host, exc_info=True)

    async def _async_session(self) -> None:
        LOGGER.info('Connecting native API to %s:%s', self.host, self.port)
        client = APIClient(self.host, self.port, password=None)
        self._client = client
        disconnected = asyncio.Event()

        async def _on_stop(expected: bool) -> None:
            LOGGER.info(
                'Native API stopped on %s (expected=%s)',
                self.host,
                expected,
            )
            disconnected.set()

        await client.connect(login=True, on_stop=_on_stop)

        info = await client.device_info()
        self._friendly_name = info.friendly_name or info.name
        self._project_name = info.project_name or ''
        self._last_error = None

        entities, _services = await client.list_entities_services()
        self._ingest_entities(entities)
        self._finalize_discovery()
        # Subscribe before Online so the first state burst is not raced by a
        # stale cache query from the node layer.
        client.subscribe_states(self._on_native_state)
        await self._set_online(True)
        self._ready.set()

        try:
            while not self._stop.is_set() and not disconnected.is_set():
                try:
                    await asyncio.wait_for(disconnected.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        finally:
            if not disconnected.is_set():
                await self._async_disconnect()

    def _ingest_entities(self, entities: list) -> None:
        with self._lock:
            self._entity_paths.clear()
            self._info_by_key.clear()
            self._keys.clear()
            ids = []
            for ent in entities:
                eid = _entity_id_from_info(ent)
                ids.append(eid)
                self._entity_paths[eid] = f'/{eid}'  # not used for HTTP anymore
                self._info_by_key[ent.key] = ent
            self._device_type = classify_device(ids)
            self._semantic = semantic_entity_map(ids)
            # Map semantic → native key
            id_to_key = {
                _entity_id_from_info(ent): ent.key for ent in entities
            }
            for sem, eid in self._semantic.items():
                key = id_to_key.get(eid)
                if key is not None:
                    self._keys[sem] = key
            # Prefer button named Re-sync for SEM_RESYNC
            for ent in entities:
                if isinstance(ent, ButtonInfo) and 'sync' in (ent.name or '').lower():
                    self._keys[SEM_RESYNC] = ent.key
                    self._semantic[SEM_RESYNC] = _entity_id_from_info(ent)
                    break

    def _finalize_discovery(self) -> None:
        LOGGER.info(
            'Discovered %d entities on %s (%s): %s',
            len(self._entity_paths),
            self.host,
            self._device_type.value,
            ', '.join(sorted(self._semantic.keys())),
        )
        self._emit('_ready', {})

    async def _set_online(self, online: bool) -> None:
        if self._online == online:
            return
        self._online = online
        self._emit('_online', {'online': online})

    def _on_native_state(self, state: Any) -> None:
        info = self._info_by_key.get(state.key)
        if info is None:
            return
        eid = _entity_id_from_info(info)
        sem = None
        with self._lock:
            for s, mapped in self._semantic.items():
                if mapped == eid:
                    sem = s
                    break
        event: Optional[dict] = None
        if isinstance(state, CoverState):
            event = _cover_event(state)
            sem = sem or SEM_DOOR
        elif isinstance(state, LightState):
            event = _light_event(state)
            sem = sem or SEM_LIGHT
        elif isinstance(state, BinarySensorState):
            event = _binary_event(state)
        elif isinstance(state, LockEntityState):
            event = _lock_event(state)
            sem = sem or SEM_LOCK
        if event is None or sem is None:
            return
        with self._lock:
            self._states[sem] = event
        stream_debug.log(self.host, 'API state', {'sem': sem, **event})
        self._emit(sem, event)

    # ── entity helpers ─────────────────────────────────────────────────────

    def entity_ids(self) -> list:
        with self._lock:
            return list(self._entity_paths.keys())

    def semantic_entity(self, semantic: str) -> Optional[str]:
        with self._lock:
            return self._semantic.get(semantic)

    def get_state(self, semantic: str) -> Optional[dict]:
        """Return last cached native-API state for a semantic key."""
        with self._lock:
            cached = self._states.get(semantic)
            return dict(cached) if cached else None

    # ── commands (sync, marshalled onto the API loop) ──────────────────────

    def _run_on_loop(self, fn: Callable[[], Any], timeout: float = 10.0) -> Any:
        loop = self._loop
        if loop is None or not loop.is_running() or not self._online:
            self._last_error = 'Device offline'
            return None
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def _wrapper() -> None:
            try:
                fut.set_result(fn())
            except Exception as exc:
                fut.set_exception(exc)

        loop.call_soon_threadsafe(_wrapper)
        try:
            return fut.result(timeout=timeout)
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning('API command failed on %s: %s', self.host, exc)
            return None

    def open_door(self) -> bool:
        key = self._keys.get(SEM_DOOR)
        if key is None or self._client is None:
            self._last_error = 'No garage door entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.cover_command(key, position=1.0)
            return True

        return bool(self._run_on_loop(_do))

    def close_door(self) -> bool:
        key = self._keys.get(SEM_DOOR)
        if key is None or self._client is None:
            self._last_error = 'No garage door entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.cover_command(key, position=0.0)
            return True

        return bool(self._run_on_loop(_do))

    def stop_door(self) -> bool:
        key = self._keys.get(SEM_DOOR)
        if key is None or self._client is None:
            self._last_error = 'No garage door entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.cover_command(key, stop=True)
            return True

        return bool(self._run_on_loop(_do))

    def turn_on_light(self) -> bool:
        key = self._keys.get(SEM_LIGHT)
        if key is None or self._client is None:
            self._last_error = 'No light entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.light_command(key, state=True)
            return True

        return bool(self._run_on_loop(_do))

    def turn_off_light(self) -> bool:
        key = self._keys.get(SEM_LIGHT)
        if key is None or self._client is None:
            self._last_error = 'No light entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.light_command(key, state=False)
            return True

        return bool(self._run_on_loop(_do))

    def lock(self) -> bool:
        key = self._keys.get(SEM_LOCK)
        if key is None or self._client is None:
            self._last_error = 'No lock entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.lock_command(key, LockCommand.LOCK)
            return True

        return bool(self._run_on_loop(_do))

    def unlock(self) -> bool:
        key = self._keys.get(SEM_LOCK)
        if key is None or self._client is None:
            self._last_error = 'No lock entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.lock_command(key, LockCommand.UNLOCK)
            return True

        return bool(self._run_on_loop(_do))

    def resync(self) -> bool:
        key = self._keys.get(SEM_RESYNC)
        if key is None or self._client is None:
            self._last_error = 'No Re-sync button entity'
            return False

        def _do() -> bool:
            assert self._client is not None
            self._client.button_command(key)
            return True

        return bool(self._run_on_loop(_do))

    def probe(self) -> bool:
        """True when the native API session is up."""
        return self._online
