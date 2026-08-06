#!/usr/bin/env bash
# Install Python dependencies for this Node Server on the Polyglot/eisy host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PIP_FLAGS=(--user --no-warn-script-location --no-input)

# FreeBSD: prefer OS package py311-zeroconf. pip --upgrade otherwise fetches
# the newest zeroconf from PyPI and builds it from source (no FreeBSD wheel).
if command -v freebsd-version >/dev/null 2>&1; then
  if python3 -c 'import zeroconf' >/dev/null 2>&1; then
    ver="$(python3 -c 'import zeroconf; print(getattr(zeroconf, "__version__", "?"))')"
    echo "zeroconf: using existing install (${ver}) — skipping pip for zeroconf"
    pip3 install "${PIP_FLAGS[@]}" \
      'udi_interface>=3.3.14' \
      'requests>=2.28.0' \
      markdown2
  else
    py_tag="$(python3 -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}")')"
    echo "NOTE: No zeroconf importable. Prefer: pkg install ${py_tag}-zeroconf" >&2
    echo "      Falling back to pip (may build from source on FreeBSD)." >&2
    pip3 install "${PIP_FLAGS[@]}" -r requirements.txt
  fi
else
  pip3 install "${PIP_FLAGS[@]}" --upgrade -r requirements.txt
fi

echo "udi-plugin-konnected dependencies installed."
