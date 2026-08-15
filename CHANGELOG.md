# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- Transient SSE disconnects no longer force **Door State** / sensors / light to
  Unknown immediately. Only **Online** goes False; last-known values are kept.
  After **2 consecutive shortPolls** while still offline, drivers become Unknown
  (delay tracks the user-set PG3 shortPoll interval; default 30s ≈ 60s).

## [0.1.3] - 2026-08-14

### Fixed

- Discover no longer runs on the PG3 Command thread. Waiting for `ADDNODEDONE`
  there deadlocked MQTT processing (~30s per node), left new GDOs at Unknown
  defaults, and falsely recreated the door as primary when the light wait also
  timed out. Discover (button / poly.DISCOVER) now runs on a worker thread;
  new nodes register immediately and REST-query after add.

### Changed

- MQTT / TLS problems are diagnosed at startup and watched at runtime. Logs say
  clearly when the failure is **PG3 MQTT** (stale client cert after CA/UDX
  regen), not a Konnected garage-door LAN outage. A PG3 Notice (`mqtt`) is
  published on reconnect when the client cert fails verify or MQTT is flapping.

## [0.1.2] - 2026-08-06

### Fixed

- Clear per-device Notices when SSE reconnects (transient `Connection reset by peer` no longer sticks after the link is healthy again). Offline notices use a short “reconnecting…” message instead of the raw requests exception.

## [0.1.1] - 2026-08-05

### Fixed

- `install.sh` / `requirements.txt`: pin `zeroconf` to `>=0.132.2,<0.133` and skip pip for zeroconf on FreeBSD when the OS package is already importable (avoids upgrading to 0.150.x and a long source build).

## [0.1.0] - 2026-08-05

### Added

- Initial Konnected Node Server for PG3 (`udi-plugin-konnected`).
- Local REST + SSE client with ESPHome entity discovery (firmware-safe paths).
- **GDO blaQ** support: door open/close/stop, light, remote lockout, motion, obstruction, synced, re-sync.
- Controller custom parameter `hosts` (comma-separated device IPs/hostnames).
- IoX profile for controller, garage door, and garage light nodes.
- Device-type detection hooks for future **GDO White** support (classified but White-specific sensors not yet mapped).
- **Discover** browses LAN mDNS for `_konnected._tcp`, filters garage door openers (blaQ/White), and auto-fills the `hosts` parameter. Manual `hosts` IPs remain supported when multicast is blocked.
- Discover runs automatically on Node Server startup (same path as the Discover button).
- Live smoke tests (`tests/test_live_smoke.py`, `gmake smoke`) for mDNS discover + read-only REST/SSE connect.
- PG3 Notices for unsupported Konnected devices (e.g. alarm panels), mDNS/connect failures, missing cover entity, and command errors — notices clear when the condition resolves.
- IoX node names use the device’s mDNS friendly name (user-assigned).
- Kasa-style `gmake release` / `gmake beta` / `gmake production` targets.

### Fixed

- mDNS discovery handlers compatible with `zeroconf` 0.13x keyword-argument callbacks (Discover was silently finding zero devices).
- Wait for GDO `ADDNODEDONE` before adding the garage light child (PG3 rejected the light when the parent was not ready yet).
- `CUSTOMNS` handler arity (`key`, `data`) — matches PG3 publish shape; was raising `TypeError` on startup.
- Do not flash a “no garage door cover entity” Notice while SSE entity discovery is still in progress.
- mDNS on FreeBSD binds non-loopback IPv4 only (avoids zeroconf `Errno 49 Can't assign requested address` spam on `127.0.0.1`).
- Garage door / light nodes REST-query on start (and reconnect) so drivers are not stuck on Unknown until the next SSE change.
- Controller **Node Server Online** uses boolean UOM 2 (True/False). PG3 `conn_status` was forcing UOM 25 and displaying raw `1`.
- Garage door nodes are self-primary so the Garage Light child can be added (PG3 rejected lights whose parent was a non-primary controller leaf). Existing GDOs are recreated once as primary if needed.
