from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Literal

from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC, Memory, Goal
from .base import Tool


@dataclass
class ReasonTool(Tool):
    """
    Safe meta-tool for requesting state mutations that are social/cognitive:
    - add_memory
    - update_memory_status
    - add_goal (including permission_granted)
    - update_goal_status
    - update_relationship
    Explicitly forbids changes to hp, inventory, equipment, or locations.
    """
    name: str = "reason"
    time_cost: int = 1

    def get_llm_prompt_fragment(self) -> str:
        return (
            "reason(thought: string, desired_outcome: object)\n"
            "Allowed desired_outcome variants:\n"
            "- add_memory: {text, priority?, status?, source_id?, confidence?, is_secret?, payload?}\n"
            "- update_memory_status: {match_text: string, new_status: 'active'|'recalled'|'archived'|'consolidated'}\n"
            "- add_goal: {text, type, priority?, status?, payload?, expiry_tick?}\n"
            "- update_goal_status: {match_text: string, new_status: 'active'|'pending'|'done'|'cancelled'}\n"
            "- update_relationship: {target_id: string, new_status: string}\n"
            "Forbidden: modifying hp, attributes, skills, inventory, slots, or moving actors."
        )

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        if not isinstance(intent, dict):
            return False
        desired = intent.get("desired_outcome")
        if not isinstance(desired, dict):
            return False
        # Hard allowlist for operation type
        allowed_ops = {"add_memory", "update_memory_status", "add_goal", "update_goal_status", "update_relationship"}
        op = next((k for k in desired.keys() if k in allowed_ops), None)
        if not op:
            return False

        # Quick format checks
        if op == "add_memory":
            data = desired[op]
            return isinstance(data, dict) and isinstance(data.get("text", ""), str)
        if op == "update_memory_status":
            data = desired[op]
            return isinstance(data, dict) and isinstance(data.get("match_text", ""), str) and data.get("new_status") in {"active", "recalled", "archived", "consolidated"}
        if op == "add_goal":
            data = desired[op]
            return isinstance(data, dict) and isinstance(data.get("text", ""), str) and isinstance(data.get("type", ""), str)
        if op == "update_goal_status":
            data = desired[op]
            return isinstance(data, dict) and isinstance(data.get("match_text", ""), str) and data.get("new_status") in {"active", "pending", "done", "cancelled"}
        if op == "update_relationship":
            data = desired[op]
            return isinstance(data, dict) and isinstance(data.get("target_id", ""), str) and isinstance(data.get("new_status", ""), str)
        return False

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        """
        This tool produces a single 'reason' event which is then applied deterministically by the world/simulator.
        World.apply_event should implement the mutations under this allowlist.
        """
        thought = intent.get("thought", "")
        desired = intent.get("desired_outcome", {})
        payload = {
            "thought": thought,
            "desired_outcome": desired,
        }
        return [Event(event_type="reason", tick=tick, actor_id=actor.id, payload=payload)]
