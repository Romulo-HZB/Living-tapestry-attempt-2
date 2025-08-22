from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List

from ..events import Event
from ..world_state import WorldState
from ..data_models import NPC


@dataclass
class Tool:
    name: str
    time_cost: int = 1

    def get_llm_prompt_fragment(self) -> str:
        return self.name

    def validate_intent(self, intent: Dict[str, Any], world: WorldState, actor: NPC) -> bool:
        return True

    def generate_events(self, intent: Dict[str, Any], world: WorldState, actor: NPC, tick: int) -> List[Event]:
        raise NotImplementedError
