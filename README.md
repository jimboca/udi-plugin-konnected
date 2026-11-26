# udi-plugin-konnected

Polyglot v3 Node Server for [Konnected](https://konnected.io) garage door openers.

Uses each device’s local **ESPHome native API** (TCP **6053**) — the same protocol Home Assistant uses. No MQTT flash and no HTTP SSE.

## Status

| Version | Hardware |
|---------|----------|
| **1.x** | **GDO blaQ** — door, light, lock, motion, obstruction, synced / re-sync |
| Later | **GDO White** — dry-contact opener; architecture already classifies White devices |

## Quick start

1. Install the plugin in PG3 and run install (`aioesphomeapi` required).
2. On startup it runs **Discover** (mDNS `_konnected._tcp`) and creates IoX nodes named from each device’s Konnected friendly name; click Discover anytime to rescan.
3. Optional: set custom parameter `hosts` to pin IPs if mDNS is unavailable.

Full setup, statuses, and troubleshooting: **[CONFIG.md](CONFIG.md)**.

To capture live device traffic while debugging, set PG3 **Logger Level** to **Debug + API Stream** (see CONFIG) and watch `logs/debug.log` for `API state` lines.

## Requirements

- PG3 / PG3x with Python 3
- Konnected GDO reachable on the LAN (native API **6053**); multicast DNS for auto-discover
- Dependencies: `udi_interface`, `aioesphomeapi` (below 46.29), `zeroconf`, `markdown2` (see `requirements.txt`). On current eisy (Python 3.12) install **`pkg install py312-cryptography`** first. `install.sh` uses that OS package and does not build `cryptography` 50 (no FreeBSD wheel). `aioesphomeapi` is installed with `SKIP_CYTHON=1`.

## Development

```bash
cd plugins/udi-plugin-konnected
gmake check          # xmllint + pytest (live tests skip if no GDO on LAN)
gmake smoke          # mDNS discover + read-only native API connect (fails if none found)
gmake zip            # ad-hoc local zip (not a store release)
gmake help           # list targets
```

Live tests use mDNS by default, or pin with `KONNECTED_HOST=192.168.x.x`. They are **read-only** (no door open/close).

PG3 release (clean tree; FreeBSD needs **gmake**):

1. Bump `nodes/__init__.py` `VERSION`, update `CHANGELOG.md`, commit.
2. `gmake release` — tag `v<VERSION>` and push branch + tag.
3. `gmake beta` — push `beta` and build `Konnected-beta-<VERSION>.zip`.
4. `gmake production` — push `production` and build `Konnected-production-<VERSION>.zip`.

Do not run release targets unless cutting a release.

## Docs

- [CONFIG.md](CONFIG.md) — Polyglot Configuration help
- [CHANGELOG.md](CHANGELOG.md)
- ESPHome native API: [api component](https://esphome.io/components/api/)
- Konnected: [GDO blaQ](https://konnected.readme.io/reference/gdo-blaq-introduction)
