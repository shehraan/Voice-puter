"""Latency benchmark for the hot path.

Measures the spans that matter for a live demo (performance.md): foreground UIA
observation, a planner round-trip, and a single grounded UI action. Does not require a
microphone.

    python -m app.bench_latency
"""
from __future__ import annotations

import sys
import time

import win32gui
from pywinauto.uia_element_info import UIAElementInfo

from app.core.config import load_config
from app.nlp.goal_shortlist import shortlist
from app.nlp.normalizer import normalize
from app.planner.base import PlannerContext
from app.ui.observe import observe_window


def _ms(fn) -> tuple[float, object]:
    start = time.perf_counter()
    out = fn()
    return (time.perf_counter() - start) * 1000.0, out


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()

    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        print("no foreground window to benchmark against", file=sys.stderr)
        return 1
    info = UIAElementInfo(hwnd)

    t_obs, obs = _ms(lambda: observe_window(info, cfg.loop))
    print(f"UIA observe foreground : {t_obs:7.1f} ms ({len(obs.elements)} controls)")

    cmd = "open notepad and type hello world"
    norm = normalize(cmd)
    hint = shortlist(norm)
    ctx = PlannerContext(transcript=cmd, normalized=norm, goal_hint=hint,
                         observation=obs.to_compact(), window=obs.window.to_dict())
    try:
        from app.planner.factory import make_planner

        planner = make_planner(cfg.planner)
        t_warm, _ = _ms(lambda: planner.plan(ctx))   # warm the model
        t_plan, _ = _ms(lambda: planner.plan(ctx))
        print(f"planner first (cold)   : {t_warm:7.1f} ms")
        print(f"planner round-trip     : {t_plan:7.1f} ms (model={cfg.planner.model})")
    except Exception as exc:
        print(f"planner benchmark skipped: {exc}")

    if obs.elements:
        from pywinauto.controls.uiawrapper import UIAWrapper

        el = obs.elements[0]
        t_act, _ = _ms(lambda: UIAWrapper(el.info).element_info.name)
        print(f"single UIA property    : {t_act:7.1f} ms")

    return 0


if __name__ == "__main__":
    sys.exit(main())
