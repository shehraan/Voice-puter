"""Run multiple independent planner agents and require majority agreement."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from app.planner.base import Planner, PlannerContext
from app.planner.schema import ActionOp, GoalType, Plan


def _action_tuple(a) -> tuple:
    sel = a.target.selector_id if a.target else None
    role = a.target.semantic_role if a.target else None
    if a.op == ActionOp.type_text and role == "search_or_input":
        sel = None
    return (
        a.op,
        sel,
        role,
        tuple(sorted((a.args or {}).items())),
    )


def _plan_fingerprint(plan: Plan) -> tuple:
    actions = [_action_tuple(a) for a in plan.actions]
    if plan.goal == GoalType.generic_search:
        while actions and actions[-1][0] == ActionOp.send_hotkey and actions[-1][3] == (("keys", "enter"),):
            actions.pop()
    return (plan.goal, tuple(actions))


class ConsensusPlanner:
    """Wrap a planner with N independent votes; execute only on majority agreement."""

    def __init__(
        self,
        inner: Planner,
        *,
        agents: int = 3,
        min_votes: int = 2,
        min_confidence: float = 0.5,
    ):
        self.inner = inner
        self.agents = max(1, agents)
        self.min_votes = max(1, min(min_votes, self.agents))
        self.min_confidence = min_confidence

    def plan(self, context: PlannerContext) -> Plan:
        votes: list[Plan] = []
        for agent_index in range(self.agents):
            agent_ctx = replace(context, consensus_agent_index=agent_index, consensus_agent_count=self.agents)
            votes.append(self.inner.plan(agent_ctx))

        fps = Counter(_plan_fingerprint(p) for p in votes)
        fingerprint, count = fps.most_common(1)[0]
        if count < self.min_votes:
            return Plan(
                goal=GoalType.clarify,
                target_app=votes[0].target_app,
                confidence=0.0,
                rationale_short=(
                    f"no planner consensus ({count}/{self.agents} agreed; need {self.min_votes})"
                ),
            )

        agreeing = [p for p in votes if _plan_fingerprint(p) == fingerprint]
        chosen = agreeing[0]
        avg_conf = sum(p.confidence for p in agreeing) / len(agreeing)
        if avg_conf < self.min_confidence:
            return Plan(
                goal=GoalType.clarify,
                target_app=chosen.target_app,
                confidence=avg_conf,
                rationale_short=(
                    f"consensus confidence too low ({avg_conf:.2f} < {self.min_confidence}; "
                    f"{count}/{self.agents} agreed)"
                ),
            )

        return chosen.model_copy(
            update={
                "confidence": avg_conf,
                "rationale_short": f"[consensus {count}/{self.agents}] {chosen.rationale_short}",
            }
        )
