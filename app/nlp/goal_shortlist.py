"""Fast goal shortlist.

Heuristic layer that guesses the goal category and pulls out the target app, the
payload (text/query/contact), any trailing follow-up action, and a message body for
messaging commands — before the planner runs. This is a hint only; the planner still
grounds every action against the observed UI tree (architecture.md goal-shortlist
section).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.planner.schema import GoalType

# Words that introduce a follow-up action after a search ("...and play it").
_THEN_VERBS = {
    "play": "play",
    "open": "open",
    "select": "select",
    "start": "play",
    "launch": "open",
    "choose": "select",
    "pick": "select",
}

# Noise phrases to strip from a music/media query.
_QUERY_NOISE = (
    "the song", "the track", "the video", "a song", "a video", "the movie",
    "the album", "song", "for", "the best result", "best result", "the result",
)

# Matches a "send [him/her] a message [saying] <text>" tail.
# Note: 'a' is separated from him/her/them to avoid consuming 'a message' as 'a ' pronoun.
# The msg group uses a simple greedy capture; _clean_msg strips surrounding quotes.
_SEND_MSG_PAT = re.compile(
    r"[,.]?\s+(?:and\s+)?(?:then\s+)?send\s+(?:(?:him|her|them)\s+)?"
    r"(?:a\s+)?(?:message|msg)\s*(?:saying|with|that\s+says)?\s*(?P<msg>.+?)\s*$",
    re.IGNORECASE,
)

# Matches verbs that navigate TO something (find / click / go to / search for).
_NAV_VERB = (
    r"(?:search\s+(?:for\s+)?|find\s+(?:the\s+)?|click\s+(?:on\s+)?|"
    r"go\s+to\s+|navigate\s+to\s+|open\s+)"
)


@dataclass
class GoalHint:
    goal: GoalType
    target_app: str | None = None
    payload: str | None = None    # text to type / query to search / contact name
    query: str | None = None      # named control / result selector phrase
    then: str | None = None       # follow-up action: play|open|select|send_message
    message: str | None = None    # message body for messaging follow-ups


def _clean_app(name: str | None) -> str | None:
    if not name:
        return None
    name = re.sub(r"\s+", " ", name).strip(" .,!\"'")
    name = re.sub(r"^(?:my|the|a)\s+", "", name).strip()
    return name or None


def _clean_query(q: str | None) -> str | None:
    if not q:
        return None
    q = q.strip().strip("\"'").strip()
    low = q.lower()
    for noise in _QUERY_NOISE:
        if low.startswith(noise + " "):
            q = q[len(noise):].strip()
            low = q.lower()
    return q.strip(" .,!\"'") or None


def _clean_msg(m: str | None) -> str | None:
    if not m:
        return None
    return m.strip().strip("\"'").strip() or None


def _strip_contact_nav(s: str) -> str:
    """Remove leading navigation verb + article from a contact/chat name."""
    s = re.sub(r"^(?:the\s+)?(?:chat|contact|person|user)?\s*(?:named\s+)?", "", s, flags=re.IGNORECASE)
    return s.strip().strip("\"'").strip()


def _extract_then(t: str) -> tuple[str, str | None]:
    """Split off a trailing follow-up action like 'and play it'.

    Returns (remaining_text, then_action). Only matches media-style verbs;
    messaging follow-ups are handled by the _SEND_MSG_PAT path.
    """
    m = re.search(
        r"\s+(?:and|then)\s+(play|open|select|start|launch|choose|pick)\b"
        r"(?:\s+(?:it|this|that|the(?: best)?(?: search)? result|the first(?: one| result)?|"
        r"the top(?: one| result)?|the best(?: one| match)?|one))?\s*$",
        t,
    )
    if not m:
        return t, None
    verb = _THEN_VERBS.get(m.group(1), m.group(1))
    return t[: m.start()].strip(), verb


def shortlist(normalized: str) -> GoalHint:
    t = normalized.strip()

    # ── Messaging compound commands ────────────────────────────────────────────
    # Detect "send [a] message [saying] <text>" tail first, then parse the prefix.
    msg_m = _SEND_MSG_PAT.search(t)
    if msg_m:
        msg = _clean_msg(msg_m.group("msg"))
        prefix = t[: msg_m.start()].strip()
        # prefix: "open <app> and [search for|find|click on] [the] [chat named] <contact>"
        p = re.match(
            r"^(?:open|launch|start|go to)\s+(?P<app>.+?)\s+and\s+" + _NAV_VERB +
            r"(?:the\s+)?(?:chat|contact|person|user)?\s*(?:named\s+)?"
            r"['\"]?(?P<contact>.+?)['\"]?\s*$",
            prefix,
            re.IGNORECASE,
        )
        if p:
            return GoalHint(
                GoalType.generic_search,
                _clean_app(p.group("app")),
                _clean_query(_strip_contact_nav(p.group("contact"))),
                then="send_message",
                message=msg,
            )
        # fallback: just an open + message with no contact navigation
        p2 = re.match(r"^(?:open|launch|start|go to)\s+(?P<app>.+)$", prefix, re.IGNORECASE)
        if p2:
            return GoalHint(
                GoalType.generic_text_entry,
                _clean_app(p2.group("app")),
                msg,
            )

    # ── Media: trailing 'and play/open it' ────────────────────────────────────
    t, then = _extract_then(t)

    # open/launch <app> and play <query>
    m = re.search(
        r"\b(?:open|launch|start)\s+(?P<app>.+?)\s+and\s+(?:play|listen\s+to|stream)\s+(?P<q>.+)$", t
    )
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), _clean_query(m.group("q")), then="play")

    # open/launch <app> and search (for) <query>
    m = re.search(
        r"\b(?:open|launch|start|go to)\s+(?P<app>.+?)\s+and\s+search\s+(?:for\s+)?(?P<q>.+)$", t
    )
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), _clean_query(m.group("q")), then=then)

    # search (for) <query> in/on/using/within <app>
    m = re.search(r"\bsearch\s+(?:for\s+)?(?P<q>.+?)\s+(?:in|on|using|within)\s+(?P<app>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), _clean_query(m.group("q")), then=then)

    # in/on <app>, search (for) <query>
    m = re.search(r"\b(?:in|on)\s+(?P<app>.+?)[, ]+search\s+(?:for\s+)?(?P<q>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), _clean_query(m.group("q")), then=then)

    # play <query> in/on <app>  (implicitly a search-and-play)
    m = re.search(r"^play\s+(?P<q>.+?)\s+(?:in|on|using|with)\s+(?P<app>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_search, _clean_app(m.group("app")), _clean_query(m.group("q")), then="play")

    # search (for) <query>  (no app)
    m = re.search(r"^search\s+(?:for\s+)?(?P<q>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_search, None, _clean_query(m.group("q")), then=then)

    # open/launch <app> and type <text>
    m = re.search(r"\b(?:open|launch|start|go to)\s+(?P<app>.+?)\s+and\s+(?:type|write|enter|say)\s+(?P<text>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_text_entry, _clean_app(m.group("app")), m.group("text").strip())

    # open/launch <app> and build/run it
    m = re.search(r"\b(?:open|launch|start)\s+(?P<app>.+?)\s+and\s+(?:build|run|compile|debug)\b", t)
    if m:
        return GoalHint(GoalType.generic_build_or_run, _clean_app(m.group("app")))

    # build/run the project (no explicit app)
    if re.search(r"\b(build|run|compile|debug)\b", t) and re.search(r"\b(project|workspace|solution|it)\b", t):
        m = re.search(r"\bin\s+(?P<app>[a-z0-9 ]+)$", t)
        return GoalHint(GoalType.generic_build_or_run, _clean_app(m.group("app")) if m else None)

    # type/write <text> (no app)
    m = re.search(r"^(?:type|write|enter)\s+(?P<text>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_text_entry, None, m.group("text").strip())

    # click/press/select <control>
    m = re.search(r"^(?:click|press|tap|select|invoke|choose)\s+(?:on\s+)?(?:the\s+)?(?P<ctrl>.+)$", t)
    if m:
        return GoalHint(GoalType.generic_click_named_control, None, None, m.group("ctrl").strip())

    # create/add a meeting/event/note (form)
    m = re.search(r"\b(?:create|add|new)\s+(?:a\s+)?(?P<thing>meeting|event|appointment|note|task|reminder)\b.*?(?:in\s+(?P<app>.+))?$", t)
    if m:
        return GoalHint(GoalType.generic_form_create, _clean_app(m.group("app")))

    # open/focus <app>
    m = re.search(r"^(?:open|launch|start|focus|switch to|go to)\s+(?P<app>.+)$", t)
    if m:
        verb = t.split()[0]
        goal = GoalType.focus_app if verb in ("focus", "switch") else GoalType.open_app
        return GoalHint(goal, _clean_app(m.group("app")))

    if not t:
        return GoalHint(GoalType.no_op)
    return GoalHint(GoalType.clarify)
