"""Prompt construction for the local LLM planner.

Keeps the prompt small (performance.md prompt budget): goal hint, target-app guess,
the compact observation subset, cache candidates, previous result. The output schema,
vocabularies, and a worked example live in the system prompt so the small model does
not echo them back as data.
"""
from __future__ import annotations

import json

from app.planner.base import PlannerContext
from app.planner.schema import ACTION_OPS, GOAL_TYPES

_SEMANTIC_ROLES = [
    "search_or_input",
    "editable_document",
    "result_item",
    "submit_or_primary",
    "named_control",
    "menu_or_command",
    "build_or_run",
    "field_by_label",
]

SYSTEM_PROMPT = f"""You are the planner for a Windows desktop agent that VISIBLY operates apps via UI Automation.

You receive a JSON object describing the command and the current UI observation. You must
reply with EXACTLY ONE JSON object that is the next plan. Output JSON only, no prose, no
markdown, no schema echo.

The plan object has these fields:
  goal: one of {GOAL_TYPES}
  target_app: string or null
  visual_demo_mode: boolean
  confidence: number 0..1
  needs_confirmation: boolean
  rationale_short: string
  actions: array of {{ "op": <op>, "target": {{ "selector_id": <id|null>, "semantic_role": <role|null> }}, "args": {{}} }}
  postconditions: array of {{ "type": string, "description": string, "args": {{}} }}

Allowed ops: {ACTION_OPS}
Allowed semantic_role values: {_SEMANTIC_ROLES}

Rules:
- visual_demo_mode is true: drive the visible UI. Never use hidden APIs, shell, or pixel coordinates.
- Ground every action in observed controls (use their selector_id) or a semantic_role the executor can search for.
- Never invent a selector_id that is not in the observation.
- Emit 1 to 4 actions per turn. After UI changes the loop observes again and calls you again.
- If the target app is not observed yet, first action is ensure_window with args.app_name set to the target app
  (use target_app_guess), then observe_window, then find_control.
- For typing, put the real text in type_text args.text (use text_or_query). Only type into an editable control found.
- For a SEARCH, the same plan must end with send_hotkey {{"keys":"enter"}} right after the type_text to submit the
  query; do not stop after merely typing. If you already typed the query last turn, submit it now with send_hotkey enter.
- ALWAYS include at least one postcondition describing the visible end state you expect.
- Do NOT repeat an action that previous_action_result shows already succeeded; build on the observation.
- If the observation already shows the goal is satisfied, return goal "no_op" with empty actions.
- needs_confirmation=true for deleting/sending/installing/purchasing/git-write/elevated actions.
- Use goal "clarify" if the target app, text, or action is too ambiguous.

Example (open a text editor and type):
{{"goal":"generic_text_entry","target_app":"notepad","visual_demo_mode":true,"confidence":0.9,"needs_confirmation":false,"rationale_short":"open notepad and type into the document","actions":[{{"op":"ensure_window","target":{{"selector_id":null,"semantic_role":null}},"args":{{"app_name":"notepad"}}}},{{"op":"find_control","target":{{"selector_id":null,"semantic_role":"editable_document"}},"args":{{}}}},{{"op":"type_text","target":{{"selector_id":null,"semantic_role":"editable_document"}},"args":{{"text":"hello world","clear_first":false}}}}],"postconditions":[{{"type":"visible_text_contains","description":"document shows the text","args":{{"contains_any":["hello world"]}}}}]}}

Example (search inside an app - ensure the app, find the search field, type the query, THEN submit):
{{"goal":"generic_search","target_app":"browser","visual_demo_mode":true,"confidence":0.85,"needs_confirmation":false,"rationale_short":"open the browser, type the query into the search field and submit","actions":[{{"op":"ensure_window","target":{{"selector_id":null,"semantic_role":null}},"args":{{"app_name":"browser"}}}},{{"op":"find_control","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{}}}},{{"op":"type_text","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{"text":"mechanical keyboards","clear_first":true}}}},{{"op":"send_hotkey","target":{{"selector_id":null,"semantic_role":null}},"args":{{"keys":"enter"}}}}],"postconditions":[{{"type":"results_appeared","description":"search results are shown","args":{{"contains_any":["mechanical","keyboards"]}}}}]}}

Note on follow_up_action: if the situation includes a follow_up_action (play/open/select/send_message),
your job is still to perform the SEARCH (find field, type query, submit). The system selects and
activates the best matching result and/or sends the message afterwards. Focus on getting results on screen.
For send_message: search for the contact name, submit, and stop. The system navigates to the chat and sends.

Example (search a media app and play - just do the search; the system plays the best result):
{{"goal":"generic_search","target_app":"spotify","visual_demo_mode":true,"confidence":0.85,"needs_confirmation":false,"rationale_short":"open spotify, type the query into search and submit","actions":[{{"op":"ensure_window","target":{{"selector_id":null,"semantic_role":null}},"args":{{"app_name":"spotify"}}}},{{"op":"find_control","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{}}}},{{"op":"type_text","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{"text":"narcos","clear_first":true}}}},{{"op":"send_hotkey","target":{{"selector_id":null,"semantic_role":null}},"args":{{"keys":"enter"}}}}],"postconditions":[{{"type":"results_appeared","description":"search results are shown","args":{{"contains_any":["narcos"]}}}}]}}

Example (messaging app - search for a contact; system opens chat and sends message):
{{"goal":"generic_search","target_app":"beeper","visual_demo_mode":true,"confidence":0.85,"needs_confirmation":true,"rationale_short":"invoke search chats, type contact name, submit","actions":[{{"op":"ensure_window","target":{{"selector_id":null,"semantic_role":null}},"args":{{"app_name":"beeper"}}}},{{"op":"invoke_control","target":{{"selector_id":null,"semantic_role":"named_control"}},"args":{{"name":"Search Chats"}}}},{{"op":"find_control","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{}}}},{{"op":"type_text","target":{{"selector_id":null,"semantic_role":"search_or_input"}},"args":{{"text":"gaston","clear_first":true}}}},{{"op":"send_hotkey","target":{{"selector_id":null,"semantic_role":null}},"args":{{"keys":"enter"}}}}],"postconditions":[{{"type":"results_appeared","description":"contact list shows the search result","args":{{"contains_any":["gaston"]}}}}]}}
"""


def build_messages(ctx: PlannerContext) -> list[dict[str, str]]:
    hint = ctx.goal_hint
    payload = {
        "transcript": ctx.transcript,
        "normalized": ctx.normalized,
        "goal_guess": hint.goal.value,
        "target_app_guess": hint.target_app,
        "text_or_query": hint.payload,
        "named_control_guess": hint.query,
        "follow_up_action": hint.then,
        "message_to_send": getattr(hint, "message", None),
        "visual_demo_mode": ctx.visual_demo_mode,
        "current_window": ctx.window,
        "observed_controls": (ctx.observation or {}).get("actionable_controls", []),
        "selector_cache_candidates": ctx.cache_candidates,
        "previous_action_result": ctx.previous_result,
        "memory": ctx.memory,
    }
    user = (
        "Here is the current situation. Reply with ONLY the next plan JSON object.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    if ctx.consensus_agent_index is not None and ctx.consensus_agent_count:
        user += (
            f"\n\nYou are independent planner agent {ctx.consensus_agent_index + 1} of "
            f"{ctx.consensus_agent_count}. Evaluate the situation on your own; other agents "
            "are doing the same in parallel. Only high-confidence plans should be emitted."
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
