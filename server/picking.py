
from obspy.signal.trigger import classic_sta_lta, trigger_onset

def pick_p(trace, sta=1.0, lta=20.0):
    df = trace.stats.sampling_rate
    cft = classic_sta_lta(trace.data, int(sta*df), int(lta*df))
    onsets = trigger_onset(cft, 3.5, 1.0)
    if not onsets.any():
        return None
    return trace.stats.starttime + onsets[0][0] / df
