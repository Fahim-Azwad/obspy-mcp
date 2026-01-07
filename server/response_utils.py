"""Response-removal helper utilities."""

from __future__ import annotations


def recommend_pre_filt(sampling_rate: float):
    """Recommend a conservative pre-filter for response removal.

    The values are expressed as fractions of the Nyquist frequency.
    """
    nyq = sampling_rate / 2.0
    return [0.01 * nyq, 0.02 * nyq, 0.8 * nyq, 0.9 * nyq]
