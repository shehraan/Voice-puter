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
        # Ctrl+K navigates to Spotify's search page (avoiding the phantom Chromium omnibox
        # that the CEF accessibility tree always exposes as "Address and search bar"). The
        # result is then activated explicitly, so Enter alone does not play.
        "search_shortcut": "ctrl+k",
        "search_plays_on_enter": False,
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
    "feishin": {
        "aliases": ["feishin", "my music app", "music player", "music server"],
        "launch": ["feishin"],
        "exe": ["feishin.exe"],
        # Feishin exposes 'Edit name=Search' via ValuePattern; no search shortcut
        # needed — the generic UIA path writes directly to the field.
    },
    "beeper": {
        "aliases": ["beeper", "messages", "chat app", "messaging app"],
        "launch": ["beeper"],
        "exe": ["beeper.exe"],
        # Beeper is an Electron messaging app. Searching for a contact uses the
        # 'Search Chats' button (Invoke), then a text field appears. After selecting
        # the chat, the compose field (unnamed Edit) accepts the message.
        "needs_confirmation_for": ["send_message"],
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


def search_shortcut(app_name: str) -> str | None:
    """Hotkey that focuses/opens the app's own search, if known (discovery metadata)."""
    indexed = lookup(app_name)
    return indexed[1].get("search_shortcut") if indexed else None


def search_plays_on_enter(app_name: str) -> bool:
    """True when submitting the app's quick-search plays the top result directly."""
    indexed = lookup(app_name)
    return bool(indexed[1].get("search_plays_on_enter")) if indexed else False


