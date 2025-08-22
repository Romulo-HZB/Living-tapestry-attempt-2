from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class DropTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="drop", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        return bool(item_id in actor.inventory)

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        return [
            Event(
                event_type="drop",
                tick=tick,
                actor_id=actor.id,
                target_ids=[intent["item_id"]],
            )
        ]
