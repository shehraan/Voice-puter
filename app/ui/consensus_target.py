"""Three independent scorers vote on which UIA element is the play target."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.ui.elements import UIElement
from app.verifier.verify import _is_nav_name, read_value


@dataclass(frozen=True)
class TargetVote:
    agent: str
    element: UIElement
    confidence: float


def _terms(query: str) -> list[str]:
    import re
    return [w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 1]


def _name_matches(name: str, terms: list[str]) -> bool:
    n = (name or "").strip().lower()
    if not n or not terms:
        return False
    if _is_nav_name(n):
        return False
    return any(
        n == t
        or n.startswith(t + " ")
        or n.startswith(t + "-")
        or n.startswith(t + "(")
        or (t in n and len(n) <= len(t) + 40)
        for t in terms
    )


def _is_playable_row(el: UIElement, *, below_tabs: bool = True) -> bool:
    if el.is_offscreen or not el.is_enabled:
        return False
    if below_tabs and el.rectangle[1] < 90:
        return False
    if el.control_type in ("Edit", "ComboBox", "Document"):
        return False
    n = (el.name or "").strip().lower()
    if n in ("tracks", "albums", "artists", "home", "favorites", "search"):
        return False
    return True


def agent_title_name(
    elements: list[UIElement],
    query: str,
) -> TargetVote | None:
    """Agent 1: visible element name matches the track title."""
    terms = _terms(query)
    best: tuple[float, UIElement] | None = None
    for e in elements:
        if not _is_playable_row(e):
            continue
        if e.control_type not in ("Hyperlink", "ListItem", "DataItem"):
            continue
        name = (e.name or "").strip()
        if not _name_matches(name, terms):
            continue
        score = 10.0
        if name.lower() in terms:
            score += 5.0
        if best is None or score > best[0]:
            best = (score, e)
    if best is None:
        return None
    return TargetVote("title_name", best[1], min(1.0, best[0] / 15.0))


def agent_value_hyperlink(
    elements: list[UIElement],
    query: str,
) -> TargetVote | None:
    """Agent 2: em-dash / unnamed hyperlink whose Value contains the title."""
    terms = _terms(query)
    for e in elements:
        if not _is_playable_row(e):
            continue
        if e.control_type not in ("Hyperlink", "ListItem", "DataItem"):
            continue
        name = (e.name or "").strip()
        if name not in ("—", "-", "") and _name_matches(name, terms):
            continue
        val = read_value(e).lower()
        if not terms or not any(t in val for t in terms):
            continue
        hits = sum(1 for t in terms if t in val)
        return TargetVote("value_hyperlink", e, min(1.0, 0.55 + hits * 0.15))
    return None


def agent_row_band_name(
    elements: list[UIElement],
    query: str,
) -> TargetVote | None:
    """Agent 3: row band whose visible names (not Values) contain the title."""
    terms = _terms(query)
    bands: dict[int, list[UIElement]] = {}
    for e in elements:
        if not _is_playable_row(e):
            continue
        if e.control_type not in ("Hyperlink", "ListItem", "DataItem", "Button", "Text"):
            continue
        bands.setdefault(e.rectangle[1] // 36, []).append(e)

    best_band: tuple[float, int] | None = None
    for band_id, elems in bands.items():
        names = " ".join((el.name or "") for el in elems).lower()
        name_hits = sum(1 for t in terms if t in names)
        if name_hits == 0:
            continue
        score = name_hits * 6.0
        if best_band is None or score > best_band[0]:
            best_band = (score, band_id)

    if best_band is None:
        return None

    row_elems = bands[best_band[1]]
    titled = [
        e for e in row_elems
        if e.control_type in ("Hyperlink", "ListItem", "DataItem")
        and _name_matches(e.name or "", terms)
    ]
    if titled:
        el = min(titled, key=lambda e: e.rectangle[0])
        return TargetVote("row_band_name", el, min(1.0, best_band[0] / 12.0))

    val_titled = [
        e for e in row_elems
        if e.control_type in ("Hyperlink", "ListItem", "DataItem")
        and any(t in read_value(e).lower() for t in terms)
    ]
    if val_titled:
        el = min(val_titled, key=lambda e: e.rectangle[0])
        return TargetVote("row_band_name", el, min(1.0, best_band[0] / 14.0))
    return None


def _row_band(el: UIElement) -> int:
    return el.rectangle[1] // 36


def _pick_title_in_band(elements: list[UIElement], band_id: int, terms: list[str]) -> UIElement | None:
    row_elems = [
        e for e in elements
        if _row_band(e) == band_id and _is_playable_row(e)
        and e.control_type in ("Hyperlink", "ListItem", "DataItem", "Button")
    ]
    titled = [
        e for e in row_elems
        if e.control_type in ("Hyperlink", "ListItem", "DataItem")
        and _name_matches(e.name or "", terms)
    ]
    if titled:
        return min(titled, key=lambda e: e.rectangle[0])
    val_titled = [
        e for e in row_elems
        if e.control_type in ("Hyperlink", "ListItem", "DataItem")
        and any(t in read_value(e).lower() for t in terms)
    ]
    if val_titled:
        return min(val_titled, key=lambda e: e.rectangle[0])
    return None


def consensus_play_target(
    elements: list[UIElement],
    query: str,
    *,
    min_votes: int = 2,
    min_confidence: float = 0.5,
) -> tuple[UIElement | None, list[TargetVote]]:
    """Return the element chosen by a majority of agents on the same result row."""
    terms = _terms(query)
    votes = [
        v for v in (
            agent_title_name(elements, query),
            agent_value_hyperlink(elements, query),
            agent_row_band_name(elements, query),
        )
        if v is not None
    ]
    if not votes:
        return None, votes

    band_counts = Counter(_row_band(v.element) for v in votes)
    band_id, tally = band_counts.most_common(1)[0]
    if tally < min_votes:
        return None, votes

    agreeing = [v for v in votes if _row_band(v.element) == band_id]
    avg_conf = sum(v.confidence for v in agreeing) / len(agreeing)
    if avg_conf < min_confidence:
        return None, votes

    el = _pick_title_in_band(elements, band_id, terms)
    if el is None:
        el = agreeing[0].element
    return el, votes
