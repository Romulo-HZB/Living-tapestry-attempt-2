from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class InventoryTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="inventory", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        items = []
        for item_id in actor.inventory:
            instance = world.get_item_instance(item_id)
            blueprint = world.get_item_blueprint(instance.blueprint_id)
            items.append(blueprint.name)
        return [
            Event(
                event_type="inventory",
                tick=tick,
                actor_id=actor.id,
                payload={"items": items},
            )
        ]
