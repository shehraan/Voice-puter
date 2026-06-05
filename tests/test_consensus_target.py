"""Tests for three-agent play target voting."""
from __future__ import annotations

from app.ui.consensus_target import consensus_play_target
from app.ui.executor import Executor
from tests.conftest import make_element, make_observation


def test_consensus_picks_named_track_not_tracks_tab():
    tracks = make_element("tab", "Tracks", "Hyperlink", supported_patterns=["Invoke"])
    tracks.rectangle = (400, 120, 500, 150)
    song = make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"])
    song.rectangle = (400, 320, 600, 350)
    obs = make_observation(title="Search - Feishin", elements=[tracks, song])
    el, votes = consensus_play_target(obs.elements, "narcos")
    assert el is not None
    assert el.selector_id == "song"
    assert len(votes) >= 1


def test_consensus_rejects_tracks_tab_alone():
    tracks = make_element("tab", "Tracks", "Hyperlink", supported_patterns=["Invoke"])
    tracks.rectangle = (400, 120, 500, 150)
    obs = make_observation(title="Search - Feishin", elements=[tracks])
    el, _ = consensus_play_target(obs.elements, "narcos")
    assert el is None


def test_executor_identifies_search_page_tab():
    tab = make_element("tab", "Tracks", "Hyperlink", supported_patterns=["Invoke"])
    tab.rectangle = (400, 120, 500, 150)
    assert Executor._is_search_page_tab(tab)


def test_consensus_rejects_search_for_nav_row():
    nav = make_element("nav", "Search for narcos", "Hyperlink", supported_patterns=["Invoke"])
    nav.rectangle = (400, 320, 600, 350)
    song = make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"])
    song.rectangle = (400, 380, 600, 410)
    artist = make_element("artist", "Migos", "Hyperlink", supported_patterns=["Invoke"])
    artist.rectangle = (500, 382, 600, 408)
    obs = make_observation(title="Feishin", elements=[nav, song, artist])
    el, votes = consensus_play_target(obs.elements, "narcos", min_votes=1)
    assert el is not None
    assert el.selector_id == "song"
    assert all(v.element.selector_id != "nav" for v in votes)


def test_consensus_agrees_on_row_band_when_picks_differ():
    song = make_element("song", "Narcos", "Hyperlink", supported_patterns=["Invoke"])
    song.rectangle = (400, 320, 600, 350)
    play = make_element("play", "", "Button", supported_patterns=["Invoke"])
    play.rectangle = (360, 320, 390, 350)
    obs = make_observation(title="Search - Feishin", elements=[song, play])
    el, votes = consensus_play_target(obs.elements, "narcos")
    assert el is not None
    assert el.selector_id == "song"
    assert len(votes) >= 1
