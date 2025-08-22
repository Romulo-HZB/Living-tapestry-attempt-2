from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class RestTool(Tool):
    """Spend time to recover hit points."""

    def __init__(self, time_cost: int = 1):
        # Allow overriding time_cost for consistency with other tools
        super().__init__(name="rest", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        ticks = intent.get("ticks", 1)
        return isinstance(ticks, int) and ticks >= 1

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        ticks = intent.get("ticks", 1)
        healed = ticks  # heal 1 HP per tick
        # Do not mutate shared Tool state; schedule narration immediately.
        return [
            Event(
                event_type="rest",
                tick=tick,
                actor_id=actor.id,
                payload={"ticks": ticks, "healed": healed},
            )
        ]
