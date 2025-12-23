from __future__ import annotations

from typing import Any, Dict
from mcp.server.fastmcp import FastMCP

# Import tools with safe names (avoid shadowing tool function names)
import server.tools as tools
from server.validate import validate_waveforms

mcp = FastMCP("ObsPy-MCP")


# ---------------------------------------------------------------------
# MCP Tools (names MUST match what the agent calls)
# ---------------------------------------------------------------------
@mcp.tool(name="search_events")
def tool_search_events(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return tools.search_events(provider, kwargs)


@mcp.tool(name="search_stations")
def tool_search_stations(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return tools.search_stations(provider, kwargs)


@mcp.tool(name="download_waveforms")
def tool_download_waveforms(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return tools.download_waveforms(provider, kwargs)


@mcp.tool(name="download_stations")
def tool_download_stations(provider: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    return tools.download_stations(provider, kwargs)


@mcp.tool(name="full_process")
def tool_full_process(waveform_file: str, stationxml_file: str) -> Dict[str, Any]:
    return tools.full_process(waveform_file, stationxml_file)


@mcp.tool(name="validate_only")
def tool_validate_only(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate + estimate without requiring strict waveform kwargs structure.
    Useful when researchers give partial / arbitrary specs.

    Returns:
      { ok: bool, error?: str, normalized?: dict, info?: any }
    """
    try:
        ok, info = validate_waveforms(kwargs)
        if not ok:
            return {"ok": False, "error": info}
        return {"ok": True, "normalized": kwargs, "info": info}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
