# UI Automation Visible Execution Rules

## Primary rule

The main demo must visibly control desktop apps using Windows UI Automation.

When the user asks the agent to use an app, the screen should show the app being opened or focused, controls being selected, text being typed, menus being opened, results appearing, and actions being invoked.

## Action method order

Use these in order:

1. UIA pattern action on a visible control.
2. UIA focus plus `ValuePattern` or text-setting action.
3. UIA focus plus keyboard typing.
4. Targeted app hotkey, only after confirming the target window is foregrounded.
5. SendInput or AutoHotkey fallback, only if the target window is verified.

## Disallowed for demo behavior

- Do not use hidden APIs to skip the visible interaction.
- Do not use raw pixel coordinates.
- Do not click blindly.
- Do not type unless the active window and focused element are verified.
- Do not continue after a failed verification.
- Do not create one-off scripts for a single app and pretend they are generic.

## UIA discovery fields

For every observed control, collect:

```text
name
automation_id
control_type
class_name
localized_control_type
is_enabled
is_offscreen
has_keyboard_focus
bounding_rectangle
supported_patterns
parent_summary
sibling_summary
child_count
```

## Preferred control patterns

Prefer semantic UIA patterns:

```text
InvokePattern -> buttons, menu items, clickable controls
ValuePattern -> editable fields
TextPattern -> reading visible text
SelectionItemPattern -> selectable rows/items
ExpandCollapsePattern -> menus, combo boxes, expandable sections
WindowPattern -> window state
ScrollPattern -> scrollable panes
```

## Observation compression

Never dump a massive full tree into the planner. Send a compact tree with likely actionable controls first.

Prioritize controls whose names, roles, or patterns suggest:

```text
search
find
input
edit
open
new
save
run
build
play
submit
send
result
row
item
project
terminal
calendar
date
time
```

## Generic visible search flow

When a command requires searching inside any app:

```text
ensure target app/window
observe window
find search-like or command-like control
focus it visibly
type query visibly
wait for results or visible state change
select best visible result
invoke/open/play/submit depending on goal
verify visible postcondition
cache successful selectors
```

## Generic visible form flow

When a command requires filling a form:

```text
ensure target app/window
observe window
find creation control if needed
open form visibly
map fields by label/name/control type
fill each field visibly
verify typed values
submit/save visibly
verify form saved or new item exists
```

## Generic visible build/run flow

When a command requires building or running a project:

```text
ensure target IDE/window
observe window
find project or workspace context
prefer visible command palette/menu/toolbar path
invoke build/run visibly
verify terminal/output/status change
```

A direct build command may be used for fallback or verification only when visual demo mode is disabled or the user explicitly requests speed over visual operation.

## Fallback behavior

If UIA cannot see a control:

1. Try a broader UIA tree view.
2. Try legacy Win32 backend.
3. Try a grounded keyboard shortcut if the target app is foreground.
4. Use SendInput only with target verification.
5. Stop and report failure in logs if still not possible.

## Visual timing

Do not make the action invisible by executing too quickly. For demo polish, keep short human-visible pauses after major visual changes:

```text
after launching app: 300ms to 800ms
after focusing an input: 100ms to 250ms
while typing text: visibly type or chunk quickly but visibly
after invoking a result/action: wait for visible UI change
```

The agent should feel fast, not like a hidden API script.
