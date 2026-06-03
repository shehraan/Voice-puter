"""Safety guardrails.

Second, independent gate after the planner schema. The executor calls into these
checks before performing any action. The agent may visibly operate apps but must not
become an unrestricted computer-control agent (security.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.planner.schema import ACTION_OPS

ALLOWED_OPS = set(ACTION_OPS)

# Processes whose UI we must never automate.
FORBIDDEN_PROCESSES = {"consent.exe", "logonui.exe", "lockapp.exe", "credentialuihost.exe"}

# Command-intent patterns that require explicit user confirmation before executing.
_CONFIRM_PATTERNS = [
    (r"\bdelete\b|\bremove\b|\btrash\b", "deletes or moves files"),
    (r"\buninstall\b|\binstall\b", "installs or uninstalls software"),
    (r"\bbuy\b|\bpurchase\b|\bcheckout\b|\bpay(ment)?\b|\bsubscribe\b", "makes a purchase or payment"),
    (r"\bsend\b.*\b(email|message|text|dm|mail|reply)\b|\bcompose\b|\bpost\b|\btweet\b", "sends a message or post"),
    (r"\bgit\b.*\b(commit|push|reset|rebase|force|merge)\b", "performs a git write operation"),
    (r"\brm\s+-rf\b|\bformat\b|\bdel\s+/", "performs destructive shell deletion"),
    (r"\b(admin|elevate|elevated|sudo|as administrator)\b", "requires elevated/admin privileges"),
]

# Dangerous hotkey combinations the planner must not emit blindly.
_FORBIDDEN_HOTKEYS = {"alt+f4", "ctrl+w"}  # closing windows can lose unsaved work


@dataclass
class SafetyVerdict:
    allowed: bool
    needs_confirmation: bool = False
    reason: str = ""


def check_op_allowed(op: str) -> bool:
    return op in ALLOWED_OPS


def command_needs_confirmation(command_text: str) -> tuple[bool, str]:
    low = command_text.lower()
    for pattern, reason in _CONFIRM_PATTERNS:
        if re.search(pattern, low):
            return True, reason
    return False, ""


def is_forbidden_window(exe: str, title: str) -> tuple[bool, str]:
    e = (exe or "").lower()
    if e in FORBIDDEN_PROCESSES:
        return True, f"refusing to automate protected process {e}"
    return False, ""


def _is_password_element(element) -> bool:
    info = getattr(element, "info", None)
    com = getattr(info, "element", None)
    if com is None:
        return False
    try:
        return bool(com.CurrentIsPassword)
    except Exception:
        return False


def check_element_target(element) -> SafetyVerdict:
    """Reject password fields and offscreen/disabled targets."""
    if element is None:
        return SafetyVerdict(allowed=False, reason="no target element resolved")
    if _is_password_element(element):
        return SafetyVerdict(allowed=False, reason="refusing to target a password field")
    if getattr(element, "is_offscreen", False):
        return SafetyVerdict(allowed=False, reason="refusing to target an offscreen control")
    if not getattr(element, "is_enabled", True):
        return SafetyVerdict(allowed=False, reason="refusing to target a disabled control")
    return SafetyVerdict(allowed=True)


def check_hotkey(keys: str) -> SafetyVerdict:
    norm = (keys or "").strip().lower().replace(" ", "")
    if norm in _FORBIDDEN_HOTKEYS:
        return SafetyVerdict(allowed=False, reason=f"refusing dangerous hotkey {keys}")
    return SafetyVerdict(allowed=True)
