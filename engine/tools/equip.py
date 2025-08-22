from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class EquipTool(Tool):
    def __init__(self, time_cost: int = 2):
        super().__init__(name="equip", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        item_id = intent.get("item_id")
        slot = intent.get("slot")
        if not item_id or not slot:
            return False
        if item_id not in actor.inventory:
            return False
        if slot not in actor.slots:
            return False
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        item_id = intent["item_id"]
        slot = intent["slot"]
        # Provide structured payload and legacy target_ids for compatibility
        return [
            Event(
                event_type="equip",
                tick=tick,
                actor_id=actor.id,
                target_ids=[item_id],
                payload={
                    "item_id": item_id,
                    "slot": slot,
                },
            )
        ]
