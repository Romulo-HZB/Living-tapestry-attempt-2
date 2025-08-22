from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class GiveTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="give", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        target_id = intent.get("target_id")
        if not item_id or not target_id:
            return False
        if item_id not in actor.inventory:
            return False
        if target_id not in world.npcs:
            return False
        actor_loc = world.find_npc_location(actor.id)
        target_loc = world.find_npc_location(target_id)
        return actor_loc is not None and actor_loc == target_loc

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        item_id = intent["item_id"]
        target_id = intent["target_id"]
        # Populate both structured payload and legacy target_ids for compatibility
        return [
            Event(
                event_type="give",
                tick=tick,
                actor_id=actor.id,
                target_ids=[item_id, target_id],
                payload={
                    "item_id": item_id,
                    "recipient_id": target_id,
                },
            )
        ]
