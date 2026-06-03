"""App/window resolver.

Resolves a target app name/alias to a concrete top-level window, launching it visibly
if needed, and confirms the foreground window before the executor acts on it
(architecture.md window-resolver section).
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import win32con
import win32gui
from pywinauto.uia_element_info import UIAElementInfo

from app.launch.app_index import lookup
from app.launch.windows import WindowInfo, get_foreground_hwnd, list_windows
from app.memory.resolvers import app_aliases

_FOREGROUND_TOKENS = {"this", "current", "active", "foreground", "front", "it"}


@dataclass
class ActiveWindow:
    hwnd: int
    info: UIAElementInfo
    title: str
    exe: str
    app_key: str


class ResolutionError(Exception):
    pass


def _tokens(name: str) -> list[str]:
    return [t for t in name.lower().replace("-", " ").split() if t not in {"my", "the", "app", "a"}]


def _score_window(win: WindowInfo, name: str, exe_hints: list[str]) -> int:
    title = win.title.lower()
    score = 0
    if name and name == title:
        score += 100
    if name and name in title:
        score += 50
    for tok in _tokens(name):
        if tok and tok in title:
            score += 15
        if tok and win.exe and tok in win.exe:
            score += 20
    for hint in exe_hints:
        if win.exe and hint.lower() == win.exe:
            score += 60
    return score


def _find_existing(name: str, exe_hints: list[str]) -> WindowInfo | None:
    windows = list_windows()
    scored = [(s, w) for w in windows if (s := _score_window(w, name, exe_hints)) > 0]
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _launch(commands: list[str]) -> None:
    for cmd in commands:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", cmd], shell=False)
            return
        except Exception:
            continue


def _make_active(win: WindowInfo, app_key: str) -> ActiveWindow:
    return ActiveWindow(
        hwnd=win.hwnd,
        info=UIAElementInfo(win.hwnd),
        title=win.title,
        exe=win.exe,
        app_key=app_key or (win.exe or win.title.lower()),
    )


def set_foreground(hwnd: int) -> bool:
    """Bring a window to the foreground using the standard alt-key unlock trick."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            shell.SendKeys("%")
        except Exception:
            pass
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


class AppResolver:
    def __init__(self, launch_timeout_s: float = 8.0):
        self.launch_timeout_s = launch_timeout_s

    def resolve(self, app_name: str | None, launch_hint: str | None = None) -> ActiveWindow:
        # Foreground / "this app".
        if not app_name or app_name.strip().lower() in _FOREGROUND_TOKENS:
            hwnd = get_foreground_hwnd()
            if not hwnd:
                raise ResolutionError("no foreground window to target")
            for w in list_windows():
                if w.hwnd == hwnd:
                    return _make_active(w, w.exe)
            raise ResolutionError("foreground window is not a normal top-level window")

        name = app_name.strip()
        # Memory-bank alias indirection (user-managed).
        mem = app_aliases()
        if name.lower() in mem:
            name = mem[name.lower()]

        indexed = lookup(name)
        app_key = indexed[0] if indexed else name.lower()
        exe_hints = indexed[1].get("exe", []) if indexed else []
        launch_cmds: list[str] = []
        if launch_hint:
            launch_cmds.append(launch_hint)
        if indexed:
            launch_cmds.extend(indexed[1].get("launch", []))
        launch_cmds.append(name)

        existing = _find_existing(name, exe_hints)
        if existing:
            return _make_active(existing, app_key)

        _launch(launch_cmds)
        deadline = time.time() + self.launch_timeout_s
        while time.time() < deadline:
            time.sleep(0.4)
            found = _find_existing(name, exe_hints)
            if found:
                return _make_active(found, app_key)
        raise ResolutionError(f"could not resolve or launch app {app_name!r} (tried {launch_cmds})")
