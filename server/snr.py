"""Signal-to-noise estimation helpers."""

from __future__ import annotations

import numpy as np


def snr(trace) -> float:
    """Estimate a simple SNR ratio (std(signal) / std(noise))."""
    data = trace.data.astype(float)

    # Heuristic windows: first 10% as noise, mid-quarter as signal.
    noise = data[: len(data) // 10]
    signal = data[len(data) // 4 : len(data) // 2]
    return signal.std() / max(noise.std(), 1e-6)
