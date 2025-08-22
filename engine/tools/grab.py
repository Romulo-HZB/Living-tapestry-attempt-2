from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class GrabTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="grab", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        if not item_id or item_id not in world.item_instances:
            return False
        loc_id = world.find_npc_location(actor.id)
        if not loc_id:
            return False
        loc_state = world.get_location_state(loc_id)
        return item_id in loc_state.items

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        return [
            Event(
                event_type="grab",
                tick=tick,
                actor_id=actor.id,
                target_ids=[intent["item_id"]],
            )
        ]
