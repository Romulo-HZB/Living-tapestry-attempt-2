from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class UnequipTool(Tool):
    def __init__(self, time_cost: int = 2):
        super().__init__(name="unequip", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        slot = intent.get("slot")
        if not slot:
            return False
        if slot not in actor.slots:
            return False
        if not actor.slots.get(slot):
            return False
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        slot = intent["slot"]
        item_id = actor.slots[slot]
        # Provide structured payload and legacy target_ids for compatibility
        return [
            Event(
                event_type="unequip",
                tick=tick,
                actor_id=actor.id,
                target_ids=[item_id],
                payload={
                    "item_id": item_id,
                    "slot": slot,
                },
            )
        ]
