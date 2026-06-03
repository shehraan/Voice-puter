"""Inspect a named window and print compact actionable controls.

    python -m app.inspect_window --app "Notepad"

Matches by window-title substring or executable basename (case-insensitive).
"""
from __future__ import annotations

import argparse
import sys

from pywinauto.uia_element_info import UIAElementInfo

from app.core.config import load_config
from app.inspect_foreground import _print_observation
from app.launch.windows import list_windows
from app.ui.observe import observe_window


def find_window(query: str):
    q = query.strip().lower()
    windows = list_windows()
    scored = []
    for w in windows:
        title = w.title.lower()
        exe = w.exe
        score = 0
        if q == title:
            score = 100
        elif q in title:
            score = 60
        if exe and (q in exe or q == exe.replace(".exe", "")):
            score = max(score, 70)
        if score:
            scored.append((score, w))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    return scored[0][1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a named window's controls.")
    parser.add_argument("--app", required=True, help="window title substring or exe name")
    args = parser.parse_args(argv)

    cfg = load_config()
    win = find_window(args.app)
    if win is None:
        print(f"no visible window matched {args.app!r}", file=sys.stderr)
        return 1
    print(f"matched: {win.title!r} ({win.exe}, pid={win.pid})")
    info = UIAElementInfo(win.hwnd)
    obs = observe_window(info, cfg.loop)
    _print_observation(obs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
