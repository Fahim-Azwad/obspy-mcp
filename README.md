
# ObsPy MCP Server + LLM Research Agent (Production)

This project provides a **production-ready ObsPy MCP server** and an **LLM-powered research agent**
for automated earthquake studies.

## Features
- MCP server wrapping ObsPy FDSN (events, stations, waveforms)
- Safety-first waveform validation
- Full processing pipeline:
  - demean / detrend / taper
  - bandpass filtering
  - instrument response removal (DISP / VEL / ACC)
  - SNR-based trimming
  - P-phase picking (STA/LTA)
  - plot generation
- Deterministic outputs + provenance manifests
- LLM agent (Gemini / OpenAI / others) orchestrates studies

## Requirements
- Python 3.10+
- ObsPy
- numpy
- matplotlib
- mcp
- openai-agents (or compatible Agents SDK)

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install obspy numpy matplotlib mcp openai-agents
```

## Run MCP Server
```bash
python server/server.py
```

## Run Agent
```bash
python agent/agent.py
```

Outputs are written to `data/`.
