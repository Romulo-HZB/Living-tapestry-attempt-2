from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class MoveTool(Tool):
    def __init__(self, time_cost: int = 5):
        super().__init__(name="move", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        target = intent.get("target_location")
        # Validate against dynamic state only (layout is dynamic)
        if not target or target not in world.locations_state:
            return False
        current = world.find_npc_location(actor.id)
        if not current:
            return False
        # Target must be listed in dynamic connections
        loc_state = world.get_location_state(current)
        if target not in (loc_state.connections_state or {}):
            return False
        conn = loc_state.connections_state.get(target, {})
        return conn.get("status", "open") == "open"

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        dest = intent["target_location"]
        # Provide structured payload and legacy target_ids for compatibility
        return [Event(
            event_type="move",
            tick=tick,
            actor_id=actor.id,
            target_ids=[dest],
            payload={
                "to_location_id": dest
            },
        )]
