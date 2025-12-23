"""
agent.py — LLM agent that talks to src/server.py via MCP (stdio)

Run:
  python src/agent.py

Notes:
- Requires OpenAI Agents SDK installed.
- Requires OPENAI_API_KEY (or the key env var your SDK expects).
"""

import asyncio
import json
from typing import Any, Dict

from agents import Agent, Runner
from agents.mcp import MCPServerStdio
from agents.model_settings import ModelSettings

INSTRUCTIONS = """
You are a seismology data assistant.

You can call MCP tools to:
- validate_only (validate & estimate without downloading)
- fdsn_events_download
- fdsn_stations_download
- fdsn_waveforms_download
- fdsn_waveforms_bulk_download

Rules:
1) For any waveform request, call validate_only first.
2) If validate_only returns DownloadDenied, reduce request size (shorter time window, fewer traces).
3) Only use override=true if user explicitly wants it, and include override_reason.
4) Always return file paths and mention that a manifest JSON was saved next to outputs.
"""

def build_demo_download_spec() -> Dict[str, Any]:
    """
    Researcher-editable example.
    """
    return {
        "events": {
            "provider": "USGS",
            "kwargs": {
                "starttime": "2025-08-01T00:00:00",
                "endtime": "2025-09-01T00:00:00",
                "minmagnitude": 7.0,
                "limit": 10
            },
            "format": "quakeml"
        },
        "stations": {
            "provider": "IRIS",
            "kwargs": {
                "network": "*",
                "station": "*",
                "location": "*",
                "channel": "BH?",
                "level": "response",
                "latitude": -57.0,
                "longitude": -26.0,
                "maxradiuskm": 6000
            },
            "format": "stationxml"
        },
        "waveforms": [
            {
                "provider": "IRIS",
                "kwargs": {
                    "network": "IU",
                    "station": "ANMO",
                    "location": "00",
                    "channel": "BH?",
                    "starttime": "2025-08-22T02:10:00",
                    "endtime": "2025-08-22T02:40:00"
                },
                "format": "mseed"
            }
        ]
    }

async def main():
    spec = build_demo_download_spec()

    user_prompt = f"""
Execute this download spec JSON using MCP tools.
For each waveform request, call validate_only first.
If validate_only denies, shrink the request and retry.
Return output file paths and manifest paths.

DOWNLOAD_SPEC_JSON:
{json.dumps(spec, indent=2)}
"""

    async with MCPServerStdio(
        name="ObsPy-FDSN",
        params={"command": "python", "args": ["src/server.py"]},
        cache_tools_list=True,
    ) as server:
        agent = Agent(
            name="QuakeResearchAssistant",
            instructions=INSTRUCTIONS,
            mcp_servers=[server],
            model_settings=ModelSettings(tool_choice="required"),
        )
        result = await Runner.run(agent, user_prompt)
        print(result.final_output)

if __name__ == "__main__":
    asyncio.run(main())
