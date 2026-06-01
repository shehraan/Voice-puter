# Generic App Automation Rules

## Goal

The system must interact with reasonable Windows desktop apps through dynamic UI discovery, not only preconfigured app adapters.

## Runtime strategy

For every app interaction:

```text
resolve app -> observe -> infer semantic controls -> act -> verify -> cache -> repair if needed
```

## Window discovery

Find windows by:

```text
exact or fuzzy title
process name
executable path
visible text in UI tree
recently active window
installed app index
Start menu shortcut
memory-bank alias
```

## Installed app index

Maintain a local app index like this:

```json
{
  "Example App": {
    "aliases": ["example", "sample app"],
    "launch_hints": ["example.exe", "shell:AppsFolder\\..."],
    "exe_hints": ["example.exe"],
    "shortcut_hints": ["Ctrl+F", "Ctrl+K"],
    "semantic_hints": {
      "search": ["Search", "Find", "Command"],
      "submit": ["Open", "Play", "Run", "Create", "Save"]
    }
  }
}
```

This is not an app-specific workflow. It is discovery metadata only.

## Semantic control matching

Given a goal, infer likely controls:

```text
search -> Search, Find, Query, Command, Ask, What do you want
play/media -> Play, Resume, result rows, media controls
build/run -> Build, Run, Task, Terminal, Command Palette, toolbar buttons
open project/file -> Open, Folder, Recent, File menu, workspace list
calendar/form -> New, Add, Create, Title, Date, Time, Save
message/send -> Message, Compose, To, Body, Send
settings -> Settings, Preferences, Options, Toggle, Apply
```

## Selector scoring

Score candidate controls by:

```text
exact semantic role match
name similarity
control type compatibility
supported pattern compatibility
visibility
enabled state
keyboard focusability
distance from focused/active region
parent/sibling context
selector cache success history
recent action context
```

## Action grounding

The planner may request a semantic action like:

```text
focus the search box
```

The executor must translate it to a specific observed control before acting.

Never let the planner target an element that was not observed or retrieved from a validated selector cache.

## Selector repair

If a cached selector fails:

1. Re-observe target window.
2. Search for the same semantic role.
3. Compare sibling and parent structure.
4. Try fuzzy name match.
5. Try same control type with similar supported patterns.
6. Try focus traversal only if target window is verified.
7. Mark old selector degraded.
8. Cache repaired selector only after successful postcondition.

## Non-deterministic planning

The planner can choose different paths:

```text
search box first
command palette first
menu navigation first
toolbar button first
keyboard shortcut first
recent item first
visible result row first
```

But each choice must be grounded in current UI observation and include verification.

## Do not fake generality

Do not write one-off scripts pretending to be generic automation.

If adding an app-specific helper, keep it limited to optional hints:

```text
app aliases
common launch hints
known shortcut candidates
known semantic control names
postcondition hints
```

The generic UI agent must still be able to inspect and act without the helper.

## Minimum generic capabilities

The project should support generic versions of:

```text
open an app
focus an existing app
search inside an app
type into a visible field
select a visible result
click/invoke a named visible control
open a menu item
create/fill/save a visible form
run/build from an IDE-like UI
verify visible result state
```
