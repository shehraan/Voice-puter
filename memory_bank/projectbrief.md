# Project Brief

## Goal

Build a local-first Windows desktop voice agent that visibly operates arbitrary desktop
apps through Windows UI Automation (UIA).

The user speaks (or types) a command. The agent observes the target app's live UIA
control tree, creates a grounded action plan, executes it visibly on screen, verifies the
result, caches successful selectors, and repairs stale ones.

## Core requirement

Visible UI Automation is the primary demo path. No hidden APIs, no hardcoded per-app
scripts, no pixel coordinates.

## Scope (v1)

- Generic text-entry, generic search-and-select, generic build/run
- Works across: text editors, browsers, media apps, IDEs, calendar/form apps
- Full voice front-end: push-to-talk → Silero VAD → whisper.cpp STT → loop
- Local planner: Ollama with qwen2.5:7b-instruct (swappable)
- Learning selector cache with repair
- Safety guardrails with confirmation gates

## Non-goals (v1)

- OCR / computer vision / pixel clicking
- App-specific workflow scripts
- Remote cloud execution
- Payment / purchase forms
