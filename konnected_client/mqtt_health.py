"""Detect PG3 MQTT TLS / client-certificate problems early.

When the eISY CA is regenerated (UDX upgrade/reboot) but this Node Server's
``<uuid_slot>.cert`` / ``.key`` are stale, MQTT flaps and Discover / device
the Node Server never starts. That looks like a Konnected device outage — it is not.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

CA_CERT_PATH = Path('/usr/local/etc/ssl/certs/ud.ca.cert')

# Short text for PG3 Notices UI
NOTICE_MQTT_CERT = (
    'PG3 MQTT TLS failure: this Node Server’s client certificate is not trusted '
    'by the current CA. The NS cannot stay connected to Polyglot (not a Konnected '
    'garage-door problem). After a UDX/eISY update or CA regen, reinstall this '
    'Node Server (or regenerate its .cert/.key) and restart.'
)

NOTICE_MQTT_FLAPPING = (
    'PG3 MQTT connection is unstable (repeated disconnects). Device Discover and '
    'status updates cannot run while MQTT is down. Check Node Server MQTT certs '
    '(.cert/.key vs /usr/local/etc/ssl/certs/ud.ca.cert) or reinstall the NS after '
    'a CA/UDX update. This is not a Konnected device LAN failure.'
)


def mqtt_client_basename(workdir: Optional[Path] = None) -> Optional[str]:
    """Return ``0021b90260e8_7`` style basename for the MQTT client cert."""
    raw = os.environ.get('PG3INIT')
    if raw:
        try:
            data = json.loads(base64.b64decode(raw))
            uuid = str(data.get('uuid') or '')
            profile = data.get('profileNum')
            if uuid and profile is not None:
                return f'{uuid}_{profile}'.replace(':', '')
        except Exception:
            pass

    root = Path(workdir or os.getcwd())
    certs = sorted(root.glob('*.cert'))
    for cert in certs:
        name = cert.stem
        if name.startswith('00') and '_' in name:
            return name
    return None


def check_mqtt_client_cert(
    workdir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Verify the NS MQTT client cert against the eISY CA.

    Returns ``(ok, detail)``. ``ok`` is False when MQTT TLS will fail.
    """
    root = Path(workdir or os.getcwd())
    base = mqtt_client_basename(root)
    if not base:
        return True, 'No MQTT client cert basename found (skipped check)'

    cert = root / f'{base}.cert'
    key = root / f'{base}.key'

    if not cert.is_file() or not key.is_file():
        return (
            False,
            f'Missing MQTT client credentials {cert.name}/{key.name} in {root}. '
            'PG3 MQTT will not authenticate. Reinstall this Node Server.',
        )

    if not CA_CERT_PATH.is_file():
        return True, f'CA file {CA_CERT_PATH} missing — skipped verify'

    try:
        proc = subprocess.run(
            ['openssl', 'verify', '-CAfile', str(CA_CERT_PATH), str(cert)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        return True, 'openssl not available — skipped MQTT cert verify'
    except Exception as exc:
        return True, f'MQTT cert verify error (ignored): {exc}'

    out = (proc.stdout or '').strip()
    err = (proc.stderr or '').strip()
    if proc.returncode == 0 and 'OK' in out:
        return True, f'MQTT client cert {cert.name} verifies against {CA_CERT_PATH.name}'

    detail = err or out or f'openssl verify exit {proc.returncode}'
    return (
        False,
        f'MQTT client certificate {cert.name} does not verify against '
        f'{CA_CERT_PATH} ({detail}). After a UDX/eISY CA regen this Node Server '
        'cannot stay on PG3 MQTT — Discover and Konnected devices will look '
        'offline even if the garage openers are reachable on the LAN. '
        'Reinstall the Node Server (or regenerate .cert/.key) then restart.',
    )


def log_mqtt_health(logger, workdir: Optional[Path] = None) -> bool:
    """Log a clear MQTT vs device diagnosis. Returns True if cert looks OK."""
    ok, detail = check_mqtt_client_cert(workdir)
    if ok:
        logger.info('MQTT health: %s', detail)
    else:
        logger.error(
            'MQTT health FAILED (PG3 link, not a Konnected GDO issue): %s',
            detail,
        )
    return ok
