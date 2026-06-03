"""Low-level Win32 window/process helpers (pywin32).

Used by the inspector, the window resolver, and the observation engine. Kept free of
any planning/automation logic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import win32con
import win32gui
import win32process


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str  # lowercase basename, e.g. "notepad.exe"


def exe_for_pid(pid: int) -> str:
    if not pid:
        return ""
    try:
        import win32api

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
            return os.path.basename(path).lower()
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def get_foreground_hwnd() -> int:
    try:
        return win32gui.GetForegroundWindow()
    except Exception:
        return 0


def list_windows() -> list[WindowInfo]:
    """Visible, titled, top-level windows."""
    results: list[WindowInfo] = []

    def _cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        results.append(WindowInfo(hwnd=hwnd, title=title, pid=pid, exe=exe_for_pid(pid)))
        return True

    win32gui.EnumWindows(_cb, None)
    return results
