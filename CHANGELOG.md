# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Discover** browses LAN mDNS for `_konnected._tcp`, filters garage door openers (blaQ/White), and auto-fills the `hosts` parameter. Manual `hosts` IPs remain supported when multicast is blocked.
- Discover runs automatically on Node Server startup (same path as the Discover button).
- Live smoke tests (`tests/test_live_smoke.py`, `gmake smoke`) for mDNS discover + read-only REST/SSE connect.
- PG3 Notices for unsupported Konnected devices (e.g. alarm panels), mDNS/connect failures, missing cover entity, and command errors — notices clear when the condition resolves.

### Fixed

- mDNS discovery handlers compatible with `zeroconf` 0.13x keyword-argument callbacks (Discover was silently finding zero devices).
- Wait for GDO `ADDNODEDONE` before adding the garage light child (PG3 rejected the light when the parent was not ready yet).
- `CUSTOMNS` handler arity (`key`, `data`) — matches PG3 publish shape; was raising `TypeError` on startup.
- Do not flash a “no garage door cover entity” Notice while SSE entity discovery is still in progress.
- IoX node names use the device’s mDNS friendly name (user-assigned, e.g. GDO-South) instead of `Konnected Blaq <IP>`.
- mDNS on FreeBSD binds non-loopback IPv4 only (avoids zeroconf `Errno 49 Can't assign requested address` spam on `127.0.0.1`).
- Garage door / light nodes REST-query on start (and reconnect) so drivers are not stuck on Unknown until the next SSE change.
- Controller **Node Server Online** uses boolean UOM 2 (True/False). PG3 `conn_status` was forcing UOM 25 and displaying raw `1`.
- Garage door nodes are self-primary so the Garage Light child can be added (PG3 rejected lights whose parent was a non-primary controller leaf). Existing GDOs are recreated once as primary if needed.

## [0.1.0] - 2026-08-05

### Added

- Initial Konnected Node Server for PG3 (`udi-plugin-konnected`).
- Local REST + SSE client with ESPHome entity discovery (firmware-safe paths).
- **GDO blaQ** support: door open/close/stop, light, remote lockout, motion, obstruction, synced, re-sync.
- Controller custom parameter `hosts` (comma-separated device IPs/hostnames).
- IoX profile for controller, garage door, and garage light nodes.
- Device-type detection hooks for future **GDO White** support (classified but White-specific sensors not yet mapped).
