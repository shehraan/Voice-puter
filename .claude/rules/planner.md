# Planner Rules

## Purpose

The planner turns a transcript and observed UI state into one to three next actions.

It should be adaptive and non-deterministic, but not unconstrained. The planner may choose different action paths based on UI state, but every step must be grounded in observed controls, validated selector cache entries, or safe window-resolution primitives.

## Planner inputs

The planner receives:

```text
transcript
time
foreground window summary
target app guess
current goal state
compact UI observation
selector cache candidates
previous action result
policy state
memory-bank summary from typed resolvers
```

## Planner outputs

The planner must emit a compact action object:

```json
{
  "goal": "generic_search",
  "target_app": "resolved or guessed app",
  "visual_demo_mode": true,
  "confidence": 0.86,
  "rationale_short": "Use visible search field because it is present and enabled.",
  "actions": [
    {
      "op": "focus_control",
      "target": {
        "selector_id": "obs_12",
        "semantic_role": "search_or_input"
      }
    },
    {
      "op": "type_text",
      "args": {
        "text": "query text",
        "clear_first": true
      }
    }
  ],
  "postconditions": [
    {
      "type": "visible_state_changed",
      "description": "results appeared or target text is visible"
    }
  ]
}
```

## Non-determinism rules

Allowed:

```text
choosing between multiple valid controls
choosing command palette vs menu vs visible field
choosing whether to observe more before acting
repairing a failed selector with a different path
using memory hints to rank candidates
```

Disallowed:

```text
raw shell commands
unverified typing
pixel coordinates
blind clicking
secret reading
privileged/elevated automation
fabricated selectors
pretending a failed action succeeded
```

## Planning style

- Prefer one to three actions per planning turn.
- Observe again after meaningful UI changes.
- Stop as soon as postconditions pass.
- Use `clarify` when target app, target text, or target action is too ambiguous.
- Prefer visible action paths in demo mode even if hidden APIs would be faster.

## Generic goal types

Use these high-level goal names:

```text
open_app
focus_app
generic_search
generic_text_entry
generic_select_result
generic_click_named_control
generic_form_create
generic_form_fill
generic_form_submit
generic_build_or_run
generic_open_project_or_file
generic_read_visible_state
clarify
no_op
```

## Confidence policy

```text
>= 0.90 -> execute if safe and grounded
0.75 to 0.89 -> execute if action is reversible and visible
0.55 to 0.74 -> observe more or clarify
< 0.55 -> clarify or no_op
```

## Visual demo mode

When `visual_demo_mode=true`:

- do not bypass UI with hidden APIs
- physically open/focus apps
- visibly type user-provided text
- visibly select results or controls
- use direct APIs only for optional verification or metadata

## Repair policy

If an action fails:

1. Read the executor error.
2. Observe the current window again.
3. Try a different grounded path.
4. Keep repair budget small.
5. Stop and log if repair fails.

Never retry the exact same failed action more than once unless the UI state changed.
