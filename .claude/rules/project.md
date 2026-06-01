# CLAUDE.md

## Project

This repo builds a Windows-first, local-first desktop voice agent inspired by fast desktop voice-control demos.

The agent converts speech into a flexible UI automation goal, observes the active desktop app, then visibly controls the app using Windows UI Automation. The system must not be built around preconfigured app scripts. It should work across arbitrary Windows desktop apps by discovering windows, controls, text fields, buttons, menus, results, toolbars, and supported control patterns at runtime.

The viewer should physically see the desktop being operated: apps opening, fields focusing, text being typed, menus opening, results appearing, buttons being invoked, and the final state being verified on screen.

## Non-negotiables

- Windows is the target platform.
- Visible UI Automation is the primary demo path.
- The agent must not depend on one app-specific adapter as the main product.
- Do not use hidden APIs to skip visible UI interaction during demo flows.
- The planner may be adaptive and non-deterministic.
- Low-level actions must be grounded in observed UI elements or validated selector-cache entries.
- Never rely on raw pixel coordinates.
- Never click or type into a window unless the target window and focused control are verified.
- The generic UI agent must discover controls at runtime using window metadata, UIA trees, control names, automation IDs, control types, bounding rectangles, enabled state, visible state, and supported patterns.
- Optional app hints are allowed only as metadata, not as hardcoded workflows.

## Core architecture files

Read these before changing implementation:

- @.claude/rules/architecture.md
- @.claude/rules/ui-automation-visible.md
- @.claude/rules/generic-app-automation.md
- @.claude/rules/planner.md
- @.claude/rules/security.md
- @.claude/rules/performance.md
- @.claude/rules/testing.md

## Build philosophy

Move fast, but keep the system inspectable.

The product should feel magical because it adapts to arbitrary apps, not because it secretly uses prebuilt APIs. The technical signal is:

- fast speech pipeline
- small planner
- dynamic UI tree grounding
- selector scoring
- action repair
- visible execution
- postcondition verification
- clean safety boundaries

## Preferred stack

- Language: Python 3.11+
- Desktop automation: pywinauto with `backend="uia"`
- Deeper UIA access: optional `uiautomation` Python package
- Win32 helpers: pywin32 for process, window, foreground, and focus utilities
- STT: whisper.cpp first, faster-whisper only if Python integration speed matters more
- VAD: Silero VAD through ONNX Runtime
- Local planner runtime: Ollama first, llama.cpp later for tighter latency and JSON constraints
- Planner model: Qwen2.5 3B Instruct, Qwen2.5-Coder 3B Instruct, Phi-3-mini, or another small local instruct model
- Memory: external memory bank handled separately by the user

## Commands to prefer

Use these command names when creating scripts:

```text
python -m app.voice_agent
python -m app.run_text "open notepad and type hello world"
python -m app.run_text "open my current project in codex and build it"
python -m app.inspect_window --app "Notepad"
python -m app.inspect_foreground
python -m app.bench_latency
python -m pytest tests/
```

## What success looks like

A recruiter or founder watching the demo should see:

1. User speaks a vague command.
2. The desktop reacts quickly.
3. The requested app opens or focuses.
4. The agent inspects visible controls.
5. It searches, clicks, types, selects, opens menus, and verifies visibly.
6. If the UI changes, it repairs itself instead of failing silently.

## What failure looks like

Fail loudly and usefully.

A failed command should produce a trace explaining:

- what window was targeted
- what UI tree was observed
- which controls were considered
- which control was selected
- what action failed
- what repair attempts were tried
- why the system stopped

Never pretend success.
