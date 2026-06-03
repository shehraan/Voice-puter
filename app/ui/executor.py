"""Grounded, safe UI action executor.

Exposes only an allowlist of primitives. Every targeted action resolves to either an
element from the current observation registry or a revalidated selector-cache entry.
No pixel coordinates, no blind clicks, no typing into an unverified window
(ui-automation-visible.md / security.md).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import keyboard
import win32gui
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.uia_defines import get_elem_interface

from app.cache.selector_cache import SelectorCache
from app.core.config import Config
from app.core.trace import Trace
from app.launch.resolver import AppResolver, ActiveWindow, set_foreground
from app.safety import guardrails
from app.planner.schema import Action
from app.ui.elements import Observation, UIElement
from app.ui.observe import observe_window
from app.ui.selectors import rank_elements
from app.verifier.verify import read_value, verify_postcondition

_MIN_FIND_SCORE = 3.0


@dataclass
class ActionResult:
    op: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Executor:
    def __init__(self, cfg: Config, cache: SelectorCache, trace: Trace, resolver: AppResolver | None = None):
        self.cfg = cfg
        self.cache = cache
        self.trace = trace
        self.resolver = resolver or AppResolver()
        self.window: ActiveWindow | None = None
        self.observation: Observation | None = None
        self._last_target: UIElement | None = None
        self._used: list[tuple[str, UIElement, bool]] = []

    # ---- observation -------------------------------------------------------
    def observe(self) -> Observation | None:
        if not self.window:
            return None
        try:
            self.observation = observe_window(self.window.info, self.cfg.loop)
        except Exception as exc:  # transient UIA/COM failure: keep last observation
            self.trace.log("observe_error", detail=str(exc))
        return self.observation

    def app_key(self) -> str:
        return self.window.app_key if self.window else ""

    # ---- target grounding --------------------------------------------------
    def _resolve_target(self, action: Action) -> tuple[UIElement | None, str]:
        sid = action.target.selector_id
        role = action.target.semantic_role
        if sid:
            if not self.observation:
                return None, "no current observation to resolve selector_id"
            el = self.observation.find(sid)
            if el is None:
                return None, f"selector_id {sid!r} not in current observation (refusing fabricated selector)"
            return el, "from observation"
        if role:
            el = self._find_by_role(role, action.args)
            if el is None:
                return None, f"no control matched semantic_role {role!r}"
            return el, "by semantic_role"
        if self._last_target is not None:
            return self._last_target, "last target"
        return None, "no target specified"

    def _find_by_role(self, role: str, args: dict) -> UIElement | None:
        if not self.observation:
            return None
        name_hints = args.get("name_contains_any") or args.get("name_contains") or []
        if isinstance(name_hints, str):
            name_hints = [name_hints]
        preferred = args.get("preferred_control_types") or []

        desc = self.cache.get(self.app_key(), role)
        if desc:
            cached = self.cache.match_in_observation(desc, self.observation)
            if cached is not None:
                self._last_target = cached
                self._used.append((role, cached, True))
                return cached

        ranked = rank_elements(self.observation.elements, role, name_hints, preferred)
        if not ranked or ranked[0].score < _MIN_FIND_SCORE:
            return None
        el = ranked[0].element
        self._last_target = el
        self._used.append((role, el, False))
        return el

    # ---- cache flush -------------------------------------------------------
    def flush_cache(self, success: bool) -> None:
        for role, el, from_cache in self._used:
            if success:
                self.cache.record_success(self.app_key(), role, el)
            elif from_cache:
                self.cache.record_failure(self.app_key(), role)
        self._used.clear()

    def discard_pending(self) -> None:
        """Clear pending selectors without recording success/failure (progress turns)."""
        self._used.clear()

    # ---- helpers -----------------------------------------------------------
    def _is_foreground(self) -> bool:
        if not self.window:
            return False
        try:
            return win32gui.GetForegroundWindow() == self.window.hwnd
        except Exception:
            return False

    def _ensure_foreground(self) -> bool:
        if self._is_foreground():
            return True
        set_foreground(self.window.hwnd)
        time.sleep(self.cfg.timing.after_focus_ms / 1000.0)
        return self._is_foreground()

    def _focus_element(self, el: UIElement) -> bool:
        try:
            UIAWrapper(el.info).set_focus()
        except Exception:
            return False
        time.sleep(self.cfg.timing.after_focus_ms / 1000.0)
        try:
            return bool(el.info.element.CurrentHasKeyboardFocus)
        except Exception:
            return False

    def _pattern(self, el: UIElement, name: str):
        return get_elem_interface(el.info.element, name)

    # ---- dispatch ----------------------------------------------------------
    def dispatch(self, action: Action) -> ActionResult:
        op = action.op.value if hasattr(action.op, "value") else str(action.op)
        if not guardrails.check_op_allowed(op):
            return ActionResult(op, False, f"op {op!r} not in allowlist")
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            return ActionResult(op, False, f"no handler for op {op!r}")
        try:
            result = handler(action)
        except Exception as exc:  # fail loud, never pretend success
            result = ActionResult(op, False, f"exception: {exc}")
        self.trace.log("action", op=op, ok=result.ok, detail=result.detail, **result.data)
        return result

    # ---- primitives --------------------------------------------------------
    def _op_ensure_window(self, action: Action) -> ActionResult:
        app = action.args.get("app_name") or action.target.semantic_role
        launch_hint = action.args.get("launch_hint")
        self.window = self.resolver.resolve(app, launch_hint)
        forbidden, reason = guardrails.is_forbidden_window(self.window.exe, self.window.title)
        if forbidden:
            self.window = None
            return ActionResult("ensure_window", False, reason)
        self._ensure_foreground()
        time.sleep(self.cfg.timing.after_launch_ms / 1000.0)
        self.observe()
        return ActionResult("ensure_window", True, f"targeting {self.window.title!r}", {"app": self.window.app_key})

    def _op_focus_window(self, action: Action) -> ActionResult:
        if not self.window:
            return ActionResult("focus_window", False, "no window resolved yet")
        ok = self._ensure_foreground()
        return ActionResult("focus_window", ok, "foreground" if ok else "failed to foreground window")

    def _op_observe_window(self, action: Action) -> ActionResult:
        if not self.window:
            return ActionResult("observe_window", False, "no window to observe")
        obs = self.observe()
        return ActionResult("observe_window", True, f"{len(obs.elements)} controls", {"controls": len(obs.elements)})

    def _op_find_control(self, action: Action) -> ActionResult:
        role = action.target.semantic_role or "named_control"
        el = self._find_by_role(role, action.args)
        if el is None:
            return ActionResult("find_control", False, f"no control matched role {role!r}")
        return ActionResult("find_control", True, f"{el.control_type} {el.name!r}", {"selector_id": el.selector_id})

    def _op_focus_control(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("focus_control", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("focus_control", False, "target window not foreground")
        ok = self._focus_element(el)
        return ActionResult("focus_control", True, f"focused {el.name!r} (kbd_focus={ok})", {"selector_id": el.selector_id})

    def _op_invoke_control(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("invoke_control", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("invoke_control", False, "target window not foreground")
        try:
            self._pattern(el, "Invoke").Invoke()
        except Exception:
            try:
                self._pattern(el, "LegacyIAccessible").DoDefaultAction()
            except Exception as exc:
                return ActionResult("invoke_control", False, f"no invokable pattern: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("invoke_control", True, f"invoked {el.name!r}", {"selector_id": el.selector_id})

    def _op_set_value(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("set_value", False, verdict.reason)
        text = str(action.args.get("text", ""))
        try:
            self._pattern(el, "Value").SetValue(text)
        except Exception as exc:
            return ActionResult("set_value", False, f"ValuePattern unavailable: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("set_value", True, f"set value of {el.name!r}", {"selector_id": el.selector_id})

    def _op_type_text(self, action: Action) -> ActionResult:
        text = str(action.args.get("text", ""))
        clear_first = bool(action.args.get("clear_first", False))
        role = action.target.semantic_role or ""
        if not text:
            return ActionResult("type_text", False, "no text to type")
        el, why = self._resolve_target(action)
        if el is None:
            return ActionResult("type_text", False, why)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("type_text", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("type_text", False, "refusing to type: target window not foreground")
        focused = self._focus_element(el)
        if not focused and not self._is_foreground():
            return ActionResult("type_text", False, "refusing to type: no verified focused control")
        time.sleep(0.2)  # let the focused control settle before input

        has_value = "Value" in el.supported_patterns
        # Search-like fields need real keystrokes to trigger live result panes;
        # plain editable fields prefer the reliable ValuePattern (method #2).
        prefer_keyboard = role == "search_or_input"

        if clear_first:
            keyboard.send("ctrl+a")
            time.sleep(0.05)
            keyboard.send("delete")
            time.sleep(0.05)

        method = ""
        if has_value and not prefer_keyboard:
            try:
                self._pattern(el, "Value").SetValue(text)
                method = "value_pattern"
            except Exception:
                method = ""
        if not method:
            keyboard.write(text, delay=self.cfg.timing.type_char_delay_s)
            method = "keyboard"
            # Self-heal garbled fast typing on plain editable fields.
            if has_value:
                time.sleep(0.1)
                if text.lower() not in read_value(el).lower():
                    try:
                        keyboard.send("ctrl+a")
                        time.sleep(0.05)
                        self._pattern(el, "Value").SetValue(text)
                        method = "keyboard+value_heal"
                    except Exception:
                        pass

        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("type_text", True, f"typed {text!r} via {method}", {"selector_id": el.selector_id})

    def _op_send_hotkey(self, action: Action) -> ActionResult:
        keys = str(action.args.get("keys", ""))
        verdict = guardrails.check_hotkey(keys)
        if not verdict.allowed:
            return ActionResult("send_hotkey", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("send_hotkey", False, "refusing hotkey: target window not foreground")
        keyboard.send(keys.replace(" ", ""))
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("send_hotkey", True, f"sent {keys!r}")

    def _op_select_item(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("select_item", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("select_item", False, "target window not foreground")
        try:
            self._pattern(el, "SelectionItem").Select()
        except Exception as exc:
            return ActionResult("select_item", False, f"SelectionItemPattern unavailable: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("select_item", True, f"selected {el.name!r}", {"selector_id": el.selector_id})

    def _op_double_click_element(self, action: Action) -> ActionResult:
        """Open/activate via grounded patterns (no coordinate double-click)."""
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("double_click_element", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("double_click_element", False, "target window not foreground")
        try:
            self._pattern(el, "Invoke").Invoke()
        except Exception:
            try:
                self._pattern(el, "SelectionItem").Select()
                self._focus_element(el)
                keyboard.send("enter")
            except Exception as exc:
                return ActionResult("double_click_element", False, f"no activate path: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("double_click_element", True, f"activated {el.name!r}", {"selector_id": el.selector_id})

    def _op_wait_for(self, action: Action) -> ActionResult:
        timeout_ms = int(action.args.get("timeout_ms", self.cfg.loop.wait_default_ms))
        terms = action.args.get("contains_any") or []
        if isinstance(terms, str):
            terms = [terms]
        terms = [t.lower() for t in terms]
        ctype = action.args.get("control_type")
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            obs = self.observe()
            if obs:
                for e in obs.elements:
                    text = f"{e.name or ''} {read_value(e)}".lower()
                    type_ok = (ctype is None) or (e.control_type == ctype)
                    term_ok = (not terms) or any(t in text for t in terms)
                    if type_ok and term_ok:
                        return ActionResult("wait_for", True, f"condition met: {e.name!r}")
            time.sleep(self.cfg.loop.wait_poll_ms / 1000.0)
        return ActionResult("wait_for", False, f"timeout waiting for terms={terms} type={ctype}")

    def _op_verify(self, action: Action) -> ActionResult:
        obs = self.observe()
        pc = {"type": action.args.get("type", "visible_state_changed"), "args": action.args}
        res = verify_postcondition(pc, obs)
        return ActionResult("verify", res.ok, res.detail)

    def _op_cache_selector(self, action: Action) -> ActionResult:
        role = action.target.semantic_role
        el, why = self._resolve_target(action)
        if not role or el is None:
            return ActionResult("cache_selector", False, "need semantic_role and a resolved element")
        self.cache.record_success(self.app_key(), role, el)
        return ActionResult("cache_selector", True, f"cached {role!r}")

    def _op_repair_selector(self, action: Action) -> ActionResult:
        role = action.target.semantic_role or "named_control"
        self.cache.record_failure(self.app_key(), role)
        self.observe()
        el = self._find_by_role(role, action.args)
        if el is None:
            return ActionResult("repair_selector", False, f"could not rediscover {role!r}")
        return ActionResult("repair_selector", True, f"rediscovered {el.name!r}", {"selector_id": el.selector_id})

    def _op_clarify(self, action: Action) -> ActionResult:
        msg = action.args.get("message", "need clarification")
        return ActionResult("clarify", True, msg, {"clarify": True})

    def _op_stop_with_failure(self, action: Action) -> ActionResult:
        reason = action.args.get("reason", "planner requested stop")
        return ActionResult("stop_with_failure", False, reason, {"stop": True})
