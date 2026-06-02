"""Tests for transcript normalization."""
from app.nlp.normalizer import normalize


def test_strips_fillers():
    assert normalize("um open notepad and type hello") == "open notepad and type hello"


def test_strips_polite_prefix():
    assert normalize("please open notepad") == "open notepad"


def test_collapses_whitespace():
    assert normalize("  open   notepad  ") == "open notepad"


def test_lowercases():
    assert normalize("Open Notepad") == "open notepad"


def test_empty_string():
    result = normalize("")
    assert result == ""


def test_no_fillers_unchanged():
    assert normalize("open calculator") == "open calculator"


def test_removes_can_you():
    assert normalize("can you open my browser") == "open my browser"


def test_removes_hey():
    assert normalize("hey open notepad") == "open notepad"
