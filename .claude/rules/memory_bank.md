# Memory Bank Rules

At the start of every task, read all files in `memory_bank/`.

Use these files as the source of truth for this repo:

- `projectbrief.md`: goals, requirements, scope
- `productContext.md`: why this exists, user problems, UX goals
- `activeContext.md`: current focus, recent changes, next steps
- `systemPatterns.md`: architecture, design patterns, key decisions
- `techContext.md`: stack, setup, constraints
- `progress.md`: what works, what is left, known issues

Before major changes, check relevant memory files.

After meaningful progress, update:
- `activeContext.md`
- `progress.md`
- any file affected by architecture, stack, or product decisions

Keep memory files concise, factual, and repo-specific.
Do not overwrite important context unless it is outdated.