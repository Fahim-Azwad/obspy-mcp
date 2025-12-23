"""
server.py — ObsPy MCP Server (stdio)

Tools:
- list_fdsn_services(provider)
- fdsn_events_download(provider, kwargs, out_format, outfile_prefix, save_manifest)
- fdsn_stations_download(provider, kwargs, out_format, outfile_prefix, save_manifest)
- fdsn_waveforms_download(provider, kwargs, out_format, outfile_prefix, dry_run, override, override_reason, save_manifest)
- fdsn_waveforms_bulk_download(provider, bulk_lines, kwargs, out_format, outfile_prefix, dry_run, override, override_reason, save_manifest)
- validate_only(request, override, override_reason)  # validate + estimate only, no download

Safety + usability knobs:
- duration/traces/samples/estimated-bytes caps
- override requires override_reason
- dry_run preflight estimates
- manifest JSON next to every output
- deterministic filenames (hash of request)

Run:
  python src/server.py
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from obspy.core.event import Catalog
from obspy.core.inventory import Inventory
from obspy.core.stream import Stream

from mcp.server.fastmcp import FastMCP

# -----------------------------
# Paths
# -----------------------------
PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("ObsPy-FDSN", json_response=True)


# -----------------------------
# Limits (safety/usability)
# -----------------------------
@dataclass(frozen=True)
class DownloadLimits:
    # Waveform request limits
    max_seconds_per_request: int = 60 * 60          # 1 hour per waveform request
    max_seconds_per_trace: int = 60 * 60            # 1 hour per bulk line
    max_traces: int = 300                           # max traces in a response
    max_total_samples: int = 50_000_000             # cap
    max_estimated_bytes: int = 300 * 1024 * 1024    # 300MB cap

    # estimation defaults
    default_sampling_rate_hz: float = 100.0

LIMITS = DownloadLimits()


# -----------------------------
# Exceptions
# -----------------------------
class DownloadDenied(ValueError):
    pass


# -----------------------------
# Helpers
# -----------------------------
def _client(provider: str) -> Client:
    return Client(provider)

def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "._-+" else "_" for c in s)[:200]

def _coerce_times(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d)
    for k in ("starttime", "endtime", "time"):
        if k in out and isinstance(out[k], str):
            out[k] = UTCDateTime(out[k])
    return out

def _hash_request(obj: Dict[str, Any]) -> str:
    def convert(v: Any) -> Any:
        if isinstance(v, UTCDateTime):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: convert(v[k]) for k in sorted(v.keys())}
        if isinstance(v, list):
            return [convert(x) for x in v]
        if isinstance(v, tuple):
            return [convert(x) for x in v]
        return v

    normalized = convert(obj)
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]

def _write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, indent=2))

def _require_override(override: bool, override_reason: Optional[str], msg: str) -> None:
    if not override:
        raise DownloadDenied(msg + " Set override=true to proceed.")
    if not override_reason or not override_reason.strip():
        raise DownloadDenied(msg + " override=true requires override_reason.")

def _duration_seconds(start: UTCDateTime, end: UTCDateTime) -> float:
    return float(end - start)

def _estimate_waveform_bytes(trace_count: int, total_seconds: float, sampling_rate_hz: float, bytes_per_sample: int = 4) -> int:
    total_samples = int(total_seconds * sampling_rate_hz * trace_count)
    return total_samples * bytes_per_sample

def _compute_stream_stats(st: Stream) -> Dict[str, Any]:
    trace_count = len(st)
    total_samples = sum(int(tr.stats.npts) for tr in st)
    est_bytes = total_samples * 4
    return {"trace_count": trace_count, "total_samples": total_samples, "estimated_bytes": est_bytes}

def _post_download_enforce(
    *,
    trace_count: int,
    total_samples: int,
    estimated_bytes: Optional[int],
    override: bool,
    override_reason: Optional[str],
) -> None:
    if trace_count > LIMITS.max_traces:
        _require_override(override, override_reason, f"Downloaded trace_count {trace_count} exceeds max_traces={LIMITS.max_traces}.")
    if total_samples > LIMITS.max_total_samples:
        _require_override(override, override_reason, f"Downloaded total_samples {total_samples} exceeds max_total_samples={LIMITS.max_total_samples}.")
    if estimated_bytes is not None and estimated_bytes > LIMITS.max_estimated_bytes:
        _require_override(override, override_reason, f"Estimated bytes {estimated_bytes} exceeds max_estimated_bytes={LIMITS.max_estimated_bytes}.")

def _validate_waveform_request(
    provider: str,
    kwargs: Dict[str, Any],
    *,
    override: bool,
    override_reason: Optional[str],
    dry_run: bool,
) -> Dict[str, Any]:
    k = _coerce_times(kwargs)
    start = k.get("starttime")
    end = k.get("endtime")

    if not isinstance(start, UTCDateTime) or not isinstance(end, UTCDateTime):
        raise DownloadDenied("Waveform request must include starttime and endtime.")

    if end <= start:
        raise DownloadDenied("endtime must be greater than starttime.")

    dur = _duration_seconds(start, end)
    if dur > LIMITS.max_seconds_per_request:
        _require_override(
            override, override_reason,
            f"Requested duration {dur:.0f}s exceeds max_seconds_per_request={LIMITS.max_seconds_per_request}s."
        )

    sr = float(k.get("sampling_rate_hint_hz") or LIMITS.default_sampling_rate_hz)
    assumed_traces = 3  # ballpark for BH?/HH? (3 components)
    est_bytes = _estimate_waveform_bytes(assumed_traces, dur, sr)

    if est_bytes > LIMITS.max_estimated_bytes:
        _require_override(
            override, override_reason,
            f"Estimated size {est_bytes/1024/1024:.1f}MB exceeds max_estimated_bytes={LIMITS.max_estimated_bytes/1024/1024:.1f}MB."
        )

    return {
        "provider": provider,
        "duration_seconds": dur,
        "sampling_rate_assumed_hz": sr,
        "assumed_trace_count_for_estimate": assumed_traces,
        "estimated_bytes_ballpark": est_bytes,
        "dry_run": dry_run,
    }

def _validate_bulk_request(
    provider: str,
    bulk_lines: List[List[Union[str, float]]],
    *,
    override: bool,
    override_reason: Optional[str],
    dry_run: bool,
) -> Dict[str, Any]:
    if not bulk_lines:
        raise DownloadDenied("bulk_lines is empty.")

    if len(bulk_lines) > LIMITS.max_traces:
        _require_override(override, override_reason, f"bulk_lines count {len(bulk_lines)} exceeds max_traces={LIMITS.max_traces}.")

    total_seconds_sum = 0.0
    for i, line in enumerate(bulk_lines):
        if len(line) != 6:
            raise DownloadDenied(f"bulk_lines[{i}] must have 6 items: [net,sta,loc,cha,start,end].")
        _, _, _, _, st, et = line
        stt = UTCDateTime(str(st))
        ett = UTCDateTime(str(et))
        if ett <= stt:
            raise DownloadDenied(f"bulk_lines[{i}] endtime must be > starttime.")
        dur = _duration_seconds(stt, ett)
        if dur > LIMITS.max_seconds_per_trace:
            _require_override(
                override, override_reason,
                f"bulk_lines[{i}] duration {dur:.0f}s exceeds max_seconds_per_trace={LIMITS.max_seconds_per_trace}s."
            )
        total_seconds_sum += dur

    sr = LIMITS.default_sampling_rate_hz
    avg_seconds = total_seconds_sum / max(len(bulk_lines), 1)
    est_bytes = _estimate_waveform_bytes(len(bulk_lines), avg_seconds, sr)

    if est_bytes > LIMITS.max_estimated_bytes:
        _require_override(
            override, override_reason,
            f"Estimated size {est_bytes/1024/1024:.1f}MB exceeds max_estimated_bytes={LIMITS.max_estimated_bytes/1024/1024:.1f}MB."
        )

    return {
        "provider": provider,
        "bulk_count": len(bulk_lines),
        "total_seconds_sum": total_seconds_sum,
        "sampling_rate_assumed_hz": sr,
        "estimated_bytes_ballpark": est_bytes,
        "dry_run": dry_run,
    }


# -----------------------------
# Writers (formats + JSON fallback)
# -----------------------------
def _write_catalog(cat: Catalog, out_path: Path, fmt: str) -> None:
    if fmt.lower() in ("quakeml", "xml"):
        cat.write(str(out_path), format="QUAKEML")
        return
    events = []
    for ev in cat:
        o = ev.preferred_origin() or (ev.origins[0] if ev.origins else None)
        m = ev.preferred_magnitude() or (ev.magnitudes[0] if ev.magnitudes else None)
        events.append({
            "id": str(ev.resource_id) if ev.resource_id else None,
            "time": o.time.isoformat() if o and o.time else None,
            "lat": float(o.latitude) if o and o.latitude is not None else None,
            "lon": float(o.longitude) if o and o.longitude is not None else None,
            "depth_km": (float(o.depth) / 1000.0) if o and o.depth is not None else None,
            "mag": float(m.mag) if m and m.mag is not None else None,
            "mag_type": m.magnitude_type if m else None,
        })
    out_path.write_text(json.dumps({"count": len(events), "events": events}, indent=2))

def _write_inventory(inv: Inventory, out_path: Path, fmt: str) -> None:
    if fmt.lower() in ("stationxml", "xml"):
        inv.write(str(out_path), format="STATIONXML")
        return
    summary = {"networks": []}
    for net in inv:
        n = {"network": net.code, "stations": []}
        for sta in net.stations:
            n["stations"].append({
                "station": sta.code,
                "lat": float(sta.latitude),
                "lon": float(sta.longitude),
                "elev_m": float(sta.elevation),
                "channels": sorted({ch.code for ch in sta.channels}),
            })
        summary["networks"].append(n)
    out_path.write_text(json.dumps(summary, indent=2))

def _write_stream(st: Stream, out_path: Path, fmt: str) -> None:
    f = fmt.lower()
    if f in ("mseed", "miniseed"):
        st.write(str(out_path), format="MSEED")
        return
    if f == "sac":
        st.write(str(out_path), format="SAC")
        return
    traces = []
    for tr in st:
        traces.append({
            "id": tr.id,
            "starttime": tr.stats.starttime.isoformat(),
            "endtime": tr.stats.endtime.isoformat(),
            "sampling_rate": float(tr.stats.sampling_rate),
            "npts": int(tr.stats.npts),
        })
    out_path.write_text(json.dumps({"trace_count": len(traces), "traces": traces}, indent=2))


# -----------------------------
# MCP Tools
# -----------------------------
@mcp.tool()
def list_fdsn_services(provider: str = "IRIS") -> Dict[str, Any]:
    cli = _client(provider)
    return {"provider": provider, "services": getattr(cli, "services", None)}

@mcp.tool()
def fdsn_events_download(
    provider: str = "USGS",
    kwargs: Dict[str, Any] = None,
    out_format: str = "quakeml",
    outfile_prefix: Optional[str] = None,
    *,
    save_manifest: bool = True,
) -> Dict[str, Any]:
    kwargs = kwargs or {}
    cli = _client(provider)
    k = _coerce_times(kwargs)

    req_obj = {"tool": "fdsn_events_download", "provider": provider, "kwargs": k, "out_format": out_format}
    rid = _hash_request(req_obj)

    prefix = _safe_name(outfile_prefix or f"events_{provider}")
    ext = "xml" if out_format.lower() in ("quakeml", "xml") else "json"

    out_path = DATA_DIR / f"{prefix}_{rid}.{ext}"
    manifest_path = DATA_DIR / f"{prefix}_{rid}.manifest.json"

    t0 = time.time()
    cat = cli.get_events(**k)
    elapsed = time.time() - t0

    _write_catalog(cat, out_path, out_format)

    if save_manifest:
        manifest = {
            "tool": "fdsn_events_download",
            "provider": provider,
            "request_kwargs": {kk: (vv.isoformat() if isinstance(vv, UTCDateTime) else vv) for kk, vv in k.items()},
            "out_format": out_format,
            "output_file": str(out_path),
            "download_seconds": elapsed,
            "event_count": len(cat),
        }
        _write_manifest(manifest_path, manifest)

    return {"ok": True, "outfile": str(out_path), "manifest": str(manifest_path) if save_manifest else None, "event_count": len(cat)}

@mcp.tool()
def fdsn_stations_download(
    provider: str = "IRIS",
    kwargs: Dict[str, Any] = None,
    out_format: str = "stationxml",
    outfile_prefix: Optional[str] = None,
    *,
    save_manifest: bool = True,
) -> Dict[str, Any]:
    kwargs = kwargs or {}
    cli = _client(provider)
    k = _coerce_times(kwargs)

    req_obj = {"tool": "fdsn_stations_download", "provider": provider, "kwargs": k, "out_format": out_format}
    rid = _hash_request(req_obj)

    prefix = _safe_name(outfile_prefix or f"stations_{provider}")
    ext = "xml" if out_format.lower() in ("stationxml", "xml") else "json"

    out_path = DATA_DIR / f"{prefix}_{rid}.{ext}"
    manifest_path = DATA_DIR / f"{prefix}_{rid}.manifest.json"

    t0 = time.time()
    inv = cli.get_stations(**k)
    elapsed = time.time() - t0

    _write_inventory(inv, out_path, out_format)

    if save_manifest:
        manifest = {
            "tool": "fdsn_stations_download",
            "provider": provider,
            "request_kwargs": {kk: (vv.isoformat() if isinstance(vv, UTCDateTime) else vv) for kk, vv in k.items()},
            "out_format": out_format,
            "output_file": str(out_path),
            "download_seconds": elapsed,
        }
        _write_manifest(manifest_path, manifest)

    return {"ok": True, "outfile": str(out_path), "manifest": str(manifest_path) if save_manifest else None}

@mcp.tool()
def fdsn_waveforms_download(
    provider: str = "IRIS",
    kwargs: Dict[str, Any] = None,
    out_format: str = "mseed",
    outfile_prefix: Optional[str] = None,
    *,
    dry_run: bool = False,
    override: bool = False,
    override_reason: Optional[str] = None,
    save_manifest: bool = True,
) -> Dict[str, Any]:
    kwargs = kwargs or {}

    try:
        estimation = _validate_waveform_request(
            provider, kwargs,
            override=override, override_reason=override_reason, dry_run=dry_run
        )
        if dry_run:
            return {"ok": True, "mode": "dry_run", "estimation": estimation, "limits": LIMITS.__dict__}

        cli = _client(provider)
        k = _coerce_times(kwargs)

        req_obj = {"tool": "fdsn_waveforms_download", "provider": provider, "kwargs": k, "out_format": out_format}
        rid = _hash_request(req_obj)

        prefix = _safe_name(outfile_prefix or f"waveforms_{provider}")
        ext = "mseed" if out_format.lower() in ("mseed", "miniseed") else ("sac" if out_format.lower() == "sac" else "json")

        out_path = DATA_DIR / f"{prefix}_{rid}.{ext}"
        manifest_path = DATA_DIR / f"{prefix}_{rid}.manifest.json"

        t0 = time.time()
        st = cli.get_waveforms(**k)
        elapsed = time.time() - t0

        stats = _compute_stream_stats(st)
        _post_download_enforce(
            trace_count=stats["trace_count"],
            total_samples=stats["total_samples"],
            estimated_bytes=stats["estimated_bytes"],
            override=override,
            override_reason=override_reason,
        )

        _write_stream(st, out_path, out_format)

        if save_manifest:
            manifest = {
                "tool": "fdsn_waveforms_download",
                "provider": provider,
                "request_kwargs": {kk: (vv.isoformat() if isinstance(vv, UTCDateTime) else vv) for kk, vv in k.items()},
                "out_format": out_format,
                "output_file": str(out_path),
                "limits": LIMITS.__dict__,
                "override": override,
                "override_reason": override_reason,
                "download_seconds": elapsed,
                "stream_stats": stats,
            }
            _write_manifest(manifest_path, manifest)

        traces = [{"id": tr.id, "npts": int(tr.stats.npts), "sr": float(tr.stats.sampling_rate)} for tr in st]
        return {
            "ok": True,
            "outfile": str(out_path),
            "manifest": str(manifest_path) if save_manifest else None,
            "stream_stats": stats,
            "traces_preview": traces[:20],
            "trace_preview_count": min(len(traces), 20),
        }

    except DownloadDenied as e:
        return {"ok": False, "error": "DownloadDenied", "message": str(e), "limits": LIMITS.__dict__}

@mcp.tool()
def fdsn_waveforms_bulk_download(
    provider: str = "IRIS",
    bulk_lines: List[List[Union[str, float]]] = None,
    kwargs: Dict[str, Any] = None,
    out_format: str = "mseed",
    outfile_prefix: Optional[str] = None,
    *,
    dry_run: bool = False,
    override: bool = False,
    override_reason: Optional[str] = None,
    save_manifest: bool = True,
) -> Dict[str, Any]:
    bulk_lines = bulk_lines or []
    kwargs = kwargs or {}

    try:
        estimation = _validate_bulk_request(
            provider, bulk_lines,
            override=override, override_reason=override_reason, dry_run=dry_run
        )
        if dry_run:
            return {"ok": True, "mode": "dry_run", "estimation": estimation, "limits": LIMITS.__dict__}

        cli = _client(provider)
        k = _coerce_times(kwargs)

        bulk = []
        for line in bulk_lines:
            net, sta, loc, cha, st, et = line
            bulk.append((str(net), str(sta), str(loc), str(cha), UTCDateTime(str(st)), UTCDateTime(str(et))))

        req_obj = {"tool": "fdsn_waveforms_bulk_download", "provider": provider, "bulk_lines": bulk_lines, "kwargs": k, "out_format": out_format}
        rid = _hash_request(req_obj)

        prefix = _safe_name(outfile_prefix or f"waveforms_bulk_{provider}")
        ext = "mseed" if out_format.lower() in ("mseed", "miniseed") else "json"

        out_path = DATA_DIR / f"{prefix}_{rid}.{ext}"
        manifest_path = DATA_DIR / f"{prefix}_{rid}.manifest.json"

        t0 = time.time()
        st_stream = cli.get_waveforms_bulk(bulk, **k)
        elapsed = time.time() - t0

        stats = _compute_stream_stats(st_stream)
        _post_download_enforce(
            trace_count=stats["trace_count"],
            total_samples=stats["total_samples"],
            estimated_bytes=stats["estimated_bytes"],
            override=override,
            override_reason=override_reason,
        )

        _write_stream(st_stream, out_path, out_format)

        if save_manifest:
            manifest = {
                "tool": "fdsn_waveforms_bulk_download",
                "provider": provider,
                "bulk_lines": bulk_lines,
                "request_kwargs": {kk: (vv.isoformat() if isinstance(vv, UTCDateTime) else vv) for kk, vv in k.items()},
                "out_format": out_format,
                "output_file": str(out_path),
                "limits": LIMITS.__dict__,
                "override": override,
                "override_reason": override_reason,
                "download_seconds": elapsed,
                "stream_stats": stats,
            }
            _write_manifest(manifest_path, manifest)

        traces = [{"id": tr.id, "npts": int(tr.stats.npts), "sr": float(tr.stats.sampling_rate)} for tr in st_stream]
        return {
            "ok": True,
            "outfile": str(out_path),
            "manifest": str(manifest_path) if save_manifest else None,
            "stream_stats": stats,
            "traces_preview": traces[:20],
            "trace_preview_count": min(len(traces), 20),
        }

    except DownloadDenied as e:
        return {"ok": False, "error": "DownloadDenied", "message": str(e), "limits": LIMITS.__dict__}

@mcp.tool()
def validate_only(
    request: Dict[str, Any],
    *,
    override: bool = False,
    override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate + estimate WITHOUT downloading and WITHOUT requiring a strict schema.

    request supports:
      {
        "type": "waveforms" | "waveforms_bulk" | "auto",
        "provider": "IRIS",
        "kwargs": {...},        # for waveforms
        "bulk_lines": [...],    # for bulk
        "out_format": "mseed",  # optional
        "outfile_prefix": "..." # optional
      }
    """
    try:
        if not isinstance(request, dict):
            return {"ok": False, "error": "BadRequest", "message": "request must be an object/dict."}

        provider = str(request.get("provider") or "IRIS")
        rtype = (request.get("type") or "auto").lower()

        kwargs = request.get("kwargs") or {}
        bulk_lines = request.get("bulk_lines") or None

        inferred = rtype
        if rtype == "auto":
            if bulk_lines:
                inferred = "waveforms_bulk"
            elif isinstance(kwargs, dict) and ("starttime" in kwargs or "endtime" in kwargs):
                inferred = "waveforms"
            else:
                inferred = "unknown"

        if inferred == "waveforms":
            if not isinstance(kwargs, dict):
                return {"ok": False, "error": "BadRequest", "message": "For type=waveforms, kwargs must be an object/dict."}
            estimation = _validate_waveform_request(
                provider, kwargs,
                override=override, override_reason=override_reason, dry_run=True
            )
            return {"ok": True, "inferred_type": "waveforms", "provider": provider, "estimation": estimation, "limits": LIMITS.__dict__}

        if inferred == "waveforms_bulk":
            if not isinstance(bulk_lines, list):
                return {"ok": False, "error": "BadRequest", "message": "For type=waveforms_bulk, bulk_lines must be a list."}
            estimation = _validate_bulk_request(
                provider, bulk_lines,
                override=override, override_reason=override_reason, dry_run=True
            )
            return {"ok": True, "inferred_type": "waveforms_bulk", "provider": provider, "estimation": estimation, "limits": LIMITS.__dict__}

        return {
            "ok": True,
            "inferred_type": "unknown",
            "provider": provider,
            "message": (
                "Could not infer waveform request type. Provide either request.kwargs with starttime/endtime "
                "(type=waveforms) or request.bulk_lines (type=waveforms_bulk)."
            ),
            "limits": LIMITS.__dict__,
            "received_keys": sorted(list(request.keys())),
        }

    except DownloadDenied as e:
        return {"ok": False, "error": "DownloadDenied", "message": str(e), "limits": LIMITS.__dict__}
    except Exception as e:
        return {"ok": False, "error": "ServerError", "message": str(e)}

if __name__ == "__main__":
    mcp.run()
