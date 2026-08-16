#!/usr/bin/env bash
# Install Python dependencies for this Node Server on the Polyglot/eisy host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PIP_FLAGS=(--user --no-warn-script-location --no-input)
# FreeBSD often lacks aioesphomeapi / maturin wheels — pure Python is fine.
export SKIP_CYTHON="${SKIP_CYTHON:-1}"

# aioesphomeapi 45+ requires zeroconf>=0.150. FreeBSD py311-zeroconf is often
# 0.132.x — pip-install a newer zeroconf (wheel or pure Python) instead of
# relying on the OS package alone.
pip3 install "${PIP_FLAGS[@]}" -r requirements.txt

if ! python3 -c 'import aioesphomeapi' >/dev/null 2>&1; then
  echo "ERROR: aioesphomeapi did not import after install" >&2
  exit 1
fi

echo "udi-plugin-konnected dependencies installed."
