"""
MCP tool implementations for ObsPy-based seismology workflows.

All tools:
- accept JSON-serializable inputs
- normalize types internally
- return JSON-only outputs
- never raise uncaught exceptions
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Tuple

from obspy import read, read_inventory, UTCDateTime

from server.config import settings
from server.fdsn import get_events, get_stations, get_waveforms
from server.validate import validate_waveforms
from server.response_utils import recommend_pre_filt
from server.picking import pick_p
from server.plotting import plot_stream


# ---------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------
DATA = Path(settings.DATA_DIR)
DATA.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------
def _hash(obj: Any) -> str:
    """Deterministic short hash for filenames."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]


def _coerce_time(val):
    """Convert ISO string → UTCDateTime if needed."""
    if val is None:
        return None
    if isinstance(val, UTCDateTime):
        return val
    if isinstance(val, str):
        return UTCDateTime(val)
    raise TypeError(f"Invalid time type: {type(val)}")


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **payload}


def _err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": msg}


# ---------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------
def search_events(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search earthquake events and return compact summaries.

    kwargs example:
      {"starttime":"2023-01-01","endtime":"2023-12-31","minmagnitude":7}
    """
    try:
        # Raw ObsPy Catalog from the provider.
        cat = get_events(provider, kwargs)
        events = []

        for ev in cat:
            try:
                # Prefer provider-chosen origin/magnitude, fall back to first.
                origin = ev.preferred_origin() or (
                    ev.origins[0] if ev.origins else None
                )
                mag = ev.preferred_magnitude() or (
                    ev.magnitudes[0] if ev.magnitudes else None
                )
                desc = ev.event_descriptions[0].text if ev.event_descriptions else ""

                events.append(
                    {
                        "id": str(ev.resource_id),
                        "time": origin.time.isoformat() if origin else None,
                        "latitude": float(origin.latitude) if origin else None,
                        "longitude": float(origin.longitude) if origin else None,
                        "depth_km": (
                            float(origin.depth) / 1000.0
                            if origin and origin.depth
                            else None
                        ),
                        "magnitude": float(mag.mag) if mag else None,
                        "magnitude_type": mag.magnitude_type if mag else None,
                        "description": desc,
                    }
                )
            except Exception:
                continue

        # Sort newest-first (agent typically picks the first).
        events.sort(key=lambda e: e["time"] or "", reverse=True)
        return _ok({"count": len(events), "events": events})

    except Exception as e:
        return _err(str(e))


def search_stations(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search stations and return a compact list for agent selection.
    """
    try:
        # Raw ObsPy Inventory.
        inv = get_stations(provider, kwargs)
        stations = []

        for net in inv:
            for sta in net:
                stations.append(
                    {
                        "network": net.code,
                        "station": sta.code,
                        "latitude": (
                            float(sta.latitude) if sta.latitude is not None else None
                        ),
                        "longitude": (
                            float(sta.longitude) if sta.longitude is not None else None
                        ),
                        "elevation_m": (
                            float(sta.elevation) if sta.elevation is not None else None
                        ),
                    }
                )

        # Hard cap the response to keep tool outputs manageable.
        return _ok({"count": len(stations), "stations": stations[:200]})

    except Exception as e:
        return _err(str(e))


def download_stations(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download StationXML for response removal.
    """
    try:
        # StationXML typically needed for response removal.
        inv = get_stations(provider, kwargs)
        hid = _hash({"provider": provider, "kwargs": kwargs, "tool": "stations"})
        path = DATA / f"stations_{hid}.stationxml"
        inv.write(str(path), format="STATIONXML")
        return _ok({"file": str(path)})

    except Exception as e:
        return _err(str(e))


def download_waveforms(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download waveform data and write MiniSEED.

    Accepts ISO time strings; coerces internally.
    """
    try:
        # Validate before downloading to keep requests bounded.
        ok, info = validate_waveforms(kwargs)
        if not ok:
            return _err(info)

        starttime = _coerce_time(kwargs.get("starttime"))
        endtime = _coerce_time(kwargs.get("endtime"))

        wf_kwargs = dict(kwargs)
        wf_kwargs["starttime"] = starttime
        wf_kwargs["endtime"] = endtime

        # Fetch waveforms into an ObsPy Stream.
        st = get_waveforms(provider, wf_kwargs)

        hid = _hash(wf_kwargs)
        path = DATA / f"waveforms_{hid}.mseed"
        st.write(str(path), format="MSEED")

        return _ok(
            {
                "file": str(path),
                "ntraces": len(st),
                "info": info,
            }
        )

    except Exception as e:
        return _err(str(e))


def full_process(waveform_file: str, stationxml_file: str) -> Dict[str, Any]:
    """
    Full preprocessing + response removal + P-picking + plotting.
    """
    try:
        # Load artifacts produced by earlier tools.
        st = read(waveform_file)
        inv = read_inventory(stationxml_file)

        # Basic preprocessing (detrend/taper/filter).
        st.detrend("demean")
        st.detrend("linear")
        st.taper(0.05)

        sr = st[0].stats.sampling_rate
        st.filter("bandpass", freqmin=0.01, freqmax=1.0)

        # Response removal with a conservative pre-filter.
        pre = recommend_pre_filt(sr)
        st.remove_response(inv, output="VEL", pre_filt=pre)

        # Phase picking (rough P onset per trace).
        picks = {}
        for tr in st:
            p = pick_p(tr)
            if p:
                picks[tr.id] = p.isoformat()

        hid = _hash({"wf": waveform_file, "sta": stationxml_file})
        out = DATA / f"processed_{hid}.mseed"
        plot = DATA / f"processed_{hid}.png"

        # Persist processed stream and a quick-look plot.
        st.write(str(out), format="MSEED")
        plot_stream(st, plot)

        return _ok(
            {
                "file": str(out),
                "plot": str(plot),
                "picks": picks,
            }
        )

    except Exception as e:
        return _err(str(e))
