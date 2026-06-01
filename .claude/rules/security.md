# Security and Safety Rules

## Core rule

The agent is allowed to visibly operate desktop applications. It is not allowed to become an unrestricted computer-control agent.

## Hard restrictions

The planner must not emit:

```text
raw shell commands
arbitrary PowerShell
arbitrary Python execution
registry edits
file deletion
credential reads
secret exfiltration
admin/elevated actions
network requests not owned by approved components
```

## Executor restrictions

The executor must:

- validate every action against an allowlisted primitive
- confirm target window before typing or hotkeys
- reject offscreen or disabled controls unless scrolling/expanding is explicitly required
- reject pixel-only actions
- reject stale selector cache entries without revalidation
- require confirmation for destructive actions
- stop when verification fails and repair budget is exhausted

## Confirmation-required examples

Require explicit confirmation before:

```text
deleting files
sending messages or emails
making purchases
submitting forms externally
changing system settings
closing unsaved documents
running build/deploy commands that affect production
installing software
uninstalling software
```

## Allowed low-risk actions

Allowed without confirmation if grounded and visible:

```text
open an app
focus a window
search inside an app
type into an empty search/input field
select a visible result
play/pause media in a visible media app
open a local project
run a local build task
create a local draft or unsent form
```

## Secrets policy

Never read, log, display, or transmit:

```text
.env files
API keys
tokens
passwords
browser cookies
credential manager contents
private message bodies unless explicitly requested
```

## Logging policy

Logs should include:

```text
trace_id
transcript
goal
window target
observed control summaries
chosen actions
verification result
latency spans
failure reason
```

Logs should not include secrets, passwords, tokens, or full sensitive document bodies.
