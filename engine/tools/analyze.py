from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class AnalyzeTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="analyze", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        if not item_id:
            return False
        if item_id in actor.inventory:
            return True
        loc_id = world.find_npc_location(actor.id)
        if not loc_id:
            return False
        loc_state = world.get_location_state(loc_id)
        return item_id in loc_state.items

    def generate_events(
        self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int
    ) -> List[Event]:
        item_id = intent["item_id"]
        inst = world.get_item_instance(item_id)
        bp = world.get_item_blueprint(inst.blueprint_id)
        payload = {
            "name": bp.name,
            "weight": bp.weight,
            "damage_dice": bp.damage_dice,
            "damage_type": bp.damage_type,
            "armour_rating": bp.armour_rating,
            "properties": bp.properties,
        }
        return [
            Event(
                event_type="analyze",
                tick=tick,
                actor_id=actor.id,
                target_ids=[item_id],
                payload=payload,
            )
        ]
