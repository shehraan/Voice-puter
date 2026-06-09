"""Tests for the two-stage search->play flow (e.g. Feishin).

These reproduce the failure where the agent clicks a navigation suggestion
("Search for slut") but never clicks the actual song row afterwards.

Two layers are covered:
  1. Executor.find_best_result scoring — a real content row must beat a navigation
     suggestion in play mode, and nav suggestions are correctly flagged.
  2. The full loop continuation — stage 1 clicks the suggestion (navigate), stage 2
     clicks the real track (play), inline within one iteration so the planner cannot
     re-search between stages.
"""
from __future__ import annotations

from unittest.mock import patch

from app.cache.selector_cache import SelectorCache
from app.core.config import Config, LoopConfig, VisualTiming
from app.core.loop import run_command
from app.core.trace import Trace
from app.planner.stub_planner import StubPlanner
from app.ui.executor import ActionResult, Executor
from app.verifier.verify import (
    playable_result_present,
    results_ready_for_followup,
    search_results_present,
)
from tests.conftest import make_element, make_observation


# ---------------------------------------------------------------------------
# Unit: nav-suggestion detection + result scoring
# ---------------------------------------------------------------------------

def _picker(obs):
    """A bare Executor with just enough state for find_best_result."""
    ex = Executor.__new__(Executor)
    ex.observation = obs
    ex._last_target = None
    ex._used = []
    return ex


def test_is_nav_suggestion_detection():
    assert Executor._is_nav_suggestion("Search for slut")
    assert Executor._is_nav_suggestion("go to artist")
    assert Executor._is_nav_suggestion("Show all results")
    assert not Executor._is_nav_suggestion("Slut")
    assert not Executor._is_nav_suggestion("Play Narcos by Migos")


def test_real_row_beats_nav_suggestion_in_play_mode():
    """When both the suggestion and the real song row are visible, play mode picks the song."""
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
            make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke", "SelectionItem"]),
        ],
    )
    el = _picker(obs).find_best_result("slut", mode="play")
    assert el is not None
    assert el.name == "Slut"
    assert not Executor._is_nav_suggestion(el.name.lower())


def test_nav_suggestion_chosen_only_when_alone():
    """In the dropdown (only the suggestion exists), it's selected so we can navigate."""
    obs = make_observation(
        title="Feishin",
        elements=[
            make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    el = _picker(obs).find_best_result("slut", mode="play")
    assert el is not None
    assert Executor._is_nav_suggestion(el.name.lower())


def test_playable_result_present_excludes_nav_suggestion():
    """Dropdown-only state: search_results_present true, playable_result_present false."""
    obs = make_observation(
        title="Feishin",
        elements=[
            make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    assert search_results_present(obs, "slut")
    assert not playable_result_present(obs, "slut")


def test_playable_result_present_true_on_results_page():
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
            make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    assert playable_result_present(obs, "slut")


def test_find_best_result_ignores_search_edit_value():
    """Query text in a search Edit Value must not beat a real result row."""
    edit = make_element("edit", "", "Edit", supported_patterns=["Value"])
    edit.info.element.CurrentValue = "narcos"
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            edit,
            make_element("nav", "Search for narcos", "Hyperlink", supported_patterns=["Invoke"]),
            make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    el = _picker(obs).find_best_result("narcos", mode="play", exclude_nav=True)
    assert el is not None
    assert el.name == "Narcos"


def test_table_row_play_button_picks_album_art_in_row():
    """Unnamed icon Button in the matching row when titles are only in row text."""
    doc = make_element("doc", "Feishin", "Document", supported_patterns=["Value"])
    doc.rectangle = (-600, 0, -400, 30)
    doc.info.element.CurrentValue = "file:///app/index.html#/search/song?query=narcos"
    header = make_element("hdr", "", "Edit", supported_patterns=["Value"])
    header.info.element.CurrentValue = "narcos"
    header.rectangle = (-800, 44, -600, 81)
    t1 = make_element("t1", "Tracks", "Hyperlink")
    t1.rectangle = (-600, 120, -500, 150)
    t2 = make_element("t2", "Albums", "Hyperlink")
    t2.rectangle = (-600, 150, -500, 180)
    t3 = make_element("t3", "Artists", "Hyperlink")
    t3.rectangle = (-600, 180, -500, 210)
    row_idx = make_element("idx", "1", "Button", supported_patterns=["Invoke"])
    row_idx.rectangle = (-700, 200, -668, 232)
    row_text = make_element("txt", "", "Text", supported_patterns=["Text", "Value"])
    row_text.rectangle = (-640, 205, -560, 225)
    row_text.info.element.CurrentValue = "narcos"
    album = make_element("art", "", "Button", supported_patterns=["Invoke"])
    album.rectangle = (-660, 200, -628, 232)
    obs = make_observation(
        title="Search - Feishin",
        elements=[doc, header, t1, t2, t3, row_idx, row_text, album],
    )
    el = _picker(obs)._find_table_row_play_button("narcos")
    assert el is not None
    assert el.selector_id == "art"


def test_play_picks_track_title_not_artist_column():
    """Artist/album links must not win when only their Value contains the track title."""
    song = make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"])
    artist = make_element("artist", "Migos", "Hyperlink", supported_patterns=["Invoke"])
    artist.info.element.CurrentValue = "Narcos Migos Culture II"
    wrong = make_element("wrong", "Taylor Swift", "Hyperlink", supported_patterns=["Invoke"])
    wrong.info.element.CurrentValue = "Narcos feat Taylor Swift"
    obs = make_observation(
        title="Search - Feishin",
        elements=[song, artist, wrong],
    )
    el = _picker(obs).find_best_result("narcos", mode="play", exclude_nav=True)
    assert el is not None
    assert el.name == "Narcos"


def test_exclude_nav_skips_suggestion():
    obs = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
            make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    el = _picker(obs).find_best_result("slut", mode="play", exclude_nav=True)
    assert el is not None
    assert el.name == "Slut"


def test_media_play_stages_nav_found_runs_play_same_turn():
    """Regression: nav suggestion found must set nav_done and run stage B in one call."""
    from unittest.mock import MagicMock

    from app.core.loop import _media_play_stages
    from app.nlp.goal_shortlist import GoalHint
    from app.planner.schema import GoalType

    dropdown = make_observation(
        title="Feishin",
        elements=[
            make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
            make_element("nav", "Search for narcos", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    results = make_observation(
        title="Search - Feishin",
        elements=[
            make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
            make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"]),
        ],
    )
    hint = GoalHint(goal=GoalType.generic_search, target_app="feishin", payload="narcos", then="play")
    trace = Trace(transcript="search narcos and play")
    ex = MagicMock()
    ex.observation = dropdown
    stage = {"nav": False}
    activate_calls = []

    def observe():
        return results if stage["nav"] else dropdown

    ex.observe.side_effect = observe
    ex._find_nav_suggestion.side_effect = lambda q: _picker(ex.observation)._find_nav_suggestion(q)
    ex.find_consensus_play_target.side_effect = lambda q: _picker(ex.observation).find_consensus_play_target(q)
    ex._find_query_result_row.side_effect = lambda q: _picker(ex.observation)._find_query_result_row(q)
    ex._find_search_datagrid_row.side_effect = lambda q: _picker(ex.observation)._find_search_datagrid_row(q)
    ex._refresh_chromium_observation.side_effect = observe
    ex._element_search_text = lambda el: Executor._element_search_text(el)

    def fake_activate(query, mode="open", exclude_nav=False, **kwargs):
        el = _picker(ex.observation).find_best_result(query, mode=mode, exclude_nav=exclude_nav)
        is_nav = Executor._is_nav_suggestion((el.name or "").lower()) if el else False
        activate_calls.append((mode, exclude_nav, el.name if el else None))
        if is_nav:
            stage["nav"] = True
            ex.observation = results
        return ActionResult(
            "activate_result", True, f"activated {el.name!r}",
            {"is_nav_suggestion": is_nav},
        )

    ex.activate_best_result.side_effect = fake_activate

    with patch("app.core.loop._NAV_SUGGESTION_POLL_S", 0), \
         patch("app.core.loop._NAV_SUGGESTION_POLL_TRIES", 2), \
         patch("app.core.loop._PLAYABLE_POLL_S", 0), \
         patch("app.core.loop._PLAYABLE_POLL_TRIES", 5):
        activated, nav_done, _, _, results_ready = _media_play_stages(
            ex, hint, trace, dropdown, nav_done=False,
        )

    assert nav_done
    assert results_ready
    assert activated
    assert ("open", False, "Search for narcos") in activate_calls
    assert ("play", True, "Narcos") in activate_calls


# ---------------------------------------------------------------------------
# Integration: full two-stage loop ends in verified playback
# ---------------------------------------------------------------------------

def _wire_executor_helpers(mock_exec):
    """Attach real result-picker helpers; MagicMock defaults break nav polling."""
    def _nav(query):
        return _picker(mock_exec.observation)._find_nav_suggestion(query)

    def _row(query):
        return _picker(mock_exec.observation)._find_query_result_row(query)

    def _grid(query):
        return _picker(mock_exec.observation)._find_search_datagrid_row(query)

    def _search_field():
        return _picker(mock_exec.observation)._find_search_field()

    def _consensus(query):
        return _picker(mock_exec.observation).find_consensus_play_target(query)

    def _refresh():
        if callable(getattr(mock_exec, "observe", None)):
            return mock_exec.observe()
        return mock_exec.observation

    mock_exec._find_nav_suggestion.side_effect = _nav
    mock_exec._find_query_result_row.side_effect = _row
    mock_exec._find_search_datagrid_row.side_effect = _grid
    mock_exec._find_search_field.side_effect = _search_field
    mock_exec.find_consensus_play_target.side_effect = _consensus
    mock_exec._refresh_chromium_observation.side_effect = _refresh
    mock_exec._element_search_text = lambda el: Executor._element_search_text(el)


def _fast_config():
    cfg = Config()
    cfg.timing = VisualTiming(after_launch_ms=0, after_focus_ms=0, after_action_ms=0, type_char_delay_s=0)
    cfg.loop = LoopConfig(max_iterations=6, repair_budget=2, wait_default_ms=10, wait_poll_ms=5)
    cfg.auto_confirm = True
    return cfg


def test_two_stage_search_then_play_succeeds(tmp_path):
    """Feishin-style: dropdown 'Search for slut' -> results page 'Slut' -> playback.

    The executor is mocked, but activate_best_result runs the *real* find_best_result
    scoring against the current mocked observation, so this faithfully exercises the
    loop's inline two-stage activation.
    """
    cmd = "open feishin and search for the track 'slut' and play it"
    stage = {"nav": False, "played": False}

    def _dropdown():
        return make_observation(
            title="Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
            ],
        )

    def _results():
        return make_observation(
            title="Search - Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke", "SelectionItem"]),
                make_element("artist", "Some Artist", "Hyperlink"),
            ],
        )

    def _playing():
        return make_observation(
            title="(Playing) Slut - Some Artist - Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("pause", "Pause", "Button", supported_patterns=["Invoke"]),
                make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke", "SelectionItem"]),
            ],
        )

    def _obs():
        if stage["played"]:
            return _playing()
        if stage["nav"]:
            return _results()
        return _dropdown()

    cfg = _fast_config()
    trace = Trace(transcript=cmd)
    SelectorCache(path=tmp_path / "cache.json")

    activate_calls = []

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec, \
             patch("app.core.loop._PLAYABLE_POLL_S", 0), patch("app.core.loop._PLAYABLE_POLL_TRIES", 10), \
             patch("app.core.loop._PLAY_ROW_POLL_S", 0), patch("app.core.loop._PLAY_ROW_POLL_TRIES", 5), \
             patch("app.core.loop._LIVE_SEARCH_POLL_S", 0), patch("app.core.loop._LIVE_SEARCH_POLL_TRIES", 3), \
             patch("app.core.loop._NAV_SUGGESTION_POLL_S", 0), patch("app.core.loop._NAV_SUGGESTION_POLL_TRIES", 3):
        from tests.test_generic_flows import _patched_executor
        mock_exec = _patched_executor(_obs, tmp_path)
        _wire_executor_helpers(mock_exec)

        def fake_dispatch(action):
            return ActionResult(op=str(action.op.value), ok=True, detail="mock ok")

        def fake_activate(query, mode="open", exclude_nav=False, **kwargs):
            # Run the REAL scoring against the current observation.
            el = _picker(mock_exec.observation).find_best_result(
                query, mode=mode, exclude_nav=exclude_nav,
            )
            if el is None:
                return ActionResult("activate_result", False, f"no result matched {query!r}")
            is_nav = Executor._is_nav_suggestion((el.name or "").lower())
            activate_calls.append((el.name, is_nav))
            if is_nav:
                stage["nav"] = True   # next observe() returns the results page
            else:
                stage["played"] = True  # next observe() returns the playing state
            return ActionResult("activate_result", True, f"activated {el.name!r}",
                                {"selector_id": el.selector_id, "is_nav_suggestion": is_nav})

        mock_exec.dispatch.side_effect = fake_dispatch
        mock_exec.activate_best_result.side_effect = fake_activate
        MockExec.return_value = mock_exec

        result = run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    # Two activations happened: navigate (suggestion) then play (real row).
    assert ("Search for slut", True) in activate_calls
    assert ("Slut", False) in activate_calls
    assert result is True
    assert trace.result == "success"


def test_two_stage_play_waits_for_delayed_results(tmp_path):
    """Results page appears only after several polls (simulates Navidrome latency)."""
    cmd = "open feishin and search for the track 'slut' and play it"
    stage = {"nav": False, "played": False, "observe_count": 0}

    def _dropdown():
        return make_observation(
            title="Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("nav", "Search for slut", "Hyperlink", supported_patterns=["Invoke"]),
            ],
        )

    def _loading():
        # Results page open but tracks not loaded yet.
        return make_observation(
            title="Search - Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("tab", "Tracks", "Hyperlink"),
            ],
        )

    def _results():
        return make_observation(
            title="Search - Feishin",
            elements=[
                make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"]),
                make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke"]),
            ],
        )

    def _playing():
        return make_observation(
            title="(Playing) Slut - Feishin",
            elements=[
                make_element("pause", "Pause", "Button", supported_patterns=["Invoke"]),
                make_element("song", "Slut", "Hyperlink", supported_patterns=["Invoke"]),
            ],
        )

    def _obs():
        stage["observe_count"] += 1
        if stage["played"]:
            return _playing()
        if not stage["nav"]:
            return _dropdown()
        # After nav: first 2 observes still loading, then results appear.
        if stage["observe_count"] < 4:
            return _loading()
        return _results()

    cfg = _fast_config()
    trace = Trace(transcript=cmd)
    activate_calls = []

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec, \
             patch("app.core.loop._PLAYABLE_POLL_S", 0), patch("app.core.loop._PLAYABLE_POLL_TRIES", 10), \
             patch("app.core.loop._PLAY_ROW_POLL_S", 0), patch("app.core.loop._PLAY_ROW_POLL_TRIES", 5), \
             patch("app.core.loop._LIVE_SEARCH_POLL_S", 0), patch("app.core.loop._LIVE_SEARCH_POLL_TRIES", 3), \
             patch("app.core.loop._NAV_SUGGESTION_POLL_S", 0), patch("app.core.loop._NAV_SUGGESTION_POLL_TRIES", 3):
        from tests.test_generic_flows import _patched_executor
        mock_exec = _patched_executor(_obs, tmp_path)
        _wire_executor_helpers(mock_exec)

        def fake_dispatch(action):
            return ActionResult(op=str(action.op.value), ok=True, detail="mock ok")

        def fake_activate(query, mode="open", exclude_nav=False, **kwargs):
            el = _picker(mock_exec.observation).find_best_result(
                query, mode=mode, exclude_nav=exclude_nav,
            )
            if el is None:
                return ActionResult("activate_result", False, f"no result matched {query!r}")
            is_nav = Executor._is_nav_suggestion((el.name or "").lower())
            activate_calls.append((el.name, is_nav))
            if is_nav:
                stage["nav"] = True
            else:
                stage["played"] = True
            return ActionResult("activate_result", True, f"activated {el.name!r}",
                                {"selector_id": el.selector_id, "is_nav_suggestion": is_nav})

        mock_exec.dispatch.side_effect = fake_dispatch
        mock_exec.activate_best_result.side_effect = fake_activate
        MockExec.return_value = mock_exec

        result = run_command(cmd, cfg, trace, planner=StubPlanner(), confirm=lambda r: True)

    assert ("Search for slut", True) in activate_calls
    assert ("Slut", False) in activate_calls
    assert result is True
    assert trace.result == "success"


def test_does_not_research_when_results_page_already_open(tmp_path):
    """Once the Feishin results page is visible, planner search actions are skipped."""
    cmd = "open feishin and search for the track 'Narcos' and play it"
    phase = {"played": False}

    def _results_page():
        doc = make_element("doc", "Feishin", "Document", supported_patterns=["Value"])
        doc.info.element.CurrentValue = "file:///app/index.html#/search/song?query=narcos"
        search = make_element("obs_0", "Search", "Edit", supported_patterns=["Value", "Text"])
        search.info.element.CurrentValue = "narcos"
        search.rectangle = (-800, 44, -600, 81)
        t1 = make_element("t1", "Tracks", "Hyperlink")
        t1.rectangle = (-600, 120, -500, 150)
        t2 = make_element("t2", "Albums", "Hyperlink")
        t2.rectangle = (-600, 150, -500, 180)
        t3 = make_element("t3", "Artists", "Hyperlink")
        t3.rectangle = (-600, 180, -500, 210)
        song = make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"])
        song.rectangle = (-700, 220, -500, 250)
        return make_observation(
            title="Search - Feishin",
            elements=[doc, search, t1, t2, t3, song],
        )

    def _playing():
        return make_observation(
            title="(Playing) Narcos - Migos - Feishin",
            elements=[
                make_element("pause", "Pause", "Button", supported_patterns=["Invoke"]),
                make_element("song", "Narcos", "Hyperlink"),
            ],
        )

    def _obs():
        if phase.get("played"):
            return _playing()
        return _results_page()

    class AlwaysSearchPlanner:
        """Simulates OpenAI re-issuing search every iteration."""
        def plan(self, ctx):
            from app.planner.schema import Action, ActionOp, GoalType, Plan, Target
            return Plan(
                goal=GoalType.generic_search,
                confidence=0.95,
                rationale_short="type query and submit",
                actions=[
                    Action(op=ActionOp.type_text, target=Target(semantic_role="search_or_input"),
                           args={"text": "narcos", "clear_first": True}),
                    Action(op=ActionOp.send_hotkey, args={"keys": "enter"}),
                ],
            )

    cfg = _fast_config()
    trace = Trace(transcript=cmd)
    dispatched_ops = []
    activate_calls = []

    with patch("app.core.loop.AppResolver"), patch("app.core.loop.Executor") as MockExec, \
             patch("app.core.loop._PLAYABLE_POLL_S", 0), patch("app.core.loop._PLAYABLE_POLL_TRIES", 10), \
             patch("app.core.loop._PLAY_ROW_POLL_S", 0), patch("app.core.loop._PLAY_ROW_POLL_TRIES", 5), \
             patch("app.core.loop._LIVE_SEARCH_POLL_S", 0), patch("app.core.loop._LIVE_SEARCH_POLL_TRIES", 3), \
             patch("app.core.loop._NAV_SUGGESTION_POLL_S", 0), patch("app.core.loop._NAV_SUGGESTION_POLL_TRIES", 3):
        from tests.test_generic_flows import _patched_executor
        mock_exec = _patched_executor(_obs, tmp_path)
        _wire_executor_helpers(mock_exec)

        def fake_dispatch(action):
            dispatched_ops.append(action.op.value)
            return ActionResult(op=str(action.op.value), ok=True, detail="mock ok")

        def fake_activate(query, mode="open", exclude_nav=False, **kwargs):
            el = _picker(mock_exec.observation).find_best_result(
                query, mode=mode, exclude_nav=exclude_nav,
            )
            if el is None:
                return ActionResult("activate_result", False, f"no result matched {query!r}")
            is_nav = Executor._is_nav_suggestion((el.name or "").lower())
            activate_calls.append((el.name, is_nav))
            if not is_nav:
                phase["played"] = True
            return ActionResult("activate_result", True, f"activated {el.name!r}",
                                {"selector_id": el.selector_id, "is_nav_suggestion": is_nav})

        mock_exec.dispatch.side_effect = fake_dispatch
        mock_exec.activate_best_result.side_effect = fake_activate
        MockExec.return_value = mock_exec

        result = run_command(cmd, cfg, trace, planner=AlwaysSearchPlanner(), confirm=lambda r: True)

    # Play is attempted before the planner — no search actions should dispatch.
    assert "type_text" not in dispatched_ops
    assert ("Narcos", False) in activate_calls
    assert result is True
    assert trace.result == "success"
