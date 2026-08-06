"""Pytest fixtures for udi-plugin-konnected."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'live: tests that talk to a real Konnected device on the LAN',
    )


def pytest_collection_modifyitems(config, items):
    """Allow skipping all live tests with KONNECTED_SKIP_LIVE=1."""
    if os.environ.get('KONNECTED_SKIP_LIVE', '').strip().lower() in ('1', 'true', 'yes'):
        skip = pytest.mark.skip(reason='KONNECTED_SKIP_LIVE=1')
        for item in items:
            if 'live' in item.keywords:
                item.add_marker(skip)
