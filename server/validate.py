"""Validation helpers for MCP tools.

The goal is to reject obviously too-large waveform requests early.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, Union

from obspy import UTCDateTime

from server.limits import LIMITS


def validate_waveforms(
    kwargs: Dict[str, Any],
) -> Tuple[bool, Union[str, Dict[str, Any]]]:
    """Validate waveform request kwargs against safety limits.

    Notes:
    - Expects `starttime` and `endtime` keys (ISO8601 strings).
    - Uses a simple bytes estimate (heuristic) to bound downloads.
    """
    # Parse ISO8601 time strings into ObsPy UTCDateTime.
    start = UTCDateTime(kwargs["starttime"])
    end = UTCDateTime(kwargs["endtime"])

    # Duration in seconds.
    duration = end - start

    # Rough size estimate: 100 Hz * 3 components * 4 bytes/sample.
    est_bytes = duration * 100 * 3 * 4

    if duration > LIMITS.max_seconds:
        return False, f"Duration {duration}s exceeds limit"

    if est_bytes > LIMITS.max_estimated_bytes:
        return False, f"Estimated bytes {int(est_bytes)} exceed limit"

    return True, {
        "duration_seconds": float(duration),
        "estimated_bytes": int(est_bytes),
    }
