"""Text-command entrypoint.

    python -m app.run_text "open notepad and type hello world"

This is the primary developer-facing runner: it exercises the full
observe -> plan -> act -> observe loop without the audio front-end.
"""
from __future__ import annotations

import argparse
import sys

from app.core.config import load_config
from app.core.trace import Trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a single text command through the agent.")
    parser.add_argument("command", help="natural-language command, e.g. 'open notepad and type hello world'")
    parser.add_argument("--no-demo", action="store_true", help="disable visual_demo_mode (allow hidden fallbacks)")
    parser.add_argument("--yes", action="store_true", help="auto-confirm confirmation-required actions")
    parser.add_argument("--stub", action="store_true", help="use the deterministic stub planner (no Ollama)")
    parser.add_argument(
        "--openai",
        action="store_true",
        help="use OpenAI GPT-5.4 with high reasoning (OPENAI_API_KEY required)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="stream each step to stderr as it happens (actions, plans, waits)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.no_demo:
        cfg.visual_demo_mode = False
    if args.yes:
        cfg.auto_confirm = True
    if args.openai:
        cfg.planner.provider = "openai"

    trace = Trace(transcript=args.command, live=args.live)
    if args.live:
        print(f"live log: {args.command!r}", file=sys.stderr, flush=True)

    # Lazy import so scaffold/no_op smoke test works before heavy deps are installed.
    if args.command.strip().lower() == "no_op":
        trace.normalized = "no_op"
        trace.goal = "no_op"
        trace.result = "success"
        trace.log("no_op", message="scaffold smoke test")
        path = trace.save()
        print(f"[no_op] trace written to {path}")
        return 0

    from app.core.loop import run_command

    from app.planner.factory import make_planner

    planner = make_planner(cfg.planner, stub=args.stub)
    if args.openai and not args.stub:
        agents = cfg.planner.consensus_agents if cfg.planner.consensus_enabled else 1
        print(
            f"planner: OpenAI {cfg.planner.openai_model} "
            f"(reasoning={cfg.planner.openai_reasoning_effort}, consensus={agents} agents)",
            file=sys.stderr,
        )

    def _confirm(reason: str) -> bool:
        try:
            return input(f"Confirm action ({reason})? [y/N] ").strip().lower() in ("y", "yes")
        except EOFError:
            return False

    result = run_command(args.command, cfg, trace, planner=planner, confirm=_confirm)
    path = trace.save()
    print(f"\nresult: {trace.result}")
    if trace.failure_reason:
        print(f"reason: {trace.failure_reason}")
    print(f"trace: {path}")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
