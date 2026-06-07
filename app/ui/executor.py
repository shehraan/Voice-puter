"""Grounded, safe UI action executor.

Exposes only an allowlist of primitives. Every targeted action resolves to either an
element from the current observation registry or a revalidated selector-cache entry.
No pixel coordinates, no blind clicks, no typing into an unverified window
(ui-automation-visible.md / security.md).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import keyboard
import win32gui
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.uia_defines import get_elem_interface
from pywinauto.uia_element_info import UIAElementInfo

from app.cache.selector_cache import SelectorCache
from app.core.config import Config
from app.core.trace import Trace
from app.launch.resolver import AppResolver, ActiveWindow, set_foreground
from app.launch.windows import chromium_render_children, enable_chromium_accessibility
from app.safety import guardrails
from app.planner.schema import Action
from app.ui.consensus_target import consensus_play_target
from app.ui.elements import Observation, UIElement
from app.ui.observe import observe_window
from app.ui.selectors import rank_elements
from app.verifier.verify import read_value, search_results_page_visible, verify_postcondition

_MIN_FIND_SCORE = 3.0


@dataclass
class ActionResult:
    op: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Executor:
    def __init__(self, cfg: Config, cache: SelectorCache, trace: Trace, resolver: AppResolver | None = None):
        self.cfg = cfg
        self.cache = cache
        self.trace = trace
        self.resolver = resolver or AppResolver()
        self.window: ActiveWindow | None = None
        self.observation: Observation | None = None
        self._last_target: UIElement | None = None
        self._used: list[tuple[str, UIElement, bool]] = []
        self._a11y_nudged: set[int] = set()
        self._pending_play_target: UIElement | None = None

    # ---- observation -------------------------------------------------------
    def observe(self) -> Observation | None:
        if not self.window:
            return None
        top = self.window.hwnd
        try:
            obs = observe_window(self.window.info, self.cfg.loop)
        except Exception as exc:  # transient UIA/COM failure: keep last observation
            self.trace.log("observe_error", detail=str(exc))
            return self.observation

        # Chromium/Electron apps (Spotify, Discord, browsers) expose their web content on
        # a renderer child window, not the frame. Merge the frame (omnibox, tabs, native
        # chrome) with the renderer children (real search box, results) into one
        # observation so grounding can see everything.
        renders = chromium_render_children(top)
        if renders:
            if top not in self._a11y_nudged:
                enable_chromium_accessibility(top)
                self._a11y_nudged.add(top)
                time.sleep(0.3)
            merged = list(obs.elements)
            seen = {e.runtime_id for e in merged if e.runtime_id}
            for h in renders:
                try:
                    cand = observe_window(UIAElementInfo(h), self.cfg.loop)
                except Exception:
                    continue
                for e in cand.elements:
                    if e.runtime_id and e.runtime_id in seen:
                        continue
                    seen.add(e.runtime_id)
                    merged.append(e)
            registry: dict[str, UIElement] = {}
            for i, e in enumerate(merged):
                e.selector_id = f"obs_{i}"
                registry[e.selector_id] = e
            obs = Observation(window=obs.window, elements=merged, registry=registry)
        self.observation = obs
        return self.observation

    def _refresh_chromium_observation(self) -> Observation | None:
        """Re-nudge Chromium accessibility so search-result rows stay in the UIA tree."""
        if self.window:
            enable_chromium_accessibility(self.window.hwnd)
            time.sleep(0.4)
        return self.observe()

    def _rehydrate_element(self, el: UIElement | None) -> UIElement | None:
        """Map a prior observation element onto the current registry by runtime_id."""
        if el is None or not self.observation:
            return el
        rid = el.runtime_id
        if rid:
            for candidate in self.observation.elements:
                if candidate.runtime_id == rid:
                    return candidate
        return el

    def _play_button_in_row(self, anchor: UIElement) -> UIElement | None:
        """Unnamed play/queue control in the same result row band as *anchor*."""
        row_y = anchor.rectangle[1]
        band = self._row_band_elements(row_y, tolerance=32)
        icons = [
            e for e in band
            if e.control_type == "Button"
            and not (e.name or "").strip()
            and "Invoke" in e.supported_patterns
            and e.is_enabled and not e.is_offscreen
        ]
        if icons:
            return min(icons, key=lambda e: e.rectangle[0])
        groups = [
            e for e in band
            if e.control_type == "Group"
            and "Invoke" in e.supported_patterns
            and e.is_enabled and not e.is_offscreen
        ]
        if groups:
            return min(groups, key=lambda e: e.rectangle[0])
        return None

    def app_key(self) -> str:
        win = getattr(self, "window", None)
        return win.app_key if win else ""

    # ---- target grounding --------------------------------------------------
    def _resolve_target(self, action: Action) -> tuple[UIElement | None, str]:
        sid = action.target.selector_id
        role = action.target.semantic_role
        if sid:
            if not self.observation:
                return None, "no current observation to resolve selector_id"
            el = self.observation.find(sid)
            if el is None:
                return None, f"selector_id {sid!r} not in current observation (refusing fabricated selector)"
            return el, "from observation"
        if role:
            el = self._find_by_role(role, action.args)
            if el is None:
                return None, f"no control matched semantic_role {role!r}"
            return el, "by semantic_role"
        if self._last_target is not None:
            return self._last_target, "last target"
        return None, "no target specified"

    def _find_by_role(self, role: str, args: dict) -> UIElement | None:
        if not self.observation:
            return None
        if role == "search_or_input":
            grounded = self._find_search_field()
            if grounded is not None:
                self._last_target = grounded
                self._used.append((role, grounded, False))
                return grounded
        name_hints = args.get("name_contains_any") or args.get("name_contains") or []
        if isinstance(name_hints, str):
            name_hints = [name_hints]
        preferred = args.get("preferred_control_types") or []

        desc = self.cache.get(self.app_key(), role)
        if desc:
            cached = self.cache.match_in_observation(desc, self.observation)
            if cached is not None:
                self._last_target = cached
                self._used.append((role, cached, True))
                return cached

        ranked = rank_elements(self.observation.elements, role, name_hints, preferred)
        if not ranked or ranked[0].score < _MIN_FIND_SCORE:
            return None
        el = ranked[0].element
        self._last_target = el
        self._used.append((role, el, False))
        return el

    # ---- result selection / activation -------------------------------------
    # Element name prefixes that indicate a navigation/command suggestion rather than
    # an actual content item. Clicking these changes the view but doesn't complete a
    # play/select goal — they're intermediate steps on the path.
    _NAV_SUGGESTION_PREFIXES = (
        "search for ", "go to ", "show ", "browse ", "see all ", "more results",
        "show more", "all results", "view all",
    )

    @staticmethod
    def _is_nav_suggestion(name: str) -> bool:
        """True when the element is a navigation command, not a playable/selectable result."""
        n = name.strip().lower()
        return any(n.startswith(p) for p in Executor._NAV_SUGGESTION_PREFIXES)

    @staticmethod
    def _element_search_text(el: UIElement) -> str:
        """Lowercased name + Value/Text content for query matching."""
        return f"{el.name or ''} {read_value(el)}".strip().lower()

    def _sidebar_boundary(self) -> int | None:
        if not self.observation:
            return None
        from app.verifier.verify import _sidebar_boundary
        return _sidebar_boundary(self.observation)

    def _is_content_area(self, el: UIElement) -> bool:
        """Main content pane when a left nav sidebar is present."""
        boundary = self._sidebar_boundary()
        if boundary is None:
            return True
        return el.rectangle[0] > boundary

    def _uses_sidebar_layout(self) -> bool:
        return self._sidebar_boundary() is not None

    @staticmethod
    def _is_sidebar_noise(name: str) -> bool:
        """Library sidebar entries that are not search-result rows."""
        n = (name or "").strip().lower()
        if n in ("home", "favorites", "albums", "tracks", "artists", "genres",
                 "settings", "playlist", "search", "my library"):
            return True
        if n.endswith("...") or n in ("search...", "create playlist...", "server commands..."):
            return True
        if "playlist" in n and len(n) > 12:
            return True
        return False

    @staticmethod
    def _is_search_page_tab(el: UIElement) -> bool:
        """Search-results header tabs (Tracks/Albums/Artists) — not playable rows."""
        n = (el.name or "").strip().lower()
        if n not in ("tracks", "albums", "artists"):
            return False
        return el.rectangle[1] < 280

    @staticmethod
    def _is_result_metadata_label(name: str) -> bool:
        """Table column headers / section labels — not playable result rows."""
        n = (name or "").strip().lower()
        if n in (
            "album artist", "album artists", "view all tracks", "title", "album",
            "artist", "duration", "genre", "year", "track", "tracks", "albums",
            "artists", "spotify", "actions",
        ):
            return True
        return n.endswith(" results") or n.endswith(" result")

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        return [w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 1]

    @staticmethod
    def _name_matches_query(name: str, terms: list[str]) -> bool:
        """True when the visible label is the sought item (track title), not a sibling column."""
        n = (name or "").strip().lower()
        if not n or not terms:
            return False
        if Executor._is_nav_suggestion(n):
            return False
        return any(
            n == t
            or n.startswith(t + " ")
            or n.startswith(t + "-")
            or n.startswith(t + "(")
            or (t in n and len(n) <= len(t) + 40)
            for t in terms
        )

    def _row_band_elements(self, y: int, *, tolerance: int = 20) -> list[UIElement]:
        if not self.observation:
            return []
        return [
            e for e in self.observation.elements
            if self._is_content_area(e)
            and abs(e.rectangle[1] - y) <= tolerance
            and e.is_enabled and not e.is_offscreen
            and e.control_type not in self._INPUT_TYPES
        ]

    def find_consensus_play_target(self, query: str) -> UIElement | None:
        """Three scorers vote on the play target; require majority agreement."""
        if not self.observation:
            return None
        el, votes = consensus_play_target(self.observation.elements, query)
        if el is not None:
            self._last_target = el
            self._used.append(("result_item", el, False))
        if getattr(self, "trace", None) and votes:
            self.trace.log(
                "consensus_play",
                ok=el is not None,
                detail=f"{sum(1 for v in votes if el and v.element.selector_id == el.selector_id)}/"
                       f"{len(votes)} agents agree"
                       if el
                       else f"no majority among {len(votes)} agent picks",
                votes=[{"agent": v.agent, "selector_id": v.element.selector_id, "confidence": v.confidence}
                       for v in votes],
            )
        return el

    def _on_search_results_view(self, query: str = "") -> bool:
        from app.verifier.verify import _on_search_route, search_results_page_visible
        obs = self.observation
        return bool(obs and (search_results_page_visible(obs, query) or _on_search_route(obs)))

    def _find_query_result_row(self, query: str) -> UIElement | None:
        """Pick the play/open target in the result row that matches the query title.

        Chromium tables often expose artist/album links whose Value contains the full row
        text (including the track title) — matching on read_value alone picks the wrong
        column. Prefer elements whose *name* matches the query; otherwise score row bands.
        """
        if not self.observation:
            return None
        terms = self._query_terms(query)
        if not terms:
            return None

        rowish = ("Hyperlink", "ListItem", "DataItem", "Text", "Custom")
        title_hits: list[tuple[float, UIElement]] = []
        for e in self.observation.elements:
            if e.is_offscreen or not e.is_enabled:
                continue
            if e.control_type in self._INPUT_TYPES:
                continue
            if self._is_sidebar_noise(e.name) or self._is_result_metadata_label(e.name):
                continue
            if self._is_search_page_tab(e):
                continue
            if self._uses_sidebar_layout() and not self._is_content_area(e) and not self._on_search_results_view(query):
                continue
            if self._is_nav_suggestion(self._element_search_text(e)):
                continue
            if e.control_type not in rowish:
                continue
            name = (e.name or "").strip()
            if not self._name_matches_query(name, terms):
                val = read_value(e).lower()
                if not (
                    (name in ("—", "-") or (not name and val))
                    and e.control_type in ("Hyperlink", "ListItem", "DataItem")
                    and any(t in val for t in terms)
                ):
                    continue
            score = 10.0
            nl = name.lower()
            if any(nl == t for t in terms):
                score += 8.0
            if e.control_type in ("Hyperlink", "ListItem", "DataItem"):
                score += 3.0
            if "Invoke" in e.supported_patterns or "SelectionItem" in e.supported_patterns:
                score += 1.0
            title_hits.append((score, e))
        if title_hits:
            title_hits.sort(key=lambda c: c[0], reverse=True)
            el = title_hits[0][1]
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        # Row-band scoring: group content elements by vertical position.
        anchors = [
            e for e in self.observation.elements
            if (self._is_content_area(e) or self._on_search_results_view(query))
            and 160 < e.rectangle[1] < 860
            and e.control_type in ("Hyperlink", "ListItem", "DataItem", "Button", "Text")
            and not self._is_sidebar_noise(e.name)
            and not self._is_result_metadata_label(e.name)
            and not self._is_search_page_tab(e)
            and e.is_enabled and not e.is_offscreen
        ]
        bands: dict[int, list[UIElement]] = {}
        for e in anchors:
            bands.setdefault(e.rectangle[1] // 36, []).append(e)

        best_band: tuple[float, int] | None = None
        for band_id, elems in bands.items():
            names = " ".join((el.name or "") for el in elems).lower()
            row_text = " ".join(self._element_search_text(el) for el in elems)
            name_hits = sum(1 for t in terms if t in names)
            text_hits = sum(1 for t in terms if t in row_text)
            if name_hits == 0 and text_hits == 0:
                continue
            score = name_hits * 6.0 + text_hits * 2.0
            if best_band is None or score > best_band[0]:
                best_band = (score, band_id)

        if best_band is None:
            return None

        row_elems = bands[best_band[1]]
        row_y = min(e.rectangle[1] for e in row_elems)

        # Prefer the title hyperlink in the matched row.
        titled = [
            e for e in row_elems
            if e.control_type in ("Hyperlink", "ListItem", "DataItem")
            and self._name_matches_query(e.name or "", terms)
        ]
        if titled:
            el = min(titled, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        # Unnamed play/album-art icon in the same row band.
        buttons = [
            e for e in row_elems
            if e.control_type == "Button"
            and not (e.name or "").strip()
            and "Invoke" in e.supported_patterns
        ]
        if buttons:
            el = min(buttons, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        # Digit row index + leftmost icon button (common Chromium table layout).
        band = self._row_band_elements(row_y)
        digit_y = [
            e for e in band
            if e.control_type == "Button" and (e.name or "").strip().isdigit()
        ]
        if digit_y:
            row_y = digit_y[0].rectangle[1]
            band = self._row_band_elements(row_y)
            icons = [
                e for e in band
                if e.control_type == "Button"
                and not (e.name or "").strip()
                and "Invoke" in e.supported_patterns
            ]
            if icons:
                el = min(icons, key=lambda e: e.rectangle[0])
                self._last_target = el
                self._used.append(("result_item", el, False))
                return el

        return None

    _INPUT_TYPES = frozenset({"Edit", "ComboBox", "Document"})

    def _find_nav_suggestion(self, query: str) -> UIElement | None:
        """Dropdown navigation row ('Search for X') before the full results page."""
        if not self.observation:
            return None
        terms = [w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 1]
        for e in self.observation.elements:
            if e.is_offscreen or not e.is_enabled:
                continue
            if not self._is_content_area(e) and e.control_type not in ("ListItem", "Hyperlink"):
                continue
            text = self._element_search_text(e)
            if not self._is_nav_suggestion(text):
                continue
            if terms and not any(t in text for t in terms):
                continue
            self._last_target = e
            self._used.append(("result_item", e, False))
            return e
        return None

    def _is_header_chrome(self, el: UIElement) -> bool:
        """Header band controls (search field, tabs) — never valid play targets."""
        if el.control_type in self._INPUT_TYPES:
            return True
        return el.rectangle[1] < 130

    def _find_table_row_play_button(self, query: str = "") -> UIElement | None:
        """Play target in the result row matching the query (not the first table row)."""
        if not self.observation or not search_results_page_visible(self.observation, query):
            return None
        return self._find_query_result_row(query)

    def _find_search_datagrid_row(self, query: str) -> UIElement | None:
        """Top search-result row when UIA exposes a DataGrid but not the track title."""
        if not self.observation or not self._on_search_results_view(query):
            return None
        from app.verifier.verify import _on_search_route

        if not _on_search_route(self.observation):
            return None
        terms = self._query_terms(query)
        if terms:
            header_ok = _on_search_route(self.observation) or any(
                any(t in read_value(e).lower() or t in (e.name or "").lower() for t in terms)
                for e in self.observation.elements
                if e.control_type in ("Edit", "ComboBox") and e.rectangle[1] < 120
            )
            if not header_ok:
                return None

        in_results = self._on_search_results_view(query)

        grids = [
            e for e in self.observation.elements
            if e.control_type == "DataGrid"
            and (self._is_content_area(e) or in_results)
            and 100 < e.rectangle[1] < 900
            and e.is_enabled and not e.is_offscreen
        ]
        if not grids:
            return None

        def _row_has_result_links(y: int) -> bool:
            for e in self.observation.elements:
                if not (self._is_content_area(e) or in_results):
                    continue
                if abs(e.rectangle[1] - y) > 85:
                    continue
                if e.control_type != "Hyperlink":
                    continue
                if self._is_search_page_tab(e) or self._is_sidebar_noise(e.name):
                    continue
                if (e.name or "").strip():
                    return True
            return False

        grids.sort(key=lambda g: g.rectangle[1])
        grid = grids[0]
        for g in grids:
            if _row_has_result_links(g.rectangle[1]):
                grid = g
                break
        row_y = grid.rectangle[1]

        titled = [
            e for e in self.observation.elements
            if (self._is_content_area(e) or in_results)
            and abs(e.rectangle[1] - row_y) <= 60
            and e.control_type in ("Hyperlink", "ListItem", "DataItem")
            and not self._is_search_page_tab(e)
            and not self._is_sidebar_noise(e.name)
            and self._name_matches_query(e.name or "", terms)
            and e.is_enabled and not e.is_offscreen
        ]
        if titled:
            el = min(titled, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        row_groups = [
            e for e in self.observation.elements
            if (self._is_content_area(e) or in_results)
            and abs(e.rectangle[1] - row_y) <= 65
            and e.control_type == "Group"
            and "Invoke" in e.supported_patterns
            and not self._is_header_chrome(e)
            and e.is_enabled and not e.is_offscreen
        ]
        if row_groups:
            el = max(row_groups, key=lambda e: e.rectangle[2] - e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        play_icons = [
            e for e in self.observation.elements
            if e.control_type == "Button"
            and not (e.name or "").strip()
            and "Invoke" in e.supported_patterns
            and (self._is_content_area(e) or in_results)
            and row_y + 40 <= e.rectangle[1] <= row_y + 130
            and e.is_enabled and not e.is_offscreen
        ]
        if play_icons:
            el = min(play_icons, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        row_links = [
            e for e in self.observation.elements
            if (self._is_content_area(e) or in_results)
            and abs(e.rectangle[1] - row_y) <= 65
            and e.control_type == "Hyperlink"
            and not self._is_search_page_tab(e)
            and not self._is_sidebar_noise(e.name)
            and not self._is_result_metadata_label(e.name)
            and not self._is_header_chrome(e)
            and e.is_enabled and not e.is_offscreen
        ]
        if row_links:
            el = min(row_links, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        items = [
            e for e in self.observation.elements
            if e.control_type == "DataItem"
            and (self._is_content_area(e) or in_results)
            and abs(e.rectangle[1] - row_y) <= 60
            and e.is_enabled and not e.is_offscreen
        ]
        if items:
            el = min(items, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el

        groups = [
            e for e in self.observation.elements
            if e.control_type == "Group"
            and (self._is_content_area(e) or in_results)
            and abs(e.rectangle[1] - row_y) <= 60
            and "Invoke" in e.supported_patterns
            and e.is_enabled and not e.is_offscreen
        ]
        if groups:
            el = min(groups, key=lambda e: e.rectangle[0])
            self._last_target = el
            self._used.append(("result_item", el, False))
            return el
        return None

    def _find_first_result_row(self, query: str) -> UIElement | None:
        """Fallback when row-band logic needs a second pass on the results page."""
        return self._find_query_result_row(query)

    def find_best_result(self, query: str, mode: str = "open", *, exclude_nav: bool = False) -> UIElement | None:
        """Pick the best visible result control matching the query.

        Considers both result-like rows (ListItem/DataItem/Hyperlink) and clickable
        action buttons whose name matches the query (e.g. a 'Play Narcos' button), then
        scores by query-term overlap. Grounded purely in the current observation.

        When mode='play', navigation suggestion elements ('Search for X', 'Go to X')
        are scored very low so actual content rows always win. They only win if no
        content row is visible yet (i.e. still in a dropdown before the results page).

        When exclude_nav=True, navigation suggestions are skipped entirely (stage 2).
        """
        if not self.observation:
            return None
        if mode == "play" and exclude_nav:
            row_match = self._find_query_result_row(query)
            if row_match is not None:
                return row_match
        terms = self._query_terms(query)
        candidates: list[tuple[float, UIElement]] = []
        for e in self.observation.elements:
            if e.is_offscreen or not e.is_enabled:
                continue
            if e.control_type in self._INPUT_TYPES:
                continue
            text = self._element_search_text(e)
            if exclude_nav and self._is_nav_suggestion(text):
                continue
            if self._is_sidebar_noise(e.name) or self._is_result_metadata_label(e.name):
                continue
            if self._is_search_page_tab(e):
                continue
            if self._uses_sidebar_layout() and not self._is_content_area(e):
                continue
            name_l = (e.name or "").strip().lower()
            name_hits = sum(1 for t in terms if t in name_l) if name_l else 0
            text_hits = sum(1 for t in terms if t in text)
            # Play on a results page: title must be in the visible name, not just Value.
            if mode == "play" and exclude_nav and e.control_type in (
                "Hyperlink", "ListItem", "DataItem", "TreeItem",
            ):
                if not self._name_matches_query(e.name or "", terms):
                    continue
                term_hits = name_hits
            else:
                term_hits = text_hits
            if terms and term_hits == 0:
                continue
            is_rowish = e.control_type in ("ListItem", "DataItem", "TreeItem", "Hyperlink")
            is_clickable = e.control_type in ("Button", "SplitButton", "MenuItem") and "Invoke" in e.supported_patterns
            score = term_hits * 3.0
            if self._name_matches_query(e.name or "", terms):
                score += 8.0
            if any(name_l == t for t in terms):
                score += 5.0
            if is_rowish:
                score += 2.0
            if is_clickable:
                score += 1.0
                if text.startswith(("play", "open")):
                    score += 2.5
            if mode == "play" and self._is_nav_suggestion(text):
                score = min(score, 0.5)
            candidates.append((score, e))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0], reverse=True)
        el = candidates[0][1]
        self._last_target = el
        self._used.append(("result_item", el, False))
        return el

    def _find_search_field(self) -> UIElement | None:
        """The app's real search input, ignoring the phantom Chromium omnibox."""
        if not self.observation:
            return None
        best, best_score = None, 0.0
        for e in self.observation.elements:
            if e.control_type not in ("Edit", "ComboBox") or "Value" not in e.supported_patterns:
                continue
            nl = (e.name or "").lower()
            if nl == "address and search bar":  # Chromium phantom omnibox
                continue
            # Modal/dialog panels often expose unnamed Edits below the header band.
            y_top = e.rectangle[1]
            if not nl and y_top > 150:
                continue
            score = 5.0 if e.control_type == "ComboBox" else 4.0
            if nl == "search":
                score += 5.0
            elif any(k in nl for k in ("what do you want", "search", "find", "query")):
                score += 3.0
            # Prefer the app-wide header search; deprioritize in-panel boxes (e.g. lyrics).
            if y_top < 120:
                score += 6.0
            elif y_top > 200:
                score -= 8.0
            if e.has_keyboard_focus and y_top > 200:
                score -= 2.0
            elif e.has_keyboard_focus:
                score += 1.0
            if e.is_enabled and not e.is_offscreen:
                score += 0.5
            if score > best_score:
                best, best_score = e, score
        return best

    def search_via_shortcut(self, shortcut: str, query: str) -> ActionResult:
        """Open the app's own search via its hotkey and type the query.

        Used for Chromium/Electron apps (e.g. Spotify Ctrl+K) whose real search box is
        not reliably exposed until navigated to. The window is verified foreground, the
        shortcut opens the app's search page, and we ground onto the real search field.
        """
        if not query:
            return ActionResult("search_via_shortcut", False, "no query to search")
        if not self._ensure_foreground():
            return ActionResult("search_via_shortcut", False, "target window not foreground")

        # Put keyboard focus inside the web content so the app's shortcut is not swallowed
        # by the native frame (common for Chromium/Electron apps after foregrounding).
        self.observe()
        doc = next((e for e in (self.observation.elements if self.observation else [])
                    if e.control_type == "Document"), None)
        if doc is not None:
            try:
                UIAWrapper(doc.info).set_focus()
            except Exception:
                pass
            time.sleep(0.2)
        keyboard.send(shortcut.replace(" ", ""))

        # Wait for the search page to render and expose its real search field.
        el = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.4)
            self.observe()
            el = self._find_search_field()
            if el is not None:
                break

        where = "search field"
        if el is not None:
            verdict = guardrails.check_element_target(el)
            if not verdict.allowed:
                return ActionResult("search_via_shortcut", False, verdict.reason)
            self._focus_element(el)
            where = el.name or where

        # Prefer atomic ValuePattern.SetValue() so multi-word queries land in full.
        # Fall back to keyboard only when the control doesn't support ValuePattern.
        method = ""
        if el is not None and "Value" in el.supported_patterns:
            try:
                self._pattern(el, "Value").SetValue(query)
                method = "set_value"
            except Exception:
                method = ""

        if not method:
            # Clear whatever text is already in the field before typing.
            keyboard.send("ctrl+a")
            time.sleep(0.12)
            keyboard.send("delete")
            time.sleep(0.12)
            keyboard.write(query, delay=self.cfg.timing.type_char_delay_s)
            method = "keyboard"

        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("search_via_shortcut", True, f"typed {query!r} via {method} into {where!r}",
                            {"selector_id": el.selector_id if el else None})

    def _find_compose_field(self) -> UIElement | None:
        """The message compose / text-input field (distinct from search boxes)."""
        if not self.observation:
            return None
        best, best_score = None, 0.0
        for e in self.observation.elements:
            if not e.is_enabled or e.is_offscreen:
                continue
            if e.control_type not in ("Edit", "Document"):
                continue
            if "Value" not in e.supported_patterns and "Text" not in e.supported_patterns:
                continue
            nl = (e.name or "").lower()
            aid = (e.automation_id or "").lower()
            score = 0.0
            # Prefer unnamed empty-name Edit (messaging compose fields rarely have a name)
            if not nl:
                score += 4.0
            # Penalise controls that look like search boxes
            if any(k in nl for k in ("search", "find", "query", "address", "url")):
                score -= 3.0
            # Prefer if has Value pattern (can be set atomically)
            if "Value" in e.supported_patterns:
                score += 2.0
            if e.has_keyboard_focus:
                score += 1.0
            if score > best_score:
                best, best_score = e, score
        return best

    def _find_send_button(self) -> UIElement | None:
        """The Send / Submit button for the compose area."""
        if not self.observation:
            return None
        for e in self.observation.elements:
            if not e.is_enabled or e.is_offscreen:
                continue
            if e.control_type not in ("Button", "SplitButton"):
                continue
            if "Invoke" not in e.supported_patterns:
                continue
            nl = (e.name or "").lower()
            if any(k in nl for k in ("send", "submit", "post", "reply", "message in")):
                return e
        return None

    def send_message_to_chat(self, message: str) -> ActionResult:
        """Type a message into the compose field and invoke the Send button.

        Grounded entirely via UIA: finds the compose Edit, sets its value atomically,
        then invokes the Send button by name. No keyboard shortcuts required.
        """
        if not message:
            return ActionResult("send_message", False, "no message text to send")
        self.observe()
        compose = self._find_compose_field()
        if compose is None:
            return ActionResult("send_message", False, "could not find compose field")
        verdict = guardrails.check_element_target(compose)
        if not verdict.allowed:
            return ActionResult("send_message", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("send_message", False, "target window not foreground")
        self._focus_element(compose)

        # Write the message via ValuePattern for reliability; keyboard fallback.
        if "Value" in compose.supported_patterns:
            try:
                self._pattern(compose, "Value").SetValue(message)
            except Exception:
                keyboard.write(message, delay=self.cfg.timing.type_char_delay_s)
        else:
            keyboard.write(message, delay=self.cfg.timing.type_char_delay_s)
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)

        send = self._find_send_button()
        if send is not None:
            try:
                self._pattern(send, "Invoke").Invoke()
                time.sleep(self.cfg.timing.after_action_ms / 1000.0)
                return ActionResult("send_message", True, f"sent {message!r} via send button {send.name!r}",
                                    {"selector_id": compose.selector_id})
            except Exception as exc:
                return ActionResult("send_message", False, f"could not invoke send button: {exc}")
        # Fallback: Enter key (common in messaging apps when send button isn't found)
        keyboard.send("enter")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("send_message", True, f"sent {message!r} via enter fallback",
                            {"selector_id": compose.selector_id})

    def activate_best_result(
        self,
        query: str,
        mode: str = "open",
        *,
        exclude_nav: bool = False,
        hint_el: UIElement | None = None,
    ) -> ActionResult:
        """Find the best result for the query and visibly activate it (play/open/select).

        Returns data["is_nav_suggestion"]=True when the clicked element is a navigation
        command (e.g. "Search for X") rather than an actual content item. The caller
        should treat this as an intermediate navigation step, not goal completion.
        """
        if hint_el is None:
            hint_el = self._pending_play_target
        saved = hint_el or self._pending_play_target
        el: UIElement | None = None
        if saved is not None:
            el = saved
            if exclude_nav and mode == "play" and self._is_nav_suggestion(self._element_search_text(el)):
                self._refresh_chromium_observation()
                el = None
        else:
            self._refresh_chromium_observation()
        if el is None and exclude_nav and mode == "play":
            el = self.find_consensus_play_target(query)
        if el is None and exclude_nav and mode == "play":
            el = self._find_table_row_play_button(query)
        if el is None and exclude_nav and mode == "play":
            el = self._find_search_datagrid_row(query)
        if el is None:
            el = self.find_best_result(query, mode=mode, exclude_nav=exclude_nav)
        if el is None and not exclude_nav and mode == "open":
            el = self._find_nav_suggestion(query)
        if el is None and exclude_nav and mode == "play":
            el = self._find_first_result_row(query)
        if el is None:
            if exclude_nav and mode == "play":
                return ActionResult(
                    "activate_result", False,
                    f"no consensus play target for {query!r} (requires named UIA row, no keyboard shortcuts)",
                )
            return ActionResult("activate_result", False, f"no result matched {query!r}")
        if exclude_nav and mode == "play" and self._is_nav_suggestion(self._element_search_text(el)):
            return ActionResult(
                "activate_result", False,
                f"refusing nav suggestion {el.name!r} during play (not a track row)",
            )
        if self._is_search_page_tab(el):
            return ActionResult(
                "activate_result", False,
                f"refusing to activate search tab {el.name!r} instead of a track row",
            )
        if exclude_nav and mode == "play" and self._is_header_chrome(el):
            return ActionResult(
                "activate_result", False,
                f"refusing to play header control {el.name!r} (y={el.rectangle[1]})",
            )
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("activate_result", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("activate_result", False, "target window not foreground")
        self._focus_element(el)

        el_text = self._element_search_text(el)
        is_nav = self._is_nav_suggestion(el_text)

        if mode == "play" and not is_nav:
            play_ctrl = self._play_button_in_row(el)
            if play_ctrl is not None:
                el = play_ctrl
                self._focus_element(el)

        if mode == "select":
            try:
                self._pattern(el, "SelectionItem").Select()
            except Exception:
                pass
            time.sleep(self.cfg.timing.after_action_ms / 1000.0)
            return ActionResult("activate_result", True, f"selected {el.name!r}",
                                {"selector_id": el.selector_id, "is_nav_suggestion": is_nav})

        method = ""

        # Navigation suggestions (e.g. "Search for X"): a single Invoke/click navigates
        # to the results page. Never double-click these — that's only for content rows.
        if is_nav:
            if "Invoke" in el.supported_patterns:
                try:
                    self._pattern(el, "Invoke").Invoke()
                    method = "invoke"
                except Exception:
                    pass
            if not method:
                try:
                    UIAWrapper(el.info).click_input()
                    method = "click"
                except Exception:
                    pass

        # Content rows in play mode: double-click starts playback in Feishin-like apps.
        # Must run before Group Invoke (which only focuses the row).
        if not method and mode == "play" and not is_nav and el.control_type in (
            "Hyperlink", "ListItem", "DataItem", "Group",
        ):
            try:
                wrapper = UIAWrapper(el.info)
                wrapper.click_input()
                time.sleep(0.1)
                wrapper.click_input()
                method = "double_click"
            except Exception:
                method = ""

        # Named play/queue icon buttons only — not whole row groups.
        if not method and mode == "play" and not is_nav and "Invoke" in el.supported_patterns:
            if el.control_type in ("Button", "SplitButton"):
                try:
                    self._pattern(el, "Invoke").Invoke()
                    method = "invoke"
                except Exception:
                    pass

        # Buttons and other invokable controls: single Invoke.
        if not method and "Invoke" in el.supported_patterns and el.control_type in (
            "Button", "Hyperlink", "MenuItem", "SplitButton", "ListItem",
        ):
            try:
                self._pattern(el, "Invoke").Invoke()
                method = "invoke"
            except Exception:
                pass
        if not method and "SelectionItem" in el.supported_patterns:
            try:
                self._pattern(el, "SelectionItem").Select()
                method = "selection_item"
            except Exception:
                pass
        if not method:
            if mode == "play":
                return ActionResult(
                    "activate_result", False,
                    f"could not activate {el.name!r} via UIA (no keyboard shortcuts in play mode)",
                )
            try:
                UIAWrapper(el.info).click_input()
                time.sleep(0.1)
                method = "click"
            except Exception:
                self._focus_element(el)
                keyboard.send("enter")
                method = "enter"

        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        self._pending_play_target = None
        return ActionResult("activate_result", True, f"activated {el.name!r} via {method}",
                            {"selector_id": el.selector_id, "is_nav_suggestion": is_nav})

    # ---- cache flush -------------------------------------------------------
    def flush_cache(self, success: bool) -> None:
        for role, el, from_cache in self._used:
            if success:
                self.cache.record_success(self.app_key(), role, el)
            elif from_cache:
                self.cache.record_failure(self.app_key(), role)
        self._used.clear()

    def discard_pending(self) -> None:
        """Clear pending selectors without recording success/failure (progress turns)."""
        self._used.clear()

    # ---- helpers -----------------------------------------------------------
    def _is_foreground(self) -> bool:
        if not self.window:
            return False
        try:
            return win32gui.GetForegroundWindow() == self.window.hwnd
        except Exception:
            return False

    def _ensure_foreground(self) -> bool:
        if self._is_foreground():
            return True
        set_foreground(self.window.hwnd)
        time.sleep(self.cfg.timing.after_focus_ms / 1000.0)
        return self._is_foreground()

    def _focus_element(self, el: UIElement) -> bool:
        wrapper = UIAWrapper(el.info)
        # For text input elements, always send a real click in addition to UIA
        # set_focus(). UIA set_focus alone sets CurrentHasKeyboardFocus=True in the
        # accessibility tree, but React/Mantine apps (Feishin, Beeper…) only fire their
        # onFocus/onChange handlers when they receive a real pointer event. Without the
        # click, keyboard.write() never triggers the React onChange and live search
        # dropdowns never appear.
        is_input = el.control_type in ("Edit", "ComboBox", "Document")
        try:
            wrapper.set_focus()
        except Exception:
            pass
        time.sleep(self.cfg.timing.after_focus_ms / 1000.0)
        if is_input:
            try:
                wrapper.click_input()
                time.sleep(self.cfg.timing.after_focus_ms / 1000.0)
            except Exception:
                pass
        try:
            return bool(el.info.element.CurrentHasKeyboardFocus)
        except Exception:
            return self._is_foreground()

    def _pattern(self, el: UIElement, name: str):
        return get_elem_interface(el.info.element, name)

    # ---- dispatch ----------------------------------------------------------
    def dispatch(self, action: Action) -> ActionResult:
        op = action.op.value if hasattr(action.op, "value") else str(action.op)
        if not guardrails.check_op_allowed(op):
            return ActionResult(op, False, f"op {op!r} not in allowlist")
        handler = getattr(self, f"_op_{op}", None)
        if handler is None:
            return ActionResult(op, False, f"no handler for op {op!r}")
        try:
            result = handler(action)
        except Exception as exc:  # fail loud, never pretend success
            result = ActionResult(op, False, f"exception: {exc}")
        self.trace.log("action", op=op, ok=result.ok, detail=result.detail, **result.data)
        return result

    # ---- primitives --------------------------------------------------------
    def _op_ensure_window(self, action: Action) -> ActionResult:
        app = action.args.get("app_name") or action.target.semantic_role
        launch_hint = action.args.get("launch_hint")
        self.window = self.resolver.resolve(app, launch_hint)
        forbidden, reason = guardrails.is_forbidden_window(self.window.exe, self.window.title)
        if forbidden:
            self.window = None
            return ActionResult("ensure_window", False, reason)
        self._ensure_foreground()
        time.sleep(self.cfg.timing.after_launch_ms / 1000.0)
        self.observe()
        return ActionResult("ensure_window", True, f"targeting {self.window.title!r}", {"app": self.window.app_key})

    def _op_focus_window(self, action: Action) -> ActionResult:
        if not self.window:
            return ActionResult("focus_window", False, "no window resolved yet")
        ok = self._ensure_foreground()
        return ActionResult("focus_window", ok, "foreground" if ok else "failed to foreground window")

    def _op_observe_window(self, action: Action) -> ActionResult:
        if not self.window:
            return ActionResult("observe_window", False, "no window to observe")
        obs = self.observe()
        return ActionResult("observe_window", True, f"{len(obs.elements)} controls", {"controls": len(obs.elements)})

    def _op_find_control(self, action: Action) -> ActionResult:
        role = action.target.semantic_role or "named_control"
        el = self._find_by_role(role, action.args)
        if el is None:
            return ActionResult("find_control", False, f"no control matched role {role!r}")
        return ActionResult("find_control", True, f"{el.control_type} {el.name!r}", {"selector_id": el.selector_id})

    def _op_focus_control(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("focus_control", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("focus_control", False, "target window not foreground")
        ok = self._focus_element(el)
        return ActionResult("focus_control", True, f"focused {el.name!r} (kbd_focus={ok})", {"selector_id": el.selector_id})

    def _op_invoke_control(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("invoke_control", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("invoke_control", False, "target window not foreground")
        try:
            self._pattern(el, "Invoke").Invoke()
        except Exception:
            try:
                self._pattern(el, "LegacyIAccessible").DoDefaultAction()
            except Exception as exc:
                return ActionResult("invoke_control", False, f"no invokable pattern: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("invoke_control", True, f"invoked {el.name!r}", {"selector_id": el.selector_id})

    def _op_set_value(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("set_value", False, verdict.reason)
        text = str(action.args.get("text", ""))
        try:
            self._pattern(el, "Value").SetValue(text)
        except Exception as exc:
            return ActionResult("set_value", False, f"ValuePattern unavailable: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("set_value", True, f"set value of {el.name!r}", {"selector_id": el.selector_id})

    def _op_type_text(self, action: Action) -> ActionResult:
        text = str(action.args.get("text", ""))
        clear_first = bool(action.args.get("clear_first", False))
        role = action.target.semantic_role or ""
        if not text:
            return ActionResult("type_text", False, "no text to type")
        el, why = self._resolve_target(action)
        if el is None:
            return ActionResult("type_text", False, why)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("type_text", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("type_text", False, "refusing to type: target window not foreground")
        has_value = "Value" in el.supported_patterns
        # React/JS-framework search inputs need real keyboard events to fire onChange.
        # SetValue only writes the DOM attribute without triggering React's event system,
        # so live-search fields stop responding. Use keyboard-first for search_or_input
        # roles and SetValue-first for plain editable text areas/documents.
        prefer_keyboard = role == "search_or_input" or (el.name or "").strip().lower() == "search"
        focused = self._focus_element(el)
        is_input = el.control_type in ("Edit", "ComboBox", "Document")
        if not focused and not self._is_foreground():
            return ActionResult("type_text", False, "refusing to type: no verified focused control")
        # Mantine/React search boxes often keep kbd_focus=False after click_input.
        if not focused and not (prefer_keyboard and is_input):
            return ActionResult("type_text", False, "refusing to type: no verified focused control")
        time.sleep(0.2)  # let the focused control settle before input

        if clear_first:
            keyboard.send("ctrl+a")
            time.sleep(0.08)
            keyboard.send("delete")
            time.sleep(0.08)

        method = ""
        if has_value and not prefer_keyboard:
            try:
                self._pattern(el, "Value").SetValue(text)
                method = "value_pattern"
            except Exception:
                method = ""
        if not method:
            keyboard.write(text, delay=self.cfg.timing.type_char_delay_s)
            method = "keyboard"
            # Self-heal: if keyboard didn't land and SetValue is available, retry.
            if has_value and not prefer_keyboard:
                time.sleep(0.1)
                if text.lower() not in read_value(el).lower():
                    try:
                        keyboard.send("ctrl+a")
                        time.sleep(0.05)
                        self._pattern(el, "Value").SetValue(text)
                        method = "keyboard+value_heal"
                    except Exception:
                        pass

        if prefer_keyboard:
            time.sleep(0.15)
            landed = text.lower() in read_value(el).lower()
            if not landed:
                sf = self._find_search_field()
                if sf is not None:
                    el = sf
                    self._focus_element(el)
                    keyboard.send("ctrl+a")
                    time.sleep(0.08)
                    keyboard.send("delete")
                    time.sleep(0.08)
                    keyboard.write(text, delay=self.cfg.timing.type_char_delay_s)
                    method = "keyboard_search_heal"

        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("type_text", True, f"typed {text!r} via {method}", {"selector_id": el.selector_id})

    def _op_send_hotkey(self, action: Action) -> ActionResult:
        keys = str(action.args.get("keys", ""))
        verdict = guardrails.check_hotkey(keys)
        if not verdict.allowed:
            return ActionResult("send_hotkey", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("send_hotkey", False, "refusing hotkey: target window not foreground")
        keyboard.send(keys.replace(" ", ""))
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("send_hotkey", True, f"sent {keys!r}")

    def _op_select_item(self, action: Action) -> ActionResult:
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("select_item", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("select_item", False, "target window not foreground")
        try:
            self._pattern(el, "SelectionItem").Select()
        except Exception as exc:
            return ActionResult("select_item", False, f"SelectionItemPattern unavailable: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("select_item", True, f"selected {el.name!r}", {"selector_id": el.selector_id})

    def _op_double_click_element(self, action: Action) -> ActionResult:
        """Open/activate via grounded patterns (no coordinate double-click)."""
        el, why = self._resolve_target(action)
        verdict = guardrails.check_element_target(el)
        if not verdict.allowed:
            return ActionResult("double_click_element", False, verdict.reason)
        if not self._ensure_foreground():
            return ActionResult("double_click_element", False, "target window not foreground")
        try:
            self._pattern(el, "Invoke").Invoke()
        except Exception:
            try:
                self._pattern(el, "SelectionItem").Select()
                self._focus_element(el)
                keyboard.send("enter")
            except Exception as exc:
                return ActionResult("double_click_element", False, f"no activate path: {exc}")
        time.sleep(self.cfg.timing.after_action_ms / 1000.0)
        return ActionResult("double_click_element", True, f"activated {el.name!r}", {"selector_id": el.selector_id})

    def _op_wait_for(self, action: Action) -> ActionResult:
        timeout_ms = int(action.args.get("timeout_ms", self.cfg.loop.wait_default_ms))
        terms = action.args.get("contains_any") or []
        if isinstance(terms, str):
            terms = [terms]
        terms = [t.lower() for t in terms]
        ctype = action.args.get("control_type")
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            obs = self.observe()
            if obs:
                for e in obs.elements:
                    text = f"{e.name or ''} {read_value(e)}".lower()
                    type_ok = (ctype is None) or (e.control_type == ctype)
                    term_ok = (not terms) or any(t in text for t in terms)
                    if type_ok and term_ok:
                        return ActionResult("wait_for", True, f"condition met: {e.name!r}")
            time.sleep(self.cfg.loop.wait_poll_ms / 1000.0)
        return ActionResult("wait_for", False, f"timeout waiting for terms={terms} type={ctype}")

    def _op_verify(self, action: Action) -> ActionResult:
        obs = self.observe()
        pc = {"type": action.args.get("type", "visible_state_changed"), "args": action.args}
        res = verify_postcondition(pc, obs)
        return ActionResult("verify", res.ok, res.detail)

    def _op_cache_selector(self, action: Action) -> ActionResult:
        role = action.target.semantic_role
        el, why = self._resolve_target(action)
        if not role or el is None:
            return ActionResult("cache_selector", False, "need semantic_role and a resolved element")
        self.cache.record_success(self.app_key(), role, el)
        return ActionResult("cache_selector", True, f"cached {role!r}")

    def _op_repair_selector(self, action: Action) -> ActionResult:
        role = action.target.semantic_role or "named_control"
        self.cache.record_failure(self.app_key(), role)
        self.observe()
        el = self._find_by_role(role, action.args)
        if el is None:
            return ActionResult("repair_selector", False, f"could not rediscover {role!r}")
        return ActionResult("repair_selector", True, f"rediscovered {el.name!r}", {"selector_id": el.selector_id})

    def _op_clarify(self, action: Action) -> ActionResult:
        msg = action.args.get("message", "need clarification")
        return ActionResult("clarify", True, msg, {"clarify": True})

    def _op_stop_with_failure(self, action: Action) -> ActionResult:
        reason = action.args.get("reason", "planner requested stop")
        return ActionResult("stop_with_failure", False, reason, {"stop": True})
