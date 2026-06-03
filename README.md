# voiceComputer

Local-first Windows desktop voice agent.  
Visibly operates arbitrary apps through Windows UI Automation — no hidden APIs, no pixel clicking, no per-app scripts.

## How it works

```
speak (or type) a command
  → transcript normalizer
  → goal shortlist
  → app/window resolver  (launches app if needed)
  → compact UIA tree observation
  → local LLM planner  (Ollama, qwen2.5:7b-instruct)
  → safety guardrail check
  → grounded executor  (UIA patterns + keyboard fallback)
  → postcondition verifier
  → selector cache update / repair
  → trace written to runtime/traces/
```

## Quick start

**Prerequisites**

- Windows 11, Python 3.11+
- [Ollama](https://ollama.com) installed and running (`ollama serve`)
- Model pulled: `ollama pull qwen2.5:7b-instruct`

**Install**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

**Fetch voice models** (optional — needed for push-to-talk voice mode)

```powershell
.venv\Scripts\python scripts\fetch_models.py
```

## Commands

```powershell
# Text runner (primary interface)
.venv\Scripts\python -m app.run_text "open notepad and type hello world"
.venv\Scripts\python -m app.run_text "search mechanical keyboards in my browser"
.venv\Scripts\python -m app.run_text "open my current project in codex and build it"
.venv\Scripts\python -m app.run_text "open calculator and press five plus five equals"

# Use stub planner (no Ollama needed — good for testing)
.venv\Scripts\python -m app.run_text "open notepad and type hello world" --stub

# Auto-confirm dangerous actions
.venv\Scripts\python -m app.run_text "..." --yes

# Inspect windows
.venv\Scripts\python -m app.inspect_window --app "Notepad"
.venv\Scripts\python -m app.inspect_foreground

# Voice agent (push-to-talk: hold Ctrl+Alt+Space and speak)
.venv\Scripts\python -m app.voice_agent

# Voice agent — transcribe a WAV and run (no mic needed)
.venv\Scripts\python -m app.voice_agent --wav path\to\command.wav

# Latency benchmark
.venv\Scripts\python -m app.bench_latency

# Tests (76 tests, no Windows APIs needed)
.venv\Scripts\python -m pytest tests/ -v
```

## Configuration

Key settings in `app/core/config.py`. Override via environment variables:

| Variable | Default | Description |
|---|---|---|
| `AGENT_PLANNER_MODEL` | `qwen2.5:7b-instruct` | Ollama model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama URL |
| `WHISPER_BIN` | `models/whisper-cli.exe` | whisper.cpp binary |
| `WHISPER_MODEL` | `models/ggml-base.en.bin` | whisper model |

## Project structure

```
app/
  run_text.py           text-command entrypoint
  voice_agent.py        push-to-talk voice entrypoint
  inspect_window.py     UIA inspector CLI
  inspect_foreground.py foreground window inspector
  bench_latency.py      latency benchmark
  audio/                VAD, STT, capture
  cache/                learning selector cache
  core/                 config, trace, loop orchestrator
  launch/               app resolver, app index, Win32 helpers
  memory/               memory-bank typed resolvers
  nlp/                  transcript normalizer, goal shortlist
  planner/              schema, Ollama planner, stub planner, prompt
  safety/               guardrails
  ui/                   UIA elements, observation engine, executor, selectors
  verifier/             postcondition verification
scripts/
  fetch_models.py       downloads Silero VAD + whisper.cpp assets
tests/                  76 unit tests (no real windows needed)
runtime/                traces, audio, selector cache (git-ignored)
models/                 VAD + whisper assets (git-ignored)
memory-bank/            project documentation
```

## Safety

- Hard allowlist of executor ops — no arbitrary shell, no registry, no file deletion
- Password fields, UAC prompts, and login surfaces are always refused
- Dangerous actions (delete, send, install, git-write) require confirmation
- Never fabricates selectors or pretends success
- Every command writes a full trace to `runtime/traces/`

## Generality proof

Three structurally different app categories verified through the same generic loop:

| App | Flow | Mechanism |
|---|---|---|
| Notepad (Win32) | type text | `Document` control, ValuePattern.SetValue |
| Brave (Chromium) | search + navigate | `Edit` omnibox, keyboard + Enter |
| VS Code (Electron) | open + build | `Button`/`MenuItem` invoke |
| Calculator (UWP) | arithmetic | keyboard `5+5=`, display read-back |
