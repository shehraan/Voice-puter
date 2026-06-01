# Architecture: Generic Windows Visible UI Voice Agent

## Goal

Build a Windows desktop voice agent that can visibly operate arbitrary desktop apps through Windows UI Automation.

The system must not be a collection of hidden API integrations. It must observe the app UI, infer what controls are useful, execute grounded visible actions, verify the resulting state, and learn reusable selectors over time.

## Design principle

Put non-determinism in planning and repair. Keep low-level action primitives constrained and observable.

The planner can choose different workflows for the same command based on current UI state. It can revise the plan after observing a window. The executor only accepts a small set of safe UI primitives.

## High-level pipeline

```text
Push-to-talk / wake trigger
  -> audio ring buffer
  -> VAD
  -> streaming STT
  -> transcript normalizer
  -> fast goal shortlist
  -> non-deterministic planner
  -> UI observation request
  -> grounded action plan
  -> visible UIA execution
  -> observe changed UI state
  -> postcondition verification
  -> selector cache update
  -> memory/log write
```

## Runtime loop

Use an observe-act-observe loop instead of one giant plan.

```text
1. Hear command.
2. Convert command into a high-level goal.
3. Resolve likely target app/window.
4. Observe target window using UIA.
5. Compress UI tree into actionable candidates.
6. Planner chooses the next one to three actions.
7. Executor grounds semantic actions into specific UI elements.
8. Executor performs visible UIA actions.
9. Observe the new UI state.
10. Verify progress.
11. Continue, repair, clarify, or stop.
```

This is what makes the agent feel non-deterministic. It is not locked to one hardcoded script. It adapts to the UI tree it sees at runtime.

## Runtime components

### 1. Audio service

Responsibilities:

- Capture microphone audio.
- Maintain a short ring buffer.
- Detect speech start and end using VAD.
- Send finalized or partial chunks to STT.
- Support push-to-talk first for reliability.

Implementation target:

```text
app/audio/capture.py
app/audio/vad.py
app/audio/stt.py
```

Output contract:

```json
{
  "event": "speech_segment_finalized",
  "trace_id": "uuid",
  "pcm_path": "runtime/audio/trace.wav",
  "started_at_ms": 0,
  "ended_at_ms": 1290
}
```

### 2. Speech-to-text

Responsibilities:

- Convert command speech into text with low latency.
- Return partial hypotheses when available.
- Normalize casing, filler words, app names, and short imperative phrases.

Preferred engines:

- `whisper.cpp` for fast local binary execution.
- `faster-whisper` only if Python integration is simpler for first prototype.

Output contract:

```json
{
  "trace_id": "uuid",
  "text": "open notepad and type hello world",
  "confidence": 0.91,
  "started_at_ms": 0,
  "finalized_at_ms": 620
}
```

### 3. Goal shortlist

Responsibilities:

- Avoid calling the planner when the command is obvious.
- Use regex and embeddings to shortlist likely task categories.
- Provide hints to the planner, not final actions.

Examples:

```text
"open ..." -> open_app
"search ... in ..." -> generic_search
"type ..." -> generic_text_entry
"build ..." -> generic_build_or_run
"create event ..." -> generic_form_create
"click ..." -> generic_click_named_control
```

This layer is allowed to be heuristic because the planner still grounds actions against the observed UI tree.

### 4. Planner

Responsibilities:

- Convert transcript plus current UI observation into a next-action plan.
- Operate adaptively and non-deterministically within constraints.
- Prefer visible UIA actions when `visual_demo_mode=true`.
- Never invent unsafe tools.
- Never use pixel coordinates.
- Never assume a control exists before observation.

Planner output contract:

```json
{
  "goal": "generic_search_and_open_result",
  "target_app": {
    "name_or_alias": "user requested app name",
    "resolution_status": "resolved|needs_resolution|foreground"
  },
  "visual_demo_mode": true,
  "confidence": 0.86,
  "actions": [
    {
      "op": "ensure_window",
      "args": {
        "app_name": "resolved app name or alias",
        "launch_hint": "optional launch hint"
      }
    },
    {
      "op": "observe_window",
      "args": {
        "scope": "active_target_window"
      }
    },
    {
      "op": "find_control",
      "args": {
        "semantic_role": "search_or_input",
        "preferred_control_types": ["Edit", "Document", "ComboBox", "Button", "MenuItem"],
        "name_contains_any": ["Search", "Find", "Open", "Type", "Ask", "Command"]
      }
    },
    {
      "op": "type_text",
      "args": {
        "text": "user text payload",
        "clear_first": true
      }
    }
  ],
  "postconditions": [
    {
      "type": "visible_text_contains_or_state_changed",
      "contains_any": ["expected visible text or state"]
    }
  ]
}
```

### 5. Window resolver

Responsibilities:

- Enumerate top-level windows.
- Match target app names and aliases against process names, titles, executable names, installed app index, and visible text.
- Launch the app if needed.
- Focus the app without stealing focus unpredictably.
- Confirm the foreground window is the intended target before typing or sending hotkeys.

Resolution sources:

```text
foreground window
running processes
top-level window titles
installed app index
Start menu shortcuts
known executable paths
user memory aliases
recent successful app resolutions
```

### 6. UI observation engine

Responsibilities:

- Dump a compact UIA tree for the target window.
- Prioritize actionable controls.
- Avoid sending giant raw trees into the planner.
- Include enough context for selector repair.

Fields to collect per element:

```text
runtime_id
name
automation_id
control_type
class_name
localized_control_type
bounding_rectangle
is_enabled
is_offscreen
has_keyboard_focus
supported_patterns
parent_summary
children_summary
sibling_summary
```

Observation output contract:

```json
{
  "trace_id": "uuid",
  "window": {
    "title": "Example App",
    "process": "example.exe",
    "handle": "0x00000000",
    "is_foreground": true
  },
  "actionable_controls": [
    {
      "selector_id": "obs_12",
      "name": "Search",
      "control_type": "Edit",
      "automation_id": "SearchBox",
      "is_enabled": true,
      "is_offscreen": false,
      "supported_patterns": ["ValuePattern", "TextPattern"]
    }
  ]
}
```

### 7. UI action executor

The executor exposes only safe primitives:

```text
ensure_window(app_name, launch_hint?)
focus_window(window_selector)
observe_window(window_selector)
find_control(query)
focus_control(selector)
invoke(selector)
set_value(selector, text)
type_text(selector?, text, clear_first)
send_hotkey(keys, target_window_required=true)
select_item(selector)
double_click_element(selector)
wait_for(condition, timeout_ms)
verify(condition)
cache_selector(app, semantic_role, selector)
repair_selector(app, failed_selector, desired_semantic_role)
clarify(message)
stop(reason)
```

Do not expose arbitrary shell execution to the planner.

### 8. Selector cache

Purpose:

Make repeated actions faster while keeping first-run behavior dynamic.

Storage example:

```json
{
  "ExampleApp": {
    "search_or_command_input": {
      "automation_id": "SearchBox",
      "name_regex": "Search|Find|Command|Ask",
      "control_type": "Edit",
      "supported_patterns": ["ValuePattern"],
      "fallback_path": ["Window", "Pane", "Edit"],
      "last_verified_at": "2026-06-03T21:00:00Z",
      "success_count": 8,
      "failure_count": 1
    }
  }
}
```

Selector cache rules:

- Cache selectors only after successful postcondition verification.
- Never blindly trust stale selectors.
- Revalidate cached selectors before action.
- If a selector fails twice, mark degraded and rediscover.
- Cache semantic roles, not brittle one-off steps.

### 9. Verification engine

Responsibilities:

- Confirm that an action changed visible state as expected.
- Use UIA text, focus state, button state, selection state, window title, and visible control changes.
- Stop or repair when verification fails.

Verification examples:

```text
text field contains typed value
results pane appeared
selected row changed
button label changed
new document opened
window title changed
terminal/build output appeared
calendar/event form saved
```

### 10. Memory-bank integration

The memory bank is external and user-managed. The agent may use it through typed resolvers only.

Allowed uses:

```text
app aliases
preferred apps
current project path
recent workspaces
known app launch hints
known generic shortcuts
frequent contact names
selector cache summaries
previous successful workflows
```

Disallowed uses:

```text
raw command execution
hidden policy overrides
blind trust in old selectors
secret/token reading
private data exfiltration
```

## App generality contract

The project must prove generic behavior with at least these categories:

```text
text editor or notes app
browser or web app
media app
IDE or coding app
calendar or form-based app
file manager or settings app
```

Do not optimize for only one app. Build the abstraction first, then improve app-specific hints as optional metadata.
