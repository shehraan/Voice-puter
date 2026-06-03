"""Tests for the safety guardrails layer."""
import pytest

from app.safety.guardrails import (
    check_element_target,
    check_hotkey,
    check_op_allowed,
    command_needs_confirmation,
    is_forbidden_window,
)
from tests.conftest import make_element


# --- op allowlist ---

def test_all_allowed_ops_pass():
    from app.planner.schema import ACTION_OPS
    for op in ACTION_OPS:
        assert check_op_allowed(op), f"op {op!r} should be allowed"


def test_unknown_op_blocked():
    assert not check_op_allowed("run_shell")
    assert not check_op_allowed("exec_python")
    assert not check_op_allowed("")


# --- element checks ---

def test_enabled_visible_element_allowed():
    el = make_element(is_enabled=True, is_offscreen=False)
    result = check_element_target(el)
    assert result.allowed


def test_disabled_element_blocked():
    el = make_element(is_enabled=False)
    result = check_element_target(el)
    assert not result.allowed


def test_offscreen_element_blocked():
    el = make_element(is_offscreen=True)
    result = check_element_target(el)
    assert not result.allowed


def test_none_element_blocked():
    result = check_element_target(None)
    assert not result.allowed


def test_password_element_blocked():
    el = make_element()
    el.info.element.CurrentIsPassword = True
    result = check_element_target(el)
    assert not result.allowed


# --- forbidden windows ---

def test_consent_exe_blocked():
    blocked, reason = is_forbidden_window("consent.exe", "User Account Control")
    assert blocked
    assert "consent" in reason


def test_logonui_blocked():
    blocked, _ = is_forbidden_window("logonui.exe", "Windows Logon")
    assert blocked


def test_normal_app_allowed():
    blocked, _ = is_forbidden_window("notepad.exe", "Notepad")
    assert not blocked


# --- command confirmation ---

def test_delete_command_needs_confirmation():
    needs, reason = command_needs_confirmation("delete all my files")
    assert needs
    assert reason


def test_send_email_needs_confirmation():
    needs, reason = command_needs_confirmation("send email to boss")
    assert needs


def test_git_push_needs_confirmation():
    needs, reason = command_needs_confirmation("git push to main")
    assert needs


def test_safe_search_no_confirmation():
    needs, _ = command_needs_confirmation("search for nearby restaurants in my browser")
    assert not needs


def test_open_app_no_confirmation():
    needs, _ = command_needs_confirmation("open notepad")
    assert not needs


# --- hotkeys ---

def test_safe_hotkey_allowed():
    result = check_hotkey("ctrl+s")
    assert result.allowed


def test_alt_f4_blocked():
    result = check_hotkey("alt+f4")
    assert not result.allowed


def test_ctrl_w_blocked():
    result = check_hotkey("ctrl+w")
    assert not result.allowed
