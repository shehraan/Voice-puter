# Performance Rules

## Latency target

The agent should feel fast enough for a live demo while still being visibly operated.

Targets:

```text
VAD chunk: under 5ms compute
STT finalize: 250ms to 800ms after speech ends
Planner first action: 100ms to 500ms
UIA observe target window: 100ms to 700ms
UIA action: 20ms to 250ms
Warm generic visible command: under 2s to first visible action
Cold generic visible command: under 4s if app launch is not slow
```

## Optimization order

1. Push-to-talk first.
2. Keep planner prompt short.
3. Use compact UI observations.
4. Cache selector successes.
5. Pre-warm local model.
6. Pre-warm STT.
7. Start safe app-resolution on partial transcript when confidence is high.
8. Reuse window handles.
9. Batch UIA property reads.
10. Stop planning once verified.

## Prompt budget

The planner should not receive the whole memory bank or full UI tree.

Send only:

```text
user goal
target app guess
relevant UI tree subset
selector cache candidates
previous action result
safety state
```

## Streaming behavior

Use partial transcript to start safe pre-actions:

Allowed pre-actions:

```text
load STT/model context
resolve installed app candidates
warm UIA engine
index foreground window
precompute likely semantic roles
```

Do not type, click, invoke, submit, or send hotkeys until the final transcript and target window are verified.

## Selector cache performance

On repeated tasks:

1. Try validated cached selector.
2. Reconfirm visible and enabled state.
3. Act.
4. Verify postcondition.
5. Update selector success count.

If revalidation fails, rediscover instead of burning time on retries.
