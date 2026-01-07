"""Gemini-powered research agent that drives ObsPy MCP tools.

This script:
- Starts the local MCP server over stdio
- Calls deterministic tools to fetch/process seismic data
- Uses Gemini to generate a scientific interpretation of results
"""

# Standard library: CLI parsing, async, JSON, env vars, and time helpers.
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Gemini SDK (Google GenAI) for the final scientific narrative.
from google import genai

# Loads environment variables from a local .env file (keeps secrets out of git).
from dotenv import load_dotenv

# MCP client: talk to the local server subprocess via stdin/stdout.
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


# Resolve the repository root (used for cwd when starting the MCP server).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load local environment variables so secrets stay in .env, not git.
load_dotenv(PROJECT_ROOT / ".env")

# Model selection: default (fast/cheap) + fallback (stronger) if the default fails.
DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL", "models/gemini-2.5-flash"
)  # fast/cheap default
FALLBACK_MODEL = os.getenv(
    "GEMINI_FALLBACK_MODEL", "models/gemini-2.5-pro"
)  # stronger backup


# High-level agent rules (used as a prompt template / guardrails).
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
    """Get Gemini API key from environment.

    Prefers GOOGLE_API_KEY (new convention) but accepts GEMINI_API_KEY.
    """
    key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "Missing API key. Set GOOGLE_API_KEY (preferred) or GEMINI_API_KEY."
        )
    return key


def build_prompt(user_request: str, tools: Dict[str, Any]) -> str:
    """Build a tool-aware prompt template.

    Note: In this version the pipeline is mostly deterministic; this is kept
    for debugging/future extension where Gemini drives tool selection.
    """
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
    """Call an MCP tool and parse its JSON response.

    Our MCP server returns JSON as text; this validates that expectation.
    """
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
    """Generate a plain-text response using the Gemini model."""
    resp = client.models.generate_content(model=model_name, contents=prompt)
    return (resp.text or "").strip()


def iso_window_last_n_days(days: int = 90) -> Dict[str, str]:
    """Create an ISO8601 start/end window for recent-event searches."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return {"starttime": start.isoformat(), "endtime": end.isoformat()}


async def main() -> None:
    """Main entrypoint.

    Runs an end-to-end workflow:
    event search -> station search -> validate -> download -> process -> explain.
    """
    # CLI arguments let you provide a natural-language request and a provider hint.
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

    # Initialize Gemini client for scientific interpretation at the end.
    key = get_api_key()
    client = genai.Client(api_key=key)

    # Start MCP server as a subprocess (stdio transport) using the same interpreter.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "server.server"],
        cwd=str(PROJECT_ROOT),
    )

    # Connect to the MCP server over stdin/stdout.
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # MCP handshake.
            await session.initialize()

            # Discover available tool names.
            tools_resp = await session.list_tools()
            tools = {t.name: t for t in tools_resp.tools}

            # Fail fast if the server doesn't expose the tools we depend on.
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

            # Build a debug prompt template (not executed as an LLM-driven tool planner yet).
            user_request = f"Default provider: {args.provider}\nRequest: {args.prompt}"
            _ = build_prompt(user_request, tools)

            # Deterministic event search: last 90 days, magnitude >= 7.
            window = iso_window_last_n_days(days=90)
            event_search_kwargs = {
                "starttime": window["starttime"],
                "endtime": window["endtime"],
                "minmagnitude": 7.0,
                "orderby": "time",
            }

            # Provider fallback strategy (try requested provider first).
            providers_to_try = [args.provider] + [
                p for p in ["IRIS", "USGS", "EMSC"] if p != args.provider
            ]

            # Find at least one matching event from the first provider that returns results.
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

            # Select the newest event (API returns newest-first).
            provider = chosen_provider or args.provider
            chosen = events_resp["events"][0]
            print(
                f"\nChosen event: M{chosen.get('magnitude')} {chosen.get('description')} @ {chosen.get('time')}\n"
            )

            ev_time = chosen.get("time")
            ev_lat = chosen.get("latitude")
            ev_lon = chosen.get("longitude")
            if ev_time is None or ev_lat is None or ev_lon is None:
                raise RuntimeError(f"Chosen event missing required fields: {chosen}")

            # Find nearby broadband stations (BH? within 2 degrees).
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

            # Build a waveform time window: 5 minutes before -> 30 minutes after event.
            from obspy import UTCDateTime

            t0 = UTCDateTime(ev_time)
            starttime = (t0 - 300).isoformat()
            endtime = (t0 + 1800).isoformat()

            waveform_file: Optional[str] = None
            stationxml_file: Optional[str] = None
            used_station: Optional[Dict[str, Any]] = None
            last_waveform_error: Optional[str] = None

            # Try a few stations in case some return no data (HTTP 204).
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

                # Validate before downloading (enforces safety limits on the server).
                val = await call_tool(session, "validate_only", {"kwargs": wf_kwargs})
                if not val.get("ok", True):
                    print(f"❌ Validation denied for {net}.{sta}: {val.get('error')}")
                    continue

                # Download waveforms (MiniSEED).
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

                # Download StationXML at response level (required for response removal).
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

            # Deterministic processing pipeline on the server (detrend/filter/response/pick/plot).
            processed = await call_tool(
                session,
                "full_process",
                {"waveform_file": waveform_file, "stationxml_file": stationxml_file},
            )

            print("Processing summary:")
            print(json.dumps(processed, indent=2))

            net = used_station["network"]
            sta = used_station["station"]
            # Ask Gemini to interpret the processed artifacts scientifically.
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

            # Model fallback improves reliability under quota/model errors.
            try:
                explanation = genai_generate(client, DEFAULT_MODEL, explain_prompt)
            except Exception:
                explanation = genai_generate(client, FALLBACK_MODEL, explain_prompt)

            print("\n===== GEMINI SCIENTIFIC INTERPRETATION =====\n")
            print(explanation)


if __name__ == "__main__":
    # Run the async pipeline when executed as a script.
    asyncio.run(main())
