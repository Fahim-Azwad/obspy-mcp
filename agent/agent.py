import asyncio
import sys
from pathlib import Path

from agents import Agent, Runner
from agents.mcp import MCPServerStdio


import os

if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY is not set")

# ---------------------------------------------------------------------
# Project root resolution (important for package imports)
# ---------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]


INSTRUCTIONS = """
You are a seismology research assistant.

Workflow:
1) Search for relevant earthquakes.
2) Validate waveform requests before downloading.
3) Download waveforms safely.
4) Process waveforms (filter, remove response, pick phases).
5) Explain results scientifically.

Never override safety limits unless explicitly requested.
"""


async def main():
    """
    Main async entrypoint for the LLM-driven research agent.
    """

    async with MCPServerStdio(
        name="ObsPy-MCP",
        params={
            "command": sys.executable,        # use venv python
            "args": ["-m", "server.server"], # module mode (CRITICAL)
            "cwd": str(PROJECT_ROOT),         # project root (CRITICAL)
        },
    ) as server:

        agent = Agent(
    name="EarthquakeResearchAgent",
    instructions=INSTRUCTIONS,
    mcp_servers=[server],
    model="gemini:gemini-1.5-pro",   # 👈 THIS is the key
)



        result = await Runner.run(
            agent,
            "Find a recent magnitude 7+ earthquake, download broadband "
            "waveforms from a nearby station, process them, and explain "
            "the seismic phases observed."
        )

        print("\n===== AGENT OUTPUT =====\n")
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
