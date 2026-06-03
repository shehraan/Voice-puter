"""Learning selector cache with revalidation and repair.

Caches reusable selector descriptors per (app, semantic_role) but only after a verified
postcondition. Cached selectors are always revalidated against a fresh observation
before reuse; after repeated failures they are marked degraded and rediscovered
(architecture.md / generic-app-automation.md).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import SELECTOR_CACHE_PATH
from app.ui.elements import Observation, UIElement

_DEGRADE_AFTER_FAILURES = 2


@dataclass
class SelectorDescriptor:
    automation_id: str = ""
    name_regex: str = ""
    control_type: str = ""
    class_name: str = ""
    supported_patterns: list[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    degraded: bool = False
    last_verified_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _descriptor_from_element(el: UIElement) -> SelectorDescriptor:
    name = el.name or ""
    return SelectorDescriptor(
        automation_id=el.automation_id,
        name_regex=re.escape(name) if name else "",
        control_type=el.control_type,
        class_name=el.class_name,
        supported_patterns=list(el.supported_patterns),
        last_verified_at=_now_iso(),
    )


class SelectorCache:
    def __init__(self, path: Path | None = None):
        self.path = path or SELECTOR_CACHE_PATH
        self.data: dict[str, dict[str, dict]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def get(self, app_key: str, role: str) -> SelectorDescriptor | None:
        raw = self.data.get(app_key, {}).get(role)
        if not raw:
            return None
        desc = SelectorDescriptor(**raw)
        if desc.degraded:
            return None
        return desc

    def match_in_observation(self, desc: SelectorDescriptor, obs: Observation) -> UIElement | None:
        """Revalidate a cached descriptor against a fresh observation."""
        best: tuple[float, UIElement] | None = None
        for el in obs.elements:
            if not el.is_enabled or el.is_offscreen:
                continue
            score = 0.0
            if desc.automation_id and el.automation_id == desc.automation_id:
                score += 5.0
            if desc.control_type and el.control_type == desc.control_type:
                score += 2.0
            if desc.name_regex:
                try:
                    if re.search(desc.name_regex, el.name or ""):
                        score += 3.0
                except re.error:
                    pass
            if desc.supported_patterns and set(desc.supported_patterns) & set(el.supported_patterns):
                score += 1.0
            if score >= 5.0 and (best is None or score > best[0]):
                best = (score, el)
        return best[1] if best else None

    def record_success(self, app_key: str, role: str, el: UIElement) -> None:
        existing = self.data.setdefault(app_key, {}).get(role)
        if existing and existing.get("automation_id") == el.automation_id:
            desc = SelectorDescriptor(**existing)
            desc.success_count += 1
            desc.failure_count = 0
            desc.degraded = False
            desc.last_verified_at = _now_iso()
            desc.supported_patterns = list(el.supported_patterns)
        else:
            desc = _descriptor_from_element(el)
            desc.success_count = 1
        self.data[app_key][role] = asdict(desc)
        self.save()

    def record_failure(self, app_key: str, role: str) -> None:
        raw = self.data.get(app_key, {}).get(role)
        if not raw:
            return
        desc = SelectorDescriptor(**raw)
        desc.failure_count += 1
        if desc.failure_count >= _DEGRADE_AFTER_FAILURES:
            desc.degraded = True
        self.data[app_key][role] = asdict(desc)
        self.save()
