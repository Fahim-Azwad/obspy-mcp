"""Simple phase picking utilities.

Currently uses a classic STA/LTA trigger to produce a rough P-onset pick.
"""

from __future__ import annotations

from obspy.signal.trigger import classic_sta_lta, trigger_onset


def pick_p(trace, sta: float = 1.0, lta: float = 20.0):
    """Pick a P onset time using STA/LTA.

    Returns an ObsPy `UTCDateTime` or None if no trigger is found.
    """
    # Convert STA/LTA windows from seconds to samples.
    df = trace.stats.sampling_rate
    cft = classic_sta_lta(trace.data, int(sta * df), int(lta * df))

    # Trigger when the STA/LTA ratio crosses on/off thresholds.
    onsets = trigger_onset(cft, 3.5, 1.0)
    if not onsets.any():
        return None

    # First trigger sample converted back to absolute time.
    return trace.stats.starttime + onsets[0][0] / df
