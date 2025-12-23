
from obspy import UTCDateTime
from server.limits import LIMITS

def validate_waveforms(kwargs):
    start = UTCDateTime(kwargs["starttime"])
    end = UTCDateTime(kwargs["endtime"])
    duration = end - start
    est_bytes = duration * 100 * 3 * 4

    if duration > LIMITS.max_seconds:
        return False, f"Duration {duration}s exceeds limit"

    if est_bytes > LIMITS.max_estimated_bytes:
        return False, f"Estimated bytes {int(est_bytes)} exceed limit"

    return True, {
        "duration_seconds": float(duration),
        "estimated_bytes": int(est_bytes),
    }
