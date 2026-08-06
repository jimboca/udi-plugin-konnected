# Configure the Konnected Node Server

Local control of [Konnected](https://konnected.io) garage door openers from IoX / Polyglot v3. This plugin talks to each device over its built-in **REST API** and **Server-Sent Events (SSE)** stream — no MQTT flashing required.

## Supported hardware

| Device | Status | Notes |
|--------|--------|-------|
| **GDO blaQ** (GDOv2-Q) | Supported (v0.1.0) | Security+ wireline — door, light, lock, motion, obstruction, synced |
| **GDO White** (GDOv2-S / GDOv1-S) | Planned | Dry-contact + sensors; cover open/close may already work if discovered |

Point the device at your LAN (Wi-Fi) and note its IP address (Konnected app or router DHCP list). Hostname such as `konnected-xxxxxx.local` may work if mDNS resolves on your Polyglot host.

## Custom parameters

| Key | Required | Description |
|-----|----------|-------------|
| `hosts` | No | Optional pin list of device IPs/hostnames, comma-separated. Optional `:port` (default 80). Example: `192.168.1.50`. Discover fills this automatically when mDNS finds devices. |
| `change_node_names` | No | `true` / `false` (default `false`). When true, Discover renames IoX nodes to match each device’s Konnected name (mDNS `friendly_name`). |

## Setup

1. Install / enable the Konnected plugin in PG3.
2. On startup the plugin runs **Discover** automatically: it browses the LAN for Konnected mDNS service `_konnected._tcp` (about 5 seconds), filters to garage door openers (blaQ / White), and adds their IPs to **hosts**.
3. Wait a few more seconds for each device’s SSE entity discovery.
4. IoX should show a **Garage Door Opener** node named with the name you set on the Konnected device (for example **GDO-South**), plus a child **Garage Light** when the device exposes a light.

Node names come from the device’s mDNS **friendly name** (what you assigned in the Konnected app / ESPHome). Custom names you set later in IoX are kept unless `change_node_names` is true.

You can click **Discover** again anytime to rescan. If mDNS is blocked on your network (guest VLAN, AP isolation, etc.), set **hosts** manually to the device IP and click Discover (or restart the Node Server).

## Notices

PG3 Notices are set for problems and **cleared when the condition goes away**:

| Notice | When set | Cleared when |
|--------|----------|--------------|
| No devices configured | `hosts` empty | Devices discovered or `hosts` set |
| mDNS / Discover errors | zeroconf missing, browse failure, or no GDOs found | Discover succeeds |
| Unsupported Konnected device | mDNS finds alarm panel / non-GDO project | Device no longer seen on Discover |
| Per-device (`IP: …`) | Offline, connect failure, no cover entity, command failure | Device healthy again / command succeeds |

Unsupported Konnected products (for example alarm panels) are **not** added as IoX nodes; they only appear as Notices so you know why they were ignored.

## Garage Door statuses

| Status | Meaning |
|--------|---------|
| **Door State** | Closed / Open / Stopped / Closing / Opening / Unknown |
| **Online** | Plugin has a live SSE connection to the device |
| **Obstruction** | Safety beam clear / obstructed |
| **Lockout** | Wireless remotes unlocked / locked (blaQ) |
| **Motion** | Clear / Detected (clears after ~60s if the device does not send Off) |
| **Synced** | blaQ rolling-code sync with the opener (Not Synced ⇒ use **Re-sync**) |
| **Device Type** | Unknown / blaQ / White |

## Commands

| Command | Action |
|---------|--------|
| **Open** | Open the door |
| **Close** | Close the door (blaQ runs its pre-close warning automatically) |
| **Stop** | Stop movement |
| **Lock Remotes** / **Unlock Remotes** | Disable / enable wireless remotes (blaQ) |
| **Re-sync** | Re-establish Security+ sync (blaQ) |

External door changes (wall button, remote) raise **Open** / **Close** commands for programs when not initiated by IoX. Motion raises the **Motion** command.

## Garage Light

When present (typical blaQ), a child light node supports On / Off. External light changes also raise On / Off commands for programs.

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Discover finds nothing | mDNS/multicast must reach the Polyglot host; try setting `hosts` to the IP manually; confirm `zeroconf` is installed (`pkg install py311-zeroconf` on FreeBSD) |
| Notice about `hosts` | Click Discover, or set the parameter and save |
| Device offline after discover | Ping the IP from the Polyglot host; ensure HTTP port 80 is open; reboot the Konnected |
| Door commands fail, **Synced** = Not Synced | Run **Re-sync** on the garage node; complete initial blaQ pairing if new |
| Nodes missing after upgrade | Discover again (addresses are stable per host) |
| White device shows Unknown type | Expected in 0.1.0 — cover control may still work; White sensors come later |

## Network notes

- Traffic is plain HTTP on the LAN (ESPHome web server). Keep devices on a trusted VLAN.
- The plugin does **not** use Konnected Cloud or MQTT.
- Entity paths are discovered from `/events` so firmware renames and ESPHome URL format changes are handled without hardcoding paths.
