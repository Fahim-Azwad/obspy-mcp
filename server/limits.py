"""Centralized request limits.

These limits are used by validation logic to keep waveform downloads bounded.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.config import settings


@dataclass(frozen=True)
class Limits:
    """Immutable container for safety limits."""

    max_seconds: int = settings.MAX_SECONDS
    max_traces: int = settings.MAX_TRACES
    max_total_samples: int = settings.MAX_TOTAL_SAMPLES
    max_estimated_bytes: int = settings.MAX_ESTIMATED_BYTES


LIMITS = Limits()
