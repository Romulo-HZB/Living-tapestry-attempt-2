import json
from pathlib import Path
from typing import Dict, Optional, Any

from .data_models import (
    NPC,
    LocationStatic,
    LocationState,
    ItemBlueprint,
    ItemInstance,
)
from .events import Event
from .data_models import Memory, Goal


# Shared hex-direction inverse map (DRY for hydration and event handling)
HEX_DIR_INVERSE = {
    "north": "south",
    "south": "north",
    "north_east": "south_west",
    "north-east": "south-west",
    "northeast": "southwest",
    "south_west": "north_east",
    "south-west": "north-east",
    "southwest": "northeast",
    "south_east": "north_west",
    "south-east": "north-west",
    "southeast": "northwest",
    "north_west": "south_east",
    "north-west": "south-east",
    "northwest": "southeast",
    "east": "west",
    "west": "east",
}


class WorldState:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.npcs: Dict[str, NPC] = {}
        self.locations_static: Dict[str, LocationStatic] = {}
        self.locations_state: Dict[str, LocationState] = {}
        self.item_blueprints: Dict[str, ItemBlueprint] = {}
        self.item_instances: Dict[str, ItemInstance] = {}

    def load(self):
        self._load_npcs()
        self._load_locations()
        self._load_items()
        # Hydrate dynamic connection directions from static hex layout for initial world
        try:
            self._hydrate_connection_directions()
        except Exception:
            # Non-fatal; renderer can still function with status-only edges
            pass
        # assign current_location for items based on location state
        for loc_id, state in self.locations_state.items():
            for item_id in state.items:
                inst = self.item_instances.get(item_id)
                if inst and inst.current_location is None:
                    inst.current_location = loc_id

    def _load_npcs(self):
        npcs_dir = self.data_dir / "npcs"
        for path in npcs_dir.glob("*.json"):
            with open(path, "r") as f:
                data = json.load(f)
            if "next_available_tick" not in data:
                data["next_available_tick"] = 0
            if "last_meal_tick" not in data:
                data["last_meal_tick"] = 0
            if "hunger_stage" not in data:
                data["hunger_stage"] = "sated"
            npc = NPC(**data)
            self.npcs[npc.id] = npc

    def _load_locations(self):
        loc_dir = self.data_dir / "locations"
        for path in loc_dir.glob("*_static.json"):
            with open(path, "r") as f:
                data = json.load(f)
            loc = LocationStatic(**data)
            self.locations_static[loc.id] = loc
        for path in loc_dir.glob("*_state.json"):
            with open(path, "r") as f:
                data = json.load(f)
            loc = LocationState(**data)
            self.locations_state[loc.id] = loc

    def _hydrate_connection_directions(self):
        """
        Populate connections_state[neighbor_id]['direction'] using static.hex_connections
        for initial load. Does not overwrite an existing direction.
        Also attempts to ensure reciprocal edges have inverse directions.
        """
        for loc_id, loc_static in self.locations_static.items():
            # Get dynamic state for this location
            state = self.locations_state.get(loc_id)
            if not state:
                continue
            hex_conns = getattr(loc_static, "hex_connections", {}) or {}
            for dir_key, neighbor_id in hex_conns.items():
                # Ensure entry exists in dynamic map
                entry = state.connections_state.setdefault(neighbor_id, {})
                # Preserve existing status; default to 'open' if entirely missing
                if "status" not in entry:
                    entry["status"] = "open"
                # Only set direction if absent
                if "direction" not in entry and isinstance(dir_key, str):
                    entry["direction"] = dir_key
                # Also ensure reciprocal neighbor has an entry with inverse direction
                recip = self.locations_state.get(neighbor_id)
                if recip is not None:
                    recip_entry = recip.connections_state.setdefault(loc_id, {})
                    if "status" not in recip_entry:
                        recip_entry["status"] = entry.get("status", "open")
                    if "direction" not in recip_entry:
                        inv = HEX_DIR_INVERSE.get(dir_key)
                        if inv:
                            recip_entry["direction"] = inv

    def _load_items(self):
        items_dir = self.data_dir / "items"
        catalog_path = items_dir / "catalog.json"
        if catalog_path.exists():
            with open(catalog_path, "r") as f:
                catalog = json.load(f)
            for item_id, data in catalog.items():
                blueprint = ItemBlueprint(id=item_id, **data)
                self.item_blueprints[blueprint.id] = blueprint

        instances_dir = items_dir / "instances"
        if instances_dir.exists():
            for path in instances_dir.glob("*.json"):
                with open(path, "r") as f:
                    data = json.load(f)
                instance = ItemInstance(**data)
                self.item_instances[instance.id] = instance

    def get_npc(self, npc_id: str) -> NPC:
        return self.npcs[npc_id]

    def get_location_static(self, loc_id: str) -> LocationStatic:
        return self.locations_static[loc_id]

    def get_location_state(self, loc_id: str) -> LocationState:
        return self.locations_state[loc_id]

    def get_item_instance(self, item_id: str) -> ItemInstance:
        return self.item_instances[item_id]

    def get_item_blueprint(self, blueprint_id: str) -> ItemBlueprint:
        return self.item_blueprints[blueprint_id]

    def find_npc_location(self, npc_id: str) -> Optional[str]:
        for loc_id, loc in self.locations_state.items():
            if npc_id in loc.occupants:
                return loc_id
        return None

    def update_hunger(self, current_tick: int) -> list[Event]:
        HUNGRY_THRESHOLD = 20
        STARVING_THRESHOLD = 40
        events: list[Event] = []
        for npc in self.npcs.values():
            if "dead" in npc.tags.get("dynamic", []):
                continue
            ticks_since = current_tick - npc.last_meal_tick
            if ticks_since >= STARVING_THRESHOLD:
                npc.hunger_stage = "starving"
                events.append(
                    Event(
                        event_type="damage_applied",
                        tick=current_tick,
                        actor_id=npc.id,
                        target_ids=[npc.id],
                        payload={"amount": 1, "damage_type": "starvation"},
                    )
                )
            elif ticks_since >= HUNGRY_THRESHOLD:
                npc.hunger_stage = "hungry"
            else:
                npc.hunger_stage = "sated"
        return events

    def apply_event(self, event):
        if event.event_type == "move":
            actor_id = event.actor_id
            target = (event.target_ids[0] if event.target_ids else None)
            if not target:
                return
            current_loc = self.find_npc_location(actor_id)
            if current_loc and actor_id in self.locations_state.get(current_loc, LocationState(id=current_loc, occupants=[], items=[], sublocations=[], transient_effects=[], connections_state={})).occupants:
                try:
                    self.locations_state[current_loc].occupants.remove(actor_id)
                except ValueError:
                    pass
            self.locations_state.setdefault(target, LocationState(id=target, occupants=[], items=[], sublocations=[], transient_effects=[], connections_state={})).occupants.append(actor_id)
        elif event.event_type == "grab":
            actor_id = event.actor_id
            item_id = event.target_ids[0]
            loc_id = self.find_npc_location(actor_id)
            if loc_id and item_id in self.locations_state[loc_id].items:
                self.locations_state[loc_id].items.remove(item_id)
                self.npcs[actor_id].inventory.append(item_id)
                inst = self.item_instances.get(item_id)
                if inst:
                    inst.owner_id = actor_id
                    inst.current_location = None
        elif event.event_type == "drop":
            actor_id = event.actor_id
            item_id = event.target_ids[0]
            loc_id = self.find_npc_location(actor_id)
            if loc_id and item_id in self.npcs[actor_id].inventory:
                self.npcs[actor_id].inventory.remove(item_id)
                self.locations_state[loc_id].items.append(item_id)
                inst = self.item_instances.get(item_id)
                if inst:
                    inst.owner_id = None
                    inst.current_location = loc_id
        elif event.event_type == "eat":
            actor_id = event.actor_id
            item_id = event.target_ids[0]
            npc = self.npcs.get(actor_id)
            if npc and item_id in npc.inventory:
                npc.inventory.remove(item_id)
                self.item_instances.pop(item_id, None)
                npc.last_meal_tick = event.tick
                npc.hunger_stage = "sated"
        elif event.event_type == "damage_applied":
            target_id = event.target_ids[0]
            amount = event.payload.get("amount", 0)
            npc = self.npcs.get(target_id)
            if npc:
                npc.hp = max(npc.hp - amount, 0)
        elif event.event_type == "rest":
            actor_id = event.actor_id
            healed = event.payload.get("healed", 0)
            npc = self.npcs.get(actor_id)
            if npc:
                # Compute a deterministic max HP from constitution; default constitution=10.
                constitution = npc.attributes.get("constitution", 10)
                max_hp = max(1, constitution * 2)
                npc.hp = min(npc.hp + healed, max_hp)
        elif event.event_type == "equip":
            actor_id = event.actor_id
            item_id = event.target_ids[0]
            slot = event.payload.get("slot")
            npc = self.npcs.get(actor_id)
            if npc and slot in npc.slots and item_id in npc.inventory:
                current = npc.slots.get(slot)
                if current:
                    npc.inventory.append(current)
                npc.inventory.remove(item_id)
                npc.slots[slot] = item_id
        elif event.event_type == "unequip":
            actor_id = event.actor_id
            slot = event.payload.get("slot")
            npc = self.npcs.get(actor_id)
            if npc and slot in npc.slots and npc.slots.get(slot):
                item_id = npc.slots[slot]
                npc.inventory.append(item_id)
                npc.slots[slot] = None
        elif event.event_type == "give":
            actor_id = event.actor_id
            # Prefer structured payload, fallback to target_ids for backward compatibility
            payload = event.payload or {}
            target_ids = event.target_ids or []
            item_id = payload.get("item_id") or (target_ids[0] if target_ids else None)
            target_id = payload.get("recipient_id") or (target_ids[1] if len(target_ids) > 1 else None)
            if not item_id or not target_id:
                return
            giver = self.npcs.get(actor_id)
            receiver = self.npcs.get(target_id)
            if giver and receiver and item_id in giver.inventory:
                try:
                    giver.inventory.remove(item_id)
                except ValueError:
                    pass
                receiver.inventory.append(item_id)
                inst = self.item_instances.get(item_id)
                if inst:
                    inst.owner_id = target_id
        elif event.event_type == "open_connection":
            actor_loc = self.find_npc_location(event.actor_id)
            target = event.target_ids[0]
            if actor_loc:
                fr = self.locations_state[actor_loc].connections_state.setdefault(target, {})
                to = self.locations_state[target].connections_state.setdefault(actor_loc, {})
                fr["status"] = "open"
                to["status"] = "open"
                # Preserve existing directions; if missing, attempt to infer from static layout
                try:
                    if "direction" not in fr:
                        static = self.locations_static.get(actor_loc)
                        if static:
                            for d, nb in (getattr(static, "hex_connections", {}) or {}).items():
                                if nb == target:
                                    fr["direction"] = d
                                    break
                    if "direction" not in to:
                        # Inverse of the forward direction if available
                        fdir = fr.get("direction")
                        if isinstance(fdir, str):
                            inv = HEX_DIR_INVERSE.get(fdir)
                            if inv:
                                to["direction"] = inv
                except Exception:
                    pass
        elif event.event_type == "close_connection":
            actor_loc = self.find_npc_location(event.actor_id)
            target = event.target_ids[0]
            if actor_loc:
                self.locations_state[actor_loc].connections_state.setdefault(target, {})["status"] = "closed"
                self.locations_state[target].connections_state.setdefault(actor_loc, {})["status"] = "closed"
        elif event.event_type == "npc_died":
            npc = self.npcs.get(event.actor_id)
            if not npc:
                return
            loc_id = self.find_npc_location(npc.id)
            if loc_id and npc.id in self.locations_state[loc_id].occupants:
                self.locations_state[loc_id].occupants.remove(npc.id)
                # Drop inventory and equipped items
                all_items = list(npc.inventory)
                for slot, item_id in npc.slots.items():
                    if item_id:
                        all_items.append(item_id)
                        npc.slots[slot] = None
                for item_id in all_items:
                    self.locations_state[loc_id].items.append(item_id)
                    inst = self.item_instances.get(item_id)
                    if inst:
                        inst.owner_id = None
                        inst.current_location = loc_id
                npc.inventory.clear()
            # Mark as dead
            if "dead" not in npc.tags.get("dynamic", []):
                npc.tags.setdefault("dynamic", []).append("dead")
        elif event.event_type == "reason":
            # Deterministic handler for ReasonTool outcomes with a strict allowlist.
            actor_id = event.actor_id
            npc = self.npcs.get(actor_id)
            if not npc:
                return
            desired = (event.payload or {}).get("desired_outcome") or {}
            # Resolve which operation is requested
            if "add_memory" in desired and isinstance(desired["add_memory"], dict):
                data = desired["add_memory"]
                # Build Memory with defaults and safe coercions
                mem = Memory(
                    text=str(data.get("text", ""))[:1000],
                    tick=int(event.tick),
                    priority=str(data.get("priority", "normal")),
                    status=str(data.get("status", "active")),
                    source_id=str(data.get("source_id")) if data.get("source_id") is not None else None,
                    confidence=float(data.get("confidence", 1.0)),
                    is_secret=bool(data.get("is_secret", False)),
                    payload=dict(data.get("payload", {})) if isinstance(data.get("payload", dict)) else {},
                )
                npc.memories.append(mem)
                # Keep a soft cap to prevent runaway growth (archival policy later)
                if len(npc.memories) > 1000:
                    # Archive oldest 50
                    for old in npc.memories[:50]:
                        try:
                            old.status = "archived"
                        except Exception:
                            pass
            elif "update_memory_status" in desired and isinstance(desired["update_memory_status"], dict):
                data = desired["update_memory_status"]
                match_text = str(data.get("match_text", "")).lower()
                new_status = str(data.get("new_status", "active"))
                # Update the first matching memory by substring in text or payload text
                for m in npc.memories:
                    try:
                        hay = (m.text or "").lower()
                        if match_text and match_text in hay:
                            m.status = new_status
                            break
                    except Exception:
                        # Legacy dict memory
                        if isinstance(m, dict):
                            hay = json.dumps(m, ensure_ascii=False).lower()
                            if match_text and match_text in hay:
                                m["status"] = new_status
                                break
                        continue
            elif "add_goal" in desired and isinstance(desired["add_goal"], dict):
                data = desired["add_goal"]
                goal = Goal(
                    text=str(data.get("text", ""))[:500],
                    type=str(data.get("type", "note")),
                    priority=str(data.get("priority", "normal")),
                    status=str(data.get("status", "active")),
                    payload=dict(data.get("payload", {})) if isinstance(data.get("payload", dict)) else {},
                    expiry_tick=int(data.get("expiry_tick")) if data.get("expiry_tick") is not None else None,
                )
                npc.goals.append(goal)
                # Optional: cap goals length
                if len(npc.goals) > 100:
                    npc.goals = npc.goals[-100:]
            elif "update_goal_status" in desired and isinstance(desired["update_goal_status"], dict):
                data = desired["update_goal_status"]
                match_text = str(data.get("match_text", "")).lower()
                new_status = str(data.get("new_status", "active"))
                for g in npc.goals:
                    try:
                        if match_text and match_text in (g.text or "").lower():
                            g.status = new_status
                            break
                    except Exception:
                        # Legacy dict fallback
                        if isinstance(g, dict):
                            txt = str(g.get("text", "")).lower()
                            if match_text and match_text in txt:
                                g["status"] = new_status
                                break
                        continue
            elif "update_relationship" in desired and isinstance(desired["update_relationship"], dict):
                data = desired["update_relationship"]
                target_id = str(data.get("target_id", ""))
                new_status = str(data.get("new_status", ""))
                if target_id:
                    npc.relationships[target_id] = new_status
            # All other mutations (hp, inventory, slots, movement) are forbidden by design.
        elif event.event_type == "reflect":
            # Deterministic handler for ReflectTool outcomes.
            actor_id = event.actor_id
            npc = self.npcs.get(actor_id)
            if not npc:
                return
            outputs = (event.payload or {}).get("outputs") or {}

            def _mk_mem(d: Dict[str, Any]) -> Memory:
                return Memory(
                    text=str(d.get("text", ""))[:1000],
                    tick=int(event.tick),
                    priority=str(d.get("priority", "normal")) if d.get("priority") else "normal",
                    status="active",
                    source_id=actor_id,
                    confidence=float(d.get("confidence", 0.8)) if d.get("confidence") is not None else 0.8,
                    is_secret=bool(d.get("is_secret", False)),
                    payload=dict(d.get("payload", {})) if isinstance(d.get("payload", dict)) else {},
                )

            # Add new core memories
            for d in outputs.get("new_core_memories", []) or []:
                try:
                    mem = _mk_mem(d)
                    npc.core_memories.append(mem)
                    if len(npc.core_memories) > 50:
                        npc.core_memories = npc.core_memories[-50:]
                except Exception:
                    continue

            # Add new ordinary memories
            for d in outputs.get("new_memories", []) or []:
                try:
                    mem = _mk_mem(d)
                    npc.memories.append(mem)
                    if len(npc.memories) > 1000:
                        for old in npc.memories[:50]:
                            try:
                                old.status = "archived"
                            except Exception:
                                pass
                except Exception:
                    continue

            # Mark archive/consolidate by substring matches
            archive_matches = outputs.get("archive_matches", []) or []
            consolidate_matches = outputs.get("consolidate_matches", []) or []

            def _match_and_mark(mem_list):
                for m in mem_list:
                    try:
                        text = (m.text or "").lower()
                    except Exception:
                        if isinstance(m, dict):
                            text = json.dumps(m, ensure_ascii=False).lower()
                        else:
                            continue
                    for token in archive_matches:
                        if isinstance(token, str) and token.lower() in text:
                            try:
                                m.status = "archived"
                            except Exception:
                                if isinstance(m, dict):
                                    m["status"] = "archived"
                    for token in consolidate_matches:
                        if isinstance(token, str) and token.lower() in text:
                            try:
                                m.status = "consolidated"
                            except Exception:
                                if isinstance(m, dict):
                                    m["status"] = "consolidated"

            _match_and_mark(npc.memories)
            _match_and_mark(npc.core_memories)
