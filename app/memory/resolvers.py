"""Typed resolvers over the user-managed memory bank.

The memory bank is external and user-managed. The agent may only read it through these
typed resolvers (no raw command execution, no secret reading). If no machine-readable
memory file exists, every resolver degrades to an empty/None result.
"""
from __future__ import annotations

import json

from app.core.config import REPO_ROOT

_MEMORY_JSON = REPO_ROOT / "memory-bank" / "agent_memory.json"


def _load() -> dict:
    if _MEMORY_JSON.exists():
        try:
            return json.loads(_MEMORY_JSON.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def app_aliases() -> dict[str, str]:
    """Map of user alias -> canonical app name/launch hint."""
    data = _load()
    aliases = data.get("app_aliases", {})
    return {str(k).lower(): str(v) for k, v in aliases.items()} if isinstance(aliases, dict) else {}


def current_project_path() -> str | None:
    data = _load()
    val = data.get("current_project_path")
    return str(val) if val else None


def memory_summary() -> dict:
    """Small, safe subset to feed the planner (never secrets)."""
    data = _load()
    return {
        "app_aliases": app_aliases(),
        "current_project_path": current_project_path(),
        "preferred_apps": data.get("preferred_apps", {}),
    }
