from __future__ import annotations

from typing import Optional, Dict, Any

from .events import Event
from .world_state import WorldState
from rpg import combat_rules


class Narrator:
    """Simple component turning events into plain text descriptions."""

    def __init__(self, world: WorldState):
        self.world = world
        # Dispatch table to avoid a long if/elif chain
        self.renderers = {
            "describe_location": self._r_describe_location,
            "move": self._r_move,
            "grab": self._r_grab,
            "drop": self._r_drop,
            "eat": self._r_eat,
            "attack_attempt": self._r_attack_attempt,
            "attack_hit": self._r_attack_hit,
            "attack_missed": self._r_attack_missed,
            "damage_applied": self._r_damage_applied,
            "talk": self._r_talk,
            "scream": self._r_scream,
            "talk_loud": self._r_talk_loud,
            "inventory": self._r_inventory,
            "stats": self._r_stats,
            "equip": self._r_equip,
            "unequip": self._r_unequip,
            "analyze": self._r_analyze,
            "give": self._r_give,
            "toggle_starvation": self._r_toggle_starvation,
            "open_connection": self._r_open_connection,
            "close_connection": self._r_close_connection,
            "npc_died": self._r_npc_died,
            "wait": self._r_wait,
            "rest": self._r_rest,
        }

    def render(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        handler = self.renderers.get(event.event_type)
        if handler:
            return handler(event, extra)
        return ""

    # Renderers

    def _r_describe_location(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        description = event.payload.get("description", "")
        occupants = event.payload.get("occupants", [])
        items = event.payload.get("items", [])
        parts = [description]
        if occupants:
            parts.append("You see: " + ", ".join(occupants))
        if items:
            parts.append("Items here: " + ", ".join(items))
        return " ".join(parts).strip()

    def _r_move(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        loc = self.world.get_location_static(event.target_ids[0])
        # Prefer a concise name/label if available; fallback to a shortened description
        label = getattr(loc, "name", None)
        if not label or not isinstance(label, str) or not label.strip():
            desc = getattr(loc, "description", "") or ""
            label = desc.split(".")[0].strip()[:60] or desc[:60]
        return f"{actor.name} moves to {label}."

    def _r_grab(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        item = self.world.get_item_instance(event.target_ids[0])
        bp = self.world.get_item_blueprint(item.blueprint_id)
        return f"{actor.name} picks up {bp.name}."

    def _r_drop(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        item = self.world.get_item_instance(event.target_ids[0])
        bp = self.world.get_item_blueprint(item.blueprint_id)
        return f"{actor.name} drops {bp.name}."

    def _r_eat(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        item_name = event.payload.get("item_name", "something")
        return f"{actor.name} eats {item_name}."

    def _r_attack_attempt(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        attacker = self.world.get_npc(event.actor_id)
        target = self.world.get_npc(event.target_ids[0])
        weapon = combat_rules.get_weapon(self.world, attacker)
        return f"{attacker.name} attacks {target.name} with {weapon.name}."

    def _r_attack_hit(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        attacker = self.world.get_npc(event.actor_id)
        target = self.world.get_npc(event.target_ids[0])
        return (
            f"{attacker.name} hits {target.name} "
            f"(roll {event.payload['to_hit']} vs AC {event.payload['target_ac']})"
        )

    def _r_attack_missed(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        attacker = self.world.get_npc(event.actor_id)
        target = self.world.get_npc(event.target_ids[0])
        return (
            f"{attacker.name} misses {target.name} "
            f"(roll {event.payload['to_hit']} vs AC {event.payload['target_ac']})"
        )

    def _r_damage_applied(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        target = self.world.get_npc(event.target_ids[0])
        amount = event.payload.get("amount", 0)
        dmg_type = event.payload.get("damage_type", "")
        return f"{target.name} takes {amount} {dmg_type} damage (HP: {target.hp})"

    def _r_talk(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        speaker = self.world.get_npc(event.actor_id)
        content = event.payload.get("content", "")
        # Prefer structured payload for recipient, fallback to target_ids
        recipient_id = event.payload.get("recipient_id") or (event.target_ids[0] if event.target_ids else None)
        if recipient_id:
            target = self.world.get_npc(recipient_id)
            return f"{speaker.name} to {target.name}: {content}"
        if event.payload.get("interject") and event.payload.get("conversation_id"):
            return f"{speaker.name} interjects: {content}"
        return f"{speaker.name} says: {content}"

    def _r_scream(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        speaker = self.world.get_npc(event.actor_id)
        content = event.payload.get("content", "")
        return f"{speaker.name} screams: {content}"

    def _r_talk_loud(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        speaker = self.world.get_npc(event.actor_id)
        content = event.payload.get("content", "")
        return f"{speaker.name} shouts: {content}"

    def _r_inventory(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        items = event.payload.get("items", [])
        if items:
            return f"{actor.name} carries: {', '.join(items)}"
        return f"{actor.name} carries nothing."

    def _r_stats(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        hp = event.payload.get("hp", 0)
        attrs = event.payload.get("attributes", {})
        skills = event.payload.get("skills", {})
        hunger = event.payload.get("hunger_stage")
        parts = [f"HP: {hp}"]
        if attrs:
            attr_str = ", ".join(f"{k}: {v}" for k, v in attrs.items())
            parts.append(f"Attributes: {attr_str}")
        if skills:
            skill_str = ", ".join(f"{k} ({v})" for k, v in skills.items())
            parts.append(f"Skills: {skill_str}")
        if hunger:
            parts.append(f"Hunger: {hunger}")
        return f"{actor.name} stats - " + "; ".join(parts)

    def _r_equip(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        item = self.world.get_item_instance(event.target_ids[0])
        bp = self.world.get_item_blueprint(item.blueprint_id)
        slot = event.payload.get("slot", "")
        return f"{actor.name} equips {bp.name} to {slot}."

    def _r_unequip(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        item = self.world.get_item_instance(event.target_ids[0])
        bp = self.world.get_item_blueprint(item.blueprint_id)
        slot = event.payload.get("slot", "")
        return f"{actor.name} removes {bp.name} from {slot}."

    def _r_analyze(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        name = event.payload.get("name", "")
        weight = event.payload.get("weight")
        damage = event.payload.get("damage_dice")
        dmg_type = event.payload.get("damage_type")
        armour = event.payload.get("armour_rating")
        props = event.payload.get("properties", [])
        parts = [f"{name} (weight {weight})"]
        if damage:
            parts.append(f"Damage: {damage} {dmg_type}")
        if armour:
            parts.append(f"Armour rating: {armour}")
        if props:
            parts.append("Properties: " + ", ".join(props))
        return " ".join(parts)

    def _r_give(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        # Prefer structured payload if available
        item_id = event.payload.get("item_id") or (event.target_ids[0] if event.target_ids else None)
        recipient_id = event.payload.get("recipient_id") or (event.target_ids[1] if len(event.target_ids) > 1 else None)
        if not item_id or not recipient_id:
            return ""
        item = self.world.get_item_instance(item_id)
        target = self.world.get_npc(recipient_id)
        bp = self.world.get_item_blueprint(item.blueprint_id)
        return f"{actor.name} gives {bp.name} to {target.name}."

    def _r_toggle_starvation(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        enabled = event.payload.get("enabled", True)
        return "Starvation enabled." if enabled else "Starvation disabled."

    def _r_open_connection(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        loc = self.world.get_location_static(event.target_ids[0])
        return f"{actor.name} opens the way to {loc.description}."

    def _r_close_connection(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        loc = self.world.get_location_static(event.target_ids[0])
        return f"{actor.name} closes the way to {loc.description}."

    def _r_npc_died(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        return f"{actor.name} dies."

    def _r_wait(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        ticks = event.payload.get("ticks", 1)
        if ticks == 1:
            return f"{actor.name} waits."
        return f"{actor.name} waits for {ticks} ticks."

    def _r_rest(self, event: Event, extra: Optional[Dict[str, Any]] = None) -> str:
        actor = self.world.get_npc(event.actor_id)
        ticks = event.payload.get("ticks", 1)
        healed = event.payload.get("healed", 0)
        if ticks == 1:
            return f"{actor.name} rests and recovers {healed} HP."
        return f"{actor.name} rests for {ticks} ticks and recovers {healed} HP."
