from typing import Dict, Any, List

from .base import Tool
from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


class InterjectTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="interject", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        # Expect conversation_id and content
        convo_id = intent.get("conversation_id")
        content = intent.get("content")
        if not isinstance(convo_id, str) or not convo_id:
            return False
        if not isinstance(content, str) or not content:
            return False
        # Basic location co-presence validation will be enforced by simulator,
        # here we just accept structure.
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        # Use standard 'talk' event with content; simulator will interpret as interjection
        # by virtue of not being a participant yet and adding actor to convo.
        return [
            Event(
                event_type="talk",
                tick=tick,
                actor_id=actor.id,
                target_ids=[],
                payload={
                    "content": intent["content"],
                    "conversation_id": intent["conversation_id"],
                    "interject": True,
                },
            )
        ]


class LeaveConversationTool(Tool):
    def __init__(self, time_cost: int = 1):
        super().__init__(name="leave_conversation", time_cost=time_cost)

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        # No params required
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        # Special event the simulator will handle to remove actor from conversation
        return [
            Event(
                event_type="leave_conversation",
                tick=tick,
                actor_id=actor.id,
                target_ids=[],
                payload={},
            )
        ]
