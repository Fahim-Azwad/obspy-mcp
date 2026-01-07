"""Thin wrappers around ObsPy's FDSN `Client`.

Keeping these calls in one place makes it easier to:
- swap/extend providers
- mock in tests
- keep the MCP tool implementations focused on I/O + validation
"""

from __future__ import annotations

from typing import Any, Dict

from obspy.clients.fdsn import Client


def client(provider: str) -> Client:
    """Construct an ObsPy FDSN client for a provider name (e.g., IRIS/USGS/EMSC)."""
    return Client(provider)


def get_events(provider: str, kwargs: Dict[str, Any]):
    """Proxy to `Client.get_events(**kwargs)`."""
    return client(provider).get_events(**kwargs)


def get_stations(provider: str, kwargs: Dict[str, Any]):
    """Proxy to `Client.get_stations(**kwargs)`."""
    return client(provider).get_stations(**kwargs)


def get_waveforms(provider: str, kwargs: Dict[str, Any]):
    """Proxy to `Client.get_waveforms(**kwargs)`."""
    return client(provider).get_waveforms(**kwargs)
