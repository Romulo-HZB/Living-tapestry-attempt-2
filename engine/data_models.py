from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Literal


@dataclass
class Memory:
    text: str = ""
    tick: int = 0
    priority: Literal["low", "normal", "high"] = "normal"
    status: Literal["active", "recalled", "archived", "consolidated"] = "active"
    source_id: Optional[str] = None
    confidence: float = 1.0
    is_secret: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PerceptionEvent:
    event_type: str = "generic"
    tick: int = 0
    actor_id: Optional[str] = None
    # Some downstream serializers expect target_ids and location_id; include them explicitly.
    target_ids: List[str] = field(default_factory=list)
    location_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Goal:
    text: str = ""
    type: str = "note"
    priority: Literal["low", "normal", "high"] = "normal"
    status: Literal["active", "pending", "done", "cancelled"] = "active"
    payload: Dict[str, Any] = field(default_factory=dict)
    expiry_tick: Optional[int] = None

@dataclass
class NPC:
    id: str
    name: str
    inventory: List[str] = field(default_factory=list)
    slots: Dict[str, Optional[str]] = field(default_factory=dict)
    hp: int = 0
    # Long-term memory (LTM) stored on disk
    memories: List[Memory] = field(default_factory=list)
    # Structured goals (permission_granted, go_to, seek_and_use, etc.)
    goals: List[Goal] = field(default_factory=list)
    relationships: Dict[str, str] = field(default_factory=dict)
    tags: Dict[str, List[str]] = field(default_factory=lambda: {"inherent": [], "dynamic": []})
    # Short-term memory (STM) buffer for recent perception events
    short_term_memory: List[PerceptionEvent] = field(default_factory=list)
    # Core memories/beliefs always included in prompts
    core_memories: List[Memory] = field(default_factory=list)
    known_locations: Dict[str, str] = field(default_factory=dict)
    next_available_tick: int = 0
    last_meal_tick: int = 0
    hunger_stage: str = "sated"
    attributes: Dict[str, int] = field(
        default_factory=lambda: {"strength": 10, "dexterity": 10, "constitution": 10}
    )
    skills: Dict[str, str] = field(default_factory=dict)


@dataclass
class LocationStatic:
    id: str
    description: str
    tags: Dict[str, List[str]] = field(default_factory=lambda: {"inherent": []})
    hex_connections: Dict[str, str] = field(default_factory=dict)


@dataclass
class LocationState:
    id: str
    occupants: List[str] = field(default_factory=list)
    items: List[str] = field(default_factory=list)
    sublocations: List[str] = field(default_factory=list)
    transient_effects: List[str] = field(default_factory=list)
    connections_state: Dict[str, dict] = field(default_factory=dict)

@dataclass
class ItemBlueprint:
    id: str
    name: str
    weight: int = 0
    damage_dice: str = "1d4"
    damage_type: str = "bludgeoning"
    armour_rating: int = 0
    skill_tag: str = "unarmed_combat"
    properties: List[str] = field(default_factory=list)

@dataclass
class ItemInstance:
    id: str
    blueprint_id: str
    current_location: Optional[str] = None
    owner_id: Optional[str] = None
    item_state: Dict[str, Any] = field(default_factory=dict)
    inventory: List[str] = field(default_factory=list)
    tags: Dict[str, List[str]] = field(default_factory=lambda: {"inherent": [], "dynamic": []})
