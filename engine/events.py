from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .data_models import PerceptionEvent


@dataclass
class Event:
    """Core event envelope passed through the simulator."""
    event_type: str
    tick: int
    actor_id: str
    target_ids: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)


# Conversation-related lightweight types (kept optional to avoid circular deps)
@dataclass
class ConversationSnapshot:
    """Minimal snapshot for narration/debugging. Not a full state container."""
    conversation_id: str
    participants: List[str]
    current_speaker: Optional[str]
    turn_order: List[str]
    last_interaction_tick: int


# Perception utilities
def make_perception_from_event(origin: Event, location_id: Optional[str] = None) -> PerceptionEvent:
    """
    Convert a core Event into a PerceptionEvent summary for NPC short-term memory.
    This intentionally strips details that are not needed for NPC reasoning prompts.
    """
    return PerceptionEvent(
        event_type=origin.event_type,
        tick=origin.tick,
        actor_id=origin.actor_id,
        location_id=location_id,
        payload=origin.payload.copy()
    )
