import asyncio
import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple

from google import genai

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


# ---------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# If your server supports other providers, keep them here
PROVIDERS = ["IRIS"]  # you can add "RESIF", "GFZ" later if your server supports them

DEFAULT_PROVIDER = PROVIDERS[0]

# Use a Flash model to avoid "2.5-pro" free tier quota errors
MODEL_NAME = "models/gemini-2.5-flash"


# ---------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------
def require_env_key() -> str:
    """
    Resolve Gemini API key.
    Prefers GOOGLE_API_KEY if both are set.
    """
    google_key = os.getenv("GOOGLE_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if google_key and gemini_key:
        print("Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY.")
        return google_key

    if google_key:
        return google_key

    if gemini_key:
        return gemini_key

    raise RuntimeError("No Gemini API key found. Set GOOGLE_API_KEY or GEMINI_API_KEY.")


# ---------------------------------------------------------------------
# Tool calling helpers (robust)
# ---------------------------------------------------------------------
async def call_tool(session: ClientSession, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call an MCP tool and safely parse JSON output.
    """
    result = await session.call_tool(name, args)

    if not result or not result.content:
        raise RuntimeError(f"MCP tool '{name}' returned no content. Args: {args}")

    raw = result.content[0].text
    if not raw or not raw.strip():
        raise RuntimeError(f"MCP tool '{name}' returned empty output. Args: {args}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"MCP tool '{name}' returned invalid JSON.\nRaw output:\n{raw}"
        ) from e


def extract_json(text: str) -> Dict[str, Any]:
    """
    Extract the first JSON object from model output.
    """
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"No JSON object found in Gemini output:\n{text}")
    return json.loads(match.group(0))


def parse_event_time_iso(iso_str: str) -> datetime:
    """
    Convert ISO string returned by tools into a timezone-aware datetime.
    """
    # Some sources may not include 'Z' but already include offset
    s = iso_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------
# Event + station discovery
# ---------------------------------------------------------------------
def recent_large_event_window(days_back: int = 60, minmag: float = 7.0) -> Dict[str, Any]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    return {
        "starttime": start.isoformat(),
        "endtime": end.isoformat(),
        "minmagnitude": float(minmag),
        "orderby": "time",
    }


async def find_event(session: ClientSession, provider: str) -> Dict[str, Any]:
    resp = await call_tool(
        session,
        "search_events",
        {"provider": provider, "kwargs": recent_large_event_window(days_back=60, minmag=7.0)},
    )
    if not resp.get("ok"):
        raise RuntimeError(f"search_events failed: {resp}")
    events = resp.get("events") or []
    if not events:
        raise RuntimeError("No magnitude 7+ events found in last 60 days.")
    return events[0]


async def find_station_candidates(
    session: ClientSession,
    provider: str,
    lat: float,
    lon: float,
    channel: str,
    radii: List[int],
    limit: int = 200,
) -> List[Dict[str, Any]]:
    for r in radii:
        resp = await call_tool(
            session,
            "search_stations",
            {
                "provider": provider,
                "kwargs": {
                    "latitude": lat,
                    "longitude": lon,
                    "maxradius": r,
                    "channel": channel,
                    "level": "station",
                },
            },
        )
        if resp.get("ok") and (resp.get("stations") or []):
            stations = resp["stations"][:limit]
            print(f"Found {len(stations)} station candidates within {r}° using channel={channel}")
            return stations
    return []


def prioritize_networks(stations: List[Dict[str, Any]], preferred=("IU", "II")) -> List[Dict[str, Any]]:
    pref = [s for s in stations if s.get("network") in preferred]
    other = [s for s in stations if s.get("network") not in preferred]
    return pref + other


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
async def main():
    api_key = require_env_key()
    client = genai.Client(api_key=api_key)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.server"],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ---------------------------------------------------------
            # 1) Choose provider + find a recent large earthquake
            # ---------------------------------------------------------
            chosen_event = None
            chosen_provider = None

            for prov in PROVIDERS:
                try:
                    ev = await find_event(session, prov)
                    chosen_event = ev
                    chosen_provider = prov
                    break
                except Exception as e:
                    print(f"Provider {prov} failed to find event: {e}")

            if not chosen_event:
                raise RuntimeError("Failed to find a recent large event from all providers.")

            event = chosen_event
            provider = chosen_provider or DEFAULT_PROVIDER

            print(
                f"\nChosen event: M{event.get('magnitude')} "
                f"{event.get('description')} @ {event.get('time')}\n"
            )

            if event.get("latitude") is None or event.get("longitude") is None or not event.get("time"):
                raise RuntimeError(f"Event is missing required fields: {event}")

            ev_lat = float(event["latitude"])
            ev_lon = float(event["longitude"])
            ev_dt = parse_event_time_iso(event["time"])

            # Default analysis window: -5 min to +25 min around origin
            start_iso = (ev_dt - timedelta(minutes=5)).isoformat()
            end_iso = (ev_dt + timedelta(minutes=25)).isoformat()

            # ---------------------------------------------------------
            # 2) Find stations near the event + retry waveform download
            # ---------------------------------------------------------
            radii = [2, 5, 10, 20, 30, 40]
            channels_to_try = ["BH?", "HH?"]

            waveform_file: Optional[str] = None
            waveform_kwargs_used: Optional[Dict[str, Any]] = None
            last_error: Optional[str] = None

            # Try each channel strategy
            for chan in channels_to_try:
                stations = await find_station_candidates(session, provider, ev_lat, ev_lon, chan, radii)
                stations = prioritize_networks(stations)

                if not stations:
                    print(f"No stations found for channel={chan}. Trying next channel...")
                    continue

                # Try up to 30 station candidates
                for s in stations[:30]:
                    wf_kwargs = {
                        "network": s["network"],
                        "station": s["station"],
                        "location": "*",
                        "channel": chan,
                        "starttime": start_iso,
                        "endtime": end_iso,
                    }

                    wf = await call_tool(
                        session,
                        "download_waveforms",
                        {"provider": provider, "kwargs": wf_kwargs},
                    )

                    if wf.get("ok"):
                        waveform_file = wf["file"]
                        waveform_kwargs_used = wf_kwargs
                        print(f"✅ Downloaded waveforms: {waveform_file} from {s['network']}.{s['station']} ({chan})")
                        break

                    last_error = wf.get("error") or "unknown error"
                    # Print short reason
                    short_err = last_error.splitlines()[0] if last_error else "unknown"
                    print(f"❌ No data for {s['network']}.{s['station']} ({chan}) -> {short_err}")

                if waveform_file:
                    break

            if not waveform_file or not waveform_kwargs_used:
                raise RuntimeError(
                    f"Waveform download failed for all station candidates.\nLast error:\n{last_error}"
                )

            # ---------------------------------------------------------
            # 3) Download StationXML (response) for the chosen station
            # ---------------------------------------------------------
            stationxml_kwargs = {
                "network": waveform_kwargs_used["network"],
                "station": waveform_kwargs_used["station"],
                "level": "response",
            }

            sta = await call_tool(
                session,
                "download_stations",
                {"provider": provider, "kwargs": stationxml_kwargs},
            )

            if not sta.get("ok"):
                raise RuntimeError(f"StationXML download failed: {sta}")

            stationxml_file = sta["file"]
            print("Downloaded StationXML:", stationxml_file)

            # ---------------------------------------------------------
            # 4) Full processing
            # ---------------------------------------------------------
            processed = await call_tool(
                session,
                "full_process",
                {
                    "waveform_file": waveform_file,
                    "stationxml_file": stationxml_file,
                },
            )

            if not processed.get("ok"):
                raise RuntimeError(f"full_process failed: {processed}")

            print("\nProcessing summary:")
            print(json.dumps(processed, indent=2))

            # ---------------------------------------------------------
            # 5) Scientific explanation with Gemini
            # ---------------------------------------------------------
            explain_prompt = f"""
We processed seismic waveforms for this earthquake.

Event:
{json.dumps(event, indent=2)}

Waveform request used:
{json.dumps(waveform_kwargs_used, indent=2)}

Processing results:
{json.dumps(processed, indent=2)}

Explain:
1) Expected seismic phases (P, S, surface waves)
2) Effect of filtering and response removal
3) What additional analyses should be done next

Be concise, practical, and scientifically accurate.
"""

            resp2 = client.models.generate_content(
                model=MODEL_NAME,
                contents=explain_prompt,
            )

            print("\n===== GEMINI SCIENTIFIC INTERPRETATION =====\n")
            print(resp2.text or "")


if __name__ == "__main__":
    asyncio.run(main())
