import random
from typing import Dict

from engine.data_models import NPC, ItemBlueprint
from engine.world_state import WorldState


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


_PROFICIENCY_MAP = {
    "novice": 1,
    "proficient": 2,
    "expert": 3,
    "master": 4,
}


def proficiency_bonus(level: str) -> int:
    return _PROFICIENCY_MAP.get(level, 0)


def roll_dice(spec: str) -> int:
    num, die = spec.lower().split('d')
    num = int(num)
    die = int(die)
    return sum(random.randint(1, die) for _ in range(num))


_DEFAULT_UNARMED = ItemBlueprint(
    id="unarmed",
    name="Unarmed",
    weight=0,
    damage_dice="1d4",
    damage_type="bludgeoning",
    armour_rating=0,
    skill_tag="unarmed_combat",
)


def get_weapon(world: WorldState, actor: NPC) -> ItemBlueprint:
    inst_id = actor.slots.get("main_hand")
    if inst_id and inst_id in world.item_instances:
        inst = world.get_item_instance(inst_id)
        bp = world.get_item_blueprint(inst.blueprint_id)
        return bp
    return _DEFAULT_UNARMED


def compute_ac(world: WorldState, actor: NPC) -> int:
    ac = 10
    # armour from equipped items
    for inst_id in actor.slots.values():
        if inst_id and inst_id in world.item_instances:
            inst = world.get_item_instance(inst_id)
            bp = world.get_item_blueprint(inst.blueprint_id)
            ac += getattr(bp, "armour_rating", 0)
    dex = actor.attributes.get("dexterity", 10)
    ac += ability_modifier(dex)
    return ac


def resolve_attack(world: WorldState, attacker: NPC, target: NPC) -> Dict[str, int]:
    weapon = get_weapon(world, attacker)
    # choose ability
    str_mod = ability_modifier(attacker.attributes.get("strength", 10))
    dex_mod = ability_modifier(attacker.attributes.get("dexterity", 10))
    if "finesse" in getattr(weapon, "properties", []):
        attr_mod = max(str_mod, dex_mod)
    else:
        attr_mod = str_mod
    prof_level = attacker.skills.get(weapon.skill_tag, "")
    prof_bonus = proficiency_bonus(prof_level)
    d20 = roll_dice("1d20")
    to_hit = d20 + attr_mod + prof_bonus
    target_ac = compute_ac(world, target)
    hit = to_hit >= target_ac
    critical = d20 == 20
    damage = 0
    if hit:
        damage = roll_dice(weapon.damage_dice)
        if critical:
            damage += roll_dice(weapon.damage_dice)
        damage += attr_mod
    return {
        "hit": hit,
        "damage": damage,
        "to_hit": to_hit,
        "target_ac": target_ac,
    }
