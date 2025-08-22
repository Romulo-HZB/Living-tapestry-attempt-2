from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class EatTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="eat", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        if not item_id or item_id not in actor.inventory:
            return False
        item = world.get_item_instance(item_id)
        bp = world.get_item_blueprint(item.blueprint_id)
        return "food" in bp.properties

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        item = world.get_item_instance(intent["item_id"])
        bp = world.get_item_blueprint(item.blueprint_id)
        return [
            Event(
                event_type="eat",
                tick=tick,
                actor_id=actor.id,
                target_ids=[intent["item_id"]],
                payload={"item_name": bp.name},
            )
        ]
