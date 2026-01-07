import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from google import genai

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "models/gemini-2.5-flash")  # cheaper + fast
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "models/gemini-2.5-pro")  # stronger


INSTRUCTIONS = """
You are a seismology research assistant that MUST use the MCP tools provided.

You can:
- search_events(provider, kwargs)
- search_stations(provider, kwargs)
- validate_only(kwargs)
- download_waveforms(provider, kwargs)
- download_stations(provider, kwargs)
- full_process(waveform_file, stationxml_file)

Rules:
- Always call validate_only() before download_waveforms() unless user explicitly disables validation.
- Use conservative time windows if user gives no window.
- Prefer BH? channels for broadband.
- If a station returns no data (204), try a different station and/or widen the time window slightly.
- Save all outputs in data/ and report paths to the user.
- Explain results scientifically (phases, filtering, response removal).
"""


def get_api_key() -> str:
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing API key. Set GOOGLE_API_KEY (preferred) or GEMINI_API_KEY."
        )
    return key


def build_prompt(user_request: str, tools: Dict[str, Any]) -> str:
    return f"""
{INSTRUCTIONS}

Available MCP tools:
{json.dumps(list(tools.keys()), indent=2)}

User request:
{user_request}

Your job:
1) Decide which provider to use (IRIS/USGS/EMSC). Default IRIS unless it fails.
2) Search events that match the request.
3) Choose a good event.
4) Find stations (distance-based).
5) Validate + estimate request using validate_only().
6) Download waveforms + StationXML.
7) Run full_process() and provide:
   - output files
   - picks
   - interpretation of phases expected vs observed
Return a concise, structured final answer.
"""


async def call_tool(
    session: ClientSession, name: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    res = await session.call_tool(name, args)

    raw = ""
    if res.content and len(res.content) > 0 and hasattr(res.content[0], "text"):
        raw = (res.content[0].text or "").strip()

    if not raw:
        raise RuntimeError(f"MCP tool '{name}' returned empty output.")

    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(
            f"MCP tool '{name}' returned invalid JSON.\nRaw output:\n{raw}"
        ) from e


def genai_generate(client: genai.Client, model_name: str, prompt: str) -> str:
    resp = client.models.generate_content(model=model_name, contents=prompt)
    return (resp.text or "").strip()


def iso_window_last_n_days(days: int = 90) -> Dict[str, str]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return {"starttime": start.isoformat(), "endtime": end.isoformat()}


async def main() -> None:
    parser = argparse.ArgumentParser(description="ObsPy MCP + Gemini Agent Runner")
    parser.add_argument(
        "--prompt",
        "-p",
        required=True,
        help="Natural-language research request for the agent.",
    )
    parser.add_argument(
        "--provider",
        default=os.getenv("FDSN_PROVIDER", "IRIS"),
        help="Default FDSN provider: IRIS/USGS/EMSC (default IRIS)",
    )
    args = parser.parse_args()

    key = get_api_key()
    client = genai.Client(api_key=key)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.server"],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_resp = await session.list_tools()
            tools = {t.name: t for t in tools_resp.tools}

            required = {
                "search_events",
                "search_stations",
                "validate_only",
                "download_waveforms",
                "download_stations",
                "full_process",
            }
            missing = sorted(list(required - set(tools.keys())))
            if missing:
                raise RuntimeError(
                    f"Server is missing required MCP tools: {missing}\n"
                    f"Fix server/server.py tool registration."
                )

            user_request = f"Default provider: {args.provider}\nRequest: {args.prompt}"
            _ = build_prompt(
                user_request, tools
            )  # kept for debugging / future improvements

            window = iso_window_last_n_days(days=90)
            event_search_kwargs = {
                "starttime": window["starttime"],
                "endtime": window["endtime"],
                "minmagnitude": 7.0,
                "orderby": "time",
            }

            providers_to_try = [args.provider] + [
                p for p in ["IRIS", "USGS", "EMSC"] if p != args.provider
            ]

            events_resp: Optional[Dict[str, Any]] = None
            chosen_provider: Optional[str] = None
            for prov in providers_to_try:
                resp = await call_tool(
                    session,
                    "search_events",
                    {"provider": prov, "kwargs": event_search_kwargs},
                )
                if resp.get("ok") and (resp.get("events") or []):
                    events_resp = resp
                    chosen_provider = prov
                    break

            if not events_resp or not (events_resp.get("events") or []):
                raise RuntimeError(
                    "No M7+ events found in last 90 days from IRIS/USGS/EMSC."
                )

            provider = chosen_provider or args.provider
            chosen = events_resp["events"][0]  # newest-first
            print(
                f"\nChosen event: M{chosen.get('magnitude')} {chosen.get('description')} @ {chosen.get('time')}\n"
            )

            ev_time = chosen.get("time")
            ev_lat = chosen.get("latitude")
            ev_lon = chosen.get("longitude")
            if ev_time is None or ev_lat is None or ev_lon is None:
                raise RuntimeError(f"Chosen event missing required fields: {chosen}")

            # Station search
            stations_resp = await call_tool(
                session,
                "search_stations",
                {
                    "provider": provider,
                    "kwargs": {
                        "latitude": ev_lat,
                        "longitude": ev_lon,
                        "maxradius": 2.0,
                        "channel": "BH?",
                        "level": "station",
                    },
                },
            )
            stations = stations_resp.get("stations") or []
            if not stations:
                raise RuntimeError(
                    "No stations found within 2° (BH?). Try increasing radius."
                )

            print(
                f"Found {len(stations)} station candidates within 2° using channel=BH?\n"
            )

            # Time window: 5 min before to 30 min after
            from obspy import UTCDateTime

            t0 = UTCDateTime(ev_time)
            starttime = (t0 - 300).isoformat()
            endtime = (t0 + 1800).isoformat()

            waveform_file: Optional[str] = None
            stationxml_file: Optional[str] = None
            used_station: Optional[Dict[str, Any]] = None
            last_waveform_error: Optional[str] = None

            # Try multiple stations in case of 204/no data
            for station in stations[:25]:
                net = station.get("network")
                sta = station.get("station")
                if not net or not sta:
                    continue

                wf_kwargs = {
                    "network": net,
                    "station": sta,
                    "location": "*",
                    "channel": "BH?",
                    "starttime": starttime,
                    "endtime": endtime,
                }

                val = await call_tool(session, "validate_only", {"kwargs": wf_kwargs})
                if not val.get("ok", True):
                    print(f"❌ Validation denied for {net}.{sta}: {val.get('error')}")
                    continue

                wf = await call_tool(
                    session,
                    "download_waveforms",
                    {"provider": provider, "kwargs": wf_kwargs},
                )
                if not wf.get("ok"):
                    last_waveform_error = wf.get("error") or "unknown error"
                    short_err = (
                        last_waveform_error.splitlines()[0]
                        if last_waveform_error
                        else "unknown"
                    )
                    print(f"❌ No data for {net}.{sta} (BH?) -> {short_err}")
                    continue

                waveform_file = wf["file"]
                used_station = station
                print(
                    f"✅ Downloaded waveforms: {waveform_file} from {net}.{sta} (BH?)\n"
                )

                sx = await call_tool(
                    session,
                    "download_stations",
                    {
                        "provider": provider,
                        "kwargs": {
                            "network": net,
                            "station": sta,
                            "location": "*",
                            "channel": "BH?",
                            "level": "response",
                        },
                    },
                )
                if not sx.get("ok"):
                    raise RuntimeError(f"StationXML download failed: {sx}")
                stationxml_file = sx["file"]
                print(f"Downloaded StationXML: {stationxml_file}\n")
                break

            if not waveform_file or not stationxml_file or not used_station:
                raise RuntimeError(
                    "Waveform download failed for all station candidates."
                    + (
                        f"\nLast error:\n{last_waveform_error}"
                        if last_waveform_error
                        else ""
                    )
                )

            processed = await call_tool(
                session,
                "full_process",
                {"waveform_file": waveform_file, "stationxml_file": stationxml_file},
            )

            print("Processing summary:")
            print(json.dumps(processed, indent=2))

            net = used_station["network"]
            sta = used_station["station"]
            explain_prompt = f"""
Event:
{json.dumps(chosen, indent=2)}

Station: {net}.{sta}
Waveform file: {waveform_file}
StationXML file: {stationxml_file}
Processed output:
{json.dumps(processed, indent=2)}

User request:
{args.prompt}

Explain:
- expected P/S/surface arrivals for this geometry
- what filtering + response removal does
- what should be checked next scientifically
Keep it clear and seismology-correct.
"""

            try:
                explanation = genai_generate(client, DEFAULT_MODEL, explain_prompt)
            except Exception:
                explanation = genai_generate(client, FALLBACK_MODEL, explain_prompt)

            print("\n===== GEMINI SCIENTIFIC INTERPRETATION =====\n")
            print(explanation)


if __name__ == "__main__":
    asyncio.run(main())
