from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class ToggleStarvationTool(Tool):
    def __init__(self, time_cost: int = 0):
        super().__init__(name="toggle_starvation", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        return isinstance(intent.get("enabled"), bool)

    def generate_events(
        self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int
    ) -> List[Event]:
        return [
            Event(
                event_type="toggle_starvation",
                tick=tick,
                actor_id=actor.id,
                target_ids=[],
                payload={"enabled": intent["enabled"]},
            )
        ]
