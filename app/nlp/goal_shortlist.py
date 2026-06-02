"""Fast goal shortlist.

Heuristic regex layer that guesses the goal category and pulls out the target app and
payload before the planner runs. This is a hint only - the planner still grounds every
action against the observed UI tree (architecture.md goal-shortlist section).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.planner.schema import GoalType


@dataclass
class GoalHint:
    goal: GoalType
    target_app: str | None = None
    payload: str | None = None  # text to type / query to search
    query: str | None = None  # named control / result selector phrase


def _clean_app(name: str | None) -> str | None:
    if not name:
        return None
    return re.sub(r"\s+", " ", name).strip(" .,!\"'") or None


def shortlist(normalized: str) -> GoalHint:
    t = normalized.strip()

    # search <query> in <app> [and play/open the best result]
    m = re.search(r"\bsearch (?:for )?(?P<q>.+?) (?:in|on|using) (?:my )?(?P<app>.+?)(?: and .+)?$", t)
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), m.group("q").strip())

    # open <app> and type <text>
    m = re.search(r"\b(?:open|launch|start|go to) (?:my )?(?P<app>.+?) and (?:type|write|enter|say) (?P<text>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_text_entry, _clean_app(m.group("app")), m.group("text").strip())

    # open <app> and build/run it
    m = re.search(r"\b(?:open|launch|start) (?:my )?(?P<app>.+?) and (?:build|run|compile|debug)\b", t)
    if m:
        return GoalHint(GoalType.generic_build_or_run, _clean_app(m.group("app")))

    # build/run the project (no explicit app)
    if re.search(r"\b(build|run|compile|debug)\b", t) and re.search(r"\b(project|workspace|solution|it)\b", t):
        m = re.search(r"\bin (?:my )?(?P<app>[a-z0-9 ]+)$", t)
        return GoalHint(GoalType.generic_build_or_run, _clean_app(m.group("app")) if m else None)

    # type/write <text> (no app)
    m = re.search(r"^(?:type|write|enter) (?P<text>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_text_entry, None, m.group("text").strip())

    # click/press/select <control>
    m = re.search(r"^(?:click|press|tap|select|invoke|choose) (?:on )?(?:the )?(?P<ctrl>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_click_named_control, None, None, m.group("ctrl").strip())

    # create/add a meeting/event/note (form)
    m = re.search(r"\b(?:create|add|new) (?:a )?(?P<thing>meeting|event|appointment|note|task|reminder)\b.*?(?:in (?:my )?(?P<app>.+))?$", t)
    if m:
        return GoalHint(GoalType.generic_form_create, _clean_app(m.group("app")))

    # open/focus <app>
    m = re.search(r"^(?:open|launch|start|focus|switch to|go to) (?:my )?(?P<app>.+)$", t)
    if m:
        verb = t.split()[0]
        goal = GoalType.focus_app if verb in ("focus", "switch") else GoalType.open_app
        return GoalHint(goal, _clean_app(m.group("app")))

    if not t:
        return GoalHint(GoalType.no_op)
    return GoalHint(GoalType.clarify)
