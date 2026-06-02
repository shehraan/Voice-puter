# Research Notes

## Why Windows UI Automation

Windows UI Automation is the correct foundation for this project because the demo goal is visible operation of desktop apps. The system should read the UI tree, identify controls, invoke patterns, type into fields, and verify visible state changes.

## Why visible UIA instead of hidden APIs

For reliability, direct app APIs are often better. For this project, the demo goal is different: show a founder or recruiter that the system can operate real desktop software. Therefore, UIA is the main action path. Hidden APIs may only be used for optional verification, metadata lookup, or non-demo fallback.

## Why not app-specific adapters

Hardcoded adapters look impressive for one demo but fail the outreach goal. The stronger project is a general UI agent:

```text
runtime UI tree discovery
semantic selector matching
action verification
selector learning
repair after UI changes
```

## The key insight

The agent should not be a freeform mouse-clicker. It should be a non-deterministic planner over a constrained action vocabulary grounded in observed UI controls.

## What makes it fast

Speed comes from:

```text
VAD before STT
short transcripts
small planner model
compact UI observations
selector caching
one to three actions per planning turn
foreground verification
early stop after postconditions pass
```

## What makes it general

Generality comes from:

```text
semantic role matching
UIA control pattern detection
selector scoring
runtime tree inspection
validated selector cache
repair loops
optional app metadata instead of hardcoded scripts
```
