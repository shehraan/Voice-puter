# Testing Rules

## Required tests

Create tests for:

```text
transcript normalization
goal shortlisting
planner schema validation
selector scoring
window resolution
UI observation compression
guardrails
mocked generic search flow
mocked generic text-entry flow
mocked generic form-fill flow
mocked generic build/run flow
selector cache repair
postcondition verification
```

## Manual smoke tests

Run these manually on Windows:

```text
python -m app.inspect_foreground
python -m app.inspect_window --app "Notepad"
python -m app.run_text "open notepad and type hello world"
python -m app.run_text "open calculator and press five plus five equals"
python -m app.run_text "open my current project in codex and build it"
python -m app.run_text "search janice stfu in my music app and play the best result"
python -m app.bench_latency
```

## Demo acceptance test

A generic app interaction test passes only if the user can physically see:

```text
target app opens or focuses
relevant visible control is selected
text is typed visibly when needed
result/menu/form appears
final action is invoked visibly
visible postcondition changes
```

A hidden API-only result fails the demo test.

## Logging acceptance test

Every command should produce a trace file with:

```text
trace_id
transcript
goal
observations
control candidates
selector scores
actions
postconditions
latency
result
```

## Failure behavior

Failures must be visible and recoverable.

The agent should say or log what failed:

```text
Could not find a visible search-like control in the target app. Tried compact UIA view, broader UIA view, and verified hotkey fallback.
```

Do not pretend success.
