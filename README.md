
# ObsPy MCP

## A Modular Seismology Research Server with LLM-Driven Analysis (MCP + ObsPy + Gemini/Azure-ready)

> **Project in collaboration with University of California, Berkeley**

ObsPy MCP is a production-focused seismology research toolkit that combines:

- **ObsPy** for waveform acquisition + processing
- **FDSN web services** (IRIS/USGS/EMSC)
- **MCP (Model Context Protocol)** to expose deterministic "tools"
- **LLM research agents** (Gemini supported now; Azure OpenAI ready)

The design goal is simple: **reproducible, auditable earthquake studies** where the LLM can help with *planning and interpretation* while the **server remains deterministic** for downloads + processing.

---

## Key Features

### Data discovery + download
- Search recent earthquakes by magnitude/time/region
- Find nearby stations (network/station/channel filters)
- Download waveforms (MiniSEED) and station metadata (StationXML)
- Safe validation before downloads (time windows, size limits, channel sanity)

### Processing + analysis
- Detrend / taper
- Bandpass filter (configurable)
- Remove instrument response (requires StationXML "response" level)
- Quick P picking (basic picker)
- SNR estimation utilities
- Plot waveform quicklooks

### LLM-driven assistance
- Agent selects an event, chooses stations, downloads data, processes it
- Agent generates a scientific interpretation of observed phases
- Provider choice + knobs for reliability and cost control

---

## Project Structure

```
obspy-mcp/
├── agent/
│   ├── __init__.py
│   └── gemini_agent.py       # Gemini-powered research agent (end-to-end workflow)
│
├── server/
│   ├── __init__.py
│   ├── server.py             # MCP server entry point
│   ├── tools.py              # Tool implementations + safe JSON results
│   ├── fdsn.py               # ObsPy FDSN client wrappers
│   ├── validate.py           # Validation / estimation helpers for waveform requests
│   ├── limits.py             # Safety + usability knobs (rate/size/time limits)
│   ├── picking.py            # Simple phase picking helpers
│   ├── snr.py                # Signal-to-noise estimates
│   ├── plotting.py           # Plotting utilities (PNG quicklook)
│   ├── response_utils.py     # Pre-filter recommendations for response removal
│   └── config.py             # Central settings (env-configurable)
│
├── data/                     # Download outputs (ignored by git)
│   ├── waveforms_*.mseed
│   ├── stations_*.stationxml
│   ├── processed_*.mseed
│   └── processed_*.png
│
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

---

## Where Data Is Saved

All downloads and outputs are written into:

- `data/`

Examples:
- Waveforms: `data/waveforms_<hash>.mseed`
- StationXML: `data/stations_<hash>.stationxml`
- Processed: `data/processed_<hash>.mseed`
- Quicklook plot: `data/processed_<hash>.png`

> `data/` should remain **ignored** in `.gitignore`.

---

## Requirements

- Python **3.11+** strongly recommended
- macOS / Linux / Windows supported (macOS easiest)

---

## Installation (VS Code Friendly)

### 1) Clone repo
```bash
git clone https://github.com/Fahim-Azwad/obspy-mcp.git
cd obspy-mcp
```

### 2) Create and activate virtual environment

**macOS/Linux:**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3) Install dependencies
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4) Select interpreter in VS Code
Open Command Palette → `Python: Select Interpreter`

Choose: `.../obspy-mcp/.venv/bin/python`

---

## Configuration

### Gemini (recommended default)

**Recommended:** use a local `.env` file (loaded automatically by the agent).

```bash
cp .env.example .env
```

Then edit `.env` and set at least:

- `GOOGLE_API_KEY=...` (preferred)
  - or `GEMINI_API_KEY=...`

Optional overrides:

- `GEMINI_MODEL=models/gemini-2.5-flash` (default)
- `GEMINI_FALLBACK_MODEL=models/gemini-2.5-pro`
- `FDSN_PROVIDER=IRIS`

You can also export env vars in your shell instead of using `.env`.

### Azure OpenAI (optional / future-ready)

These are dummy placeholders (do not commit real keys):
```bash
export AZURE_ENDPOINT="https://xxx.openai.azure.com/"
export AZURE_API_KEY="xxx"
export DEPLOYMENT_NAME="gpt-5-chat"
```

---

## Running

### Run the MCP Server (manual)

This starts the tool server (stdio MCP). It is mainly used by the agent.
```bash
python -m server.server
```

> **Note:** If you run the server manually, it will wait for MCP JSON-RPC messages on stdin (so it can appear "stuck"). That is normal.

### Run the Gemini Research Agent (recommended)
```bash
python -m agent.gemini_agent -p "Find a recent magnitude 7+ earthquake and analyze BH? waveforms."
```

Optional:

```bash
python -m agent.gemini_agent -p "..." --provider IRIS
```

The agent will:
1. Find a recent M7+ event
2. Find nearby broadband stations (BH?)
3. Download waveforms + StationXML
4. Process and plot results
5. Output a scientific interpretation

---

## How the MCP Agent Fetches Data

The agent is **not** a web-scraper. It launches the MCP server locally (stdio transport) and calls deterministic MCP tools.

Pipeline in this version:

1. Start the MCP server: `python -m server.server`
2. Discover tool list via `list_tools`
3. Fetch a recent large event (last 90 days, $M\ge7$)
4. Search stations within 2° using `BH?`
5. Validate the waveform request with `validate_only`
6. Download waveforms + StationXML
7. Run `full_process` to generate processed MiniSEED + PNG quicklook
8. Ask Gemini for a **scientific interpretation** of the resulting artifacts

Note: the `--prompt` text is currently used primarily for the **final explanation**. Event/station selection follows the deterministic defaults above.

---

## MCP Tools

These are exposed by the server through MCP:

| Tool | Description |
|------|-------------|
| `search_events` | Find earthquakes by time window, magnitude, bounds |
| `search_stations` | Find stations by radius or bounds, channel patterns |
| `validate_only` | Validate + estimate a waveform request before downloading |
| `download_waveforms` | Download waveforms (MiniSEED) safely |
| `download_stations` | Download StationXML for response removal |
| `full_process` | Detrend → filter → remove response → pick → plot |

All tools return strict JSON. Errors return JSON with `ok=false` plus a readable message.

---

## Safety + Usability Knobs

The server enforces safety guardrails (configurable in `server/limits.py` and env vars):

- Max duration per request (seconds)
- Max number of channels/traces
- Max total samples / estimated bytes
- Provider fallback handling (IRIS/USGS/EMSC)
- Validation-only mode to estimate cost/size before download

These prevent accidental "download the entire internet" requests.

---

## Example Output

Typical workflow generates artifacts like:

```json
{
  "ok": true,
  "file": "data/processed_cc665600ea6c.mseed",
  "plot": "data/processed_cc665600ea6c.png",
  "picks": {
    "II.ERM.00.BHZ": "2025-12-08T14:10:30.44"
  }
}
```

You can open the PNG plot locally to inspect the trace visually.

---

## Troubleshooting

### "pip: command not found"
Use:
```bash
python -m pip install -r requirements.txt
```

### "python: command not found"
On macOS, prefer:
```bash
python3 --version
python3 -m pip --version
```

### Server looks stuck
That's expected if you run it directly. It waits for MCP messages.
Run the agent instead:
```bash
python -m agent.gemini_agent
```

### No data returned (HTTP 204)
That request window or station/channel may have no waveform coverage.
Try:
- Another station
- Larger radius
- Different channel code (e.g., `HH?` vs `BH?`)
- Slightly longer time window

---

## Roadmap (Next Improvements)

Suggested next steps (production hardening):

- [ ] Research CLI (menu-driven): user chooses event/station/download options
- [ ] TauP integration for theoretical phase arrival predictions
- [ ] Better station ranking:
  - Distance
  - Station uptime
  - Estimated SNR
- [ ] Multi-station download + aligned plotting
- [ ] Dockerfile + single-command deployment
- [ ] Azure OpenAI agent parity (optional)

---

## License

MIT — free for research and commercial use.

---

## Author
**S M Azwad Ul Alam**  
Seismology • AI Systems • Research Infrastructure

Project built at the request of **Professor Weiqiang Zhu, Department of Earth & Planetary Science, UC Berkeley**

