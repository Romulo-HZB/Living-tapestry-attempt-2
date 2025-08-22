from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class TalkTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="talk", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        # Expect at least 'content' string; optional 'target_id'
        content = intent.get("content")
        if not isinstance(content, str) or not content:
            return False
        target = intent.get("target_id")
        if target is not None and target not in world.npcs:
            return False
        # ensure speaker and target share location if target given
        if target:
            actor_loc = world.find_npc_location(actor.id)
            target_loc = world.find_npc_location(target)
            if actor_loc != target_loc:
                return False
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        return [
            Event(
                event_type="talk",
                tick=tick,
                actor_id=actor.id,
                target_ids=[intent.get("target_id")] if intent.get("target_id") else [],
                payload={"content": intent["content"]},
            )
        ]
