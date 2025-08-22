from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC, Memory
from .base import Tool


@dataclass
class ReflectTool(Tool):
    """
    Reflection/consolidation tool. Allows an actor to:
    - summarize recent events into new higher-level memories (optionally core)
    - mark older detailed memories as consolidated/archived
    This tool does not mutate stats/inventory/slots.
    """
    name: str = "reflect"
    time_cost: int = 5  # reflection takes longer than a normal action

    def get_llm_prompt_fragment(self) -> str:
        return (
            "reflect(thought: string, outputs: {"
            " new_core_memories?: [{text, confidence?, is_secret?, payload?}],"
            " new_memories?: [{text, confidence?, is_secret?, payload?}],"
            " archive_matches?: [string],"
            " consolidate_matches?: [string]"
            "})"
        )

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        if not isinstance(intent, dict):
            return False
        outputs = intent.get("outputs")
        if outputs is None or not isinstance(outputs, dict):
            return False
        # Basic sanity checks if present
        for key in ("new_core_memories", "new_memories"):
            vals = outputs.get(key)
            if vals is not None and not isinstance(vals, list):
                return False
        for key in ("archive_matches", "consolidate_matches"):
            vals = outputs.get(key)
            if vals is not None and not isinstance(vals, list):
                return False
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        """
        Emits a single 'reflect' event which world_state.apply_event will handle deterministically:
        - add Memory objects to npc.core_memories or npc.memories
        - mark matched memories as archived/consolidated based on substring matching
        """
        thought = intent.get("thought", "")
        outputs = intent.get("outputs", {}) or {}
        payload = {
            "thought": thought,
            "outputs": outputs,
        }
        return [Event(event_type="reflect", tick=tick, actor_id=actor.id, payload=payload)]
