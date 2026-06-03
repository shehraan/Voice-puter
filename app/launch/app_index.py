"""Installed-app discovery metadata.

This is discovery metadata ONLY (aliases, launch hints, exe hints, semantic hints) -
never an app-specific workflow. The generic UI loop must work without it; the index
just makes resolution faster and more reliable (generic-app-automation.md).
"""
from __future__ import annotations

APP_INDEX: dict[str, dict] = {
    "notepad": {
        "aliases": ["notepad", "notes", "text editor"],
        "launch": ["notepad"],
        "exe": ["notepad.exe"],
    },
    "calculator": {
        "aliases": ["calculator", "calc"],
        "launch": ["calc"],
        "exe": ["calculatorapp.exe", "calculator.exe"],
    },
    "wordpad": {"aliases": ["wordpad"], "launch": ["write"], "exe": ["wordpad.exe"]},
    "brave": {
        "aliases": ["brave", "browser", "my browser", "web browser"],
        "launch": ["brave"],
        "exe": ["brave.exe"],
    },
    "edge": {
        "aliases": ["edge", "microsoft edge"],
        "launch": ["msedge"],
        "exe": ["msedge.exe"],
    },
    "chrome": {"aliases": ["chrome", "google chrome"], "launch": ["chrome"], "exe": ["chrome.exe"]},
    "spotify": {
        "aliases": ["spotify", "music app", "my music app", "music"],
        "launch": ["spotify"],
        "exe": ["spotify.exe"],
    },
    "vscode": {
        "aliases": ["vscode", "vs code", "visual studio code", "code", "codex", "my editor"],
        "launch": ["code"],
        "exe": ["code.exe"],
    },
    "explorer": {
        "aliases": ["explorer", "file explorer", "files"],
        "launch": ["explorer"],
        "exe": ["explorer.exe"],
    },
    "outlook": {
        "aliases": ["outlook", "calendar", "calendar app", "my calendar app", "mail"],
        "launch": ["outlook"],
        "exe": ["outlook.exe", "olk.exe"],
    },
    "terminal": {
        "aliases": ["terminal", "windows terminal", "cmd", "powershell"],
        "launch": ["wt"],
        "exe": ["windowsterminal.exe", "cmd.exe", "powershell.exe"],
    },
}


def lookup(app_name: str) -> tuple[str, dict] | None:
    """Return (canonical_key, entry) for an app name/alias, or None."""
    low = (app_name or "").strip().lower()
    if not low:
        return None
    if low in APP_INDEX:
        return low, APP_INDEX[low]
    for key, entry in APP_INDEX.items():
        for alias in entry.get("aliases", []):
            if alias == low or alias in low or low in alias:
                return key, entry
    return None
