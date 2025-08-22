from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class StatsTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="stats", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        return [
            Event(
                event_type="stats",
                tick=tick,
                actor_id=actor.id,
                payload={
                    "hp": actor.hp,
                    "attributes": actor.attributes,
                    "skills": actor.skills,
                    "hunger_stage": actor.hunger_stage,
                },
            )
        ]
