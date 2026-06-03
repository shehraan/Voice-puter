"""Inspect the current foreground window and print compact actionable controls.

    python -m app.inspect_foreground
"""
from __future__ import annotations

import sys
import time

import win32gui
from pywinauto.uia_element_info import UIAElementInfo

from app.core.config import load_config
from app.ui.observe import observe_window


def _print_observation(obs) -> None:
    w = obs.window
    print(f"window: {w.title!r}  process={w.process}  pid={w.pid}  foreground={w.is_foreground}")
    print(f"actionable controls ({len(obs.elements)}):")
    for el in obs.elements:
        patterns = ",".join(el.supported_patterns) or "-"
        name = (el.name or "")[:50]
        print(
            f"  {el.selector_id:<7} [{el.control_type:<10}] "
            f"name={name!r:<52} aid={el.automation_id!r:<22} "
            f"en={int(el.is_enabled)} off={int(el.is_offscreen)} pat={patterns}"
        )


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    time.sleep(0.3)  # give the user a moment to focus the target window
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        print("no foreground window found", file=sys.stderr)
        return 1
    info = UIAElementInfo(hwnd)
    obs = observe_window(info, cfg.loop)
    _print_observation(obs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
