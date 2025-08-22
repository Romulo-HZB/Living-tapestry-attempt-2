from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class WaitTool(Tool):
    """Tool allowing an actor to deliberately pass time."""

    def __init__(self, time_cost: int = 1):
        # Allow overriding time_cost for consistency with other tools
        super().__init__(name="wait", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        ticks = intent.get("ticks", 1)
        return isinstance(ticks, int) and ticks >= 1

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        ticks = intent.get("ticks", 1)
        # Do not mutate shared Tool state; narration should occur immediately.
        return [
            Event(
                event_type="wait",
                tick=tick,
                actor_id=actor.id,
                payload={"ticks": ticks},
            )
        ]
