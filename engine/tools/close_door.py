from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class CloseDoorTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="close", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        target = intent.get("target_location")
        # Validate target exists in dynamic state
        if not target or target not in world.locations_state:
            return False
        current = world.find_npc_location(actor.id)
        if not current:
            return False
        # Must be a dynamic neighbor
        loc_state = world.get_location_state(current)
        if target not in (loc_state.connections_state or {}):
            return False
        # Allow close only if currently open
        status = (loc_state.connections_state.get(target, {}) or {}).get("status", "open")
        return status == "open"

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        return [
            Event(
                event_type="close_connection",
                tick=tick,
                actor_id=actor.id,
                target_ids=[intent["target_location"]],
            )
        ]
