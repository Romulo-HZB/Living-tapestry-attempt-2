from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class TalkLoudTool(Tool):
    """Speak loudly so adjacent locations with open connections can hear."""

    def __init__(self, time_cost: int = 1):
        super().__init__(name="talk_loud", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        content = intent.get("content")
        return isinstance(content, str) and bool(content)

    def generate_events(
        self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int
    ) -> List[Event]:
        return [
            Event(
                event_type="talk_loud",
                tick=tick,
                actor_id=actor.id,
                target_ids=[],
                payload={"content": intent["content"]},
            )
        ]
