import sys
import os
from pathlib import Path
import argparse


# Allow running from repository root
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from engine.world_state import WorldState
from engine.simulator import Simulator
from engine.narrator import Narrator
from engine.tools.move import MoveTool
from engine.tools.talk import TalkTool
from engine.tools.talk_loud import TalkLoudTool
from engine.tools.scream import ScreamTool
from engine.tools.conversation import InterjectTool, LeaveConversationTool
from engine.tools.look import LookTool
from engine.tools.grab import GrabTool
from engine.tools.attack import AttackTool
from engine.tools.inventory import InventoryTool
from engine.tools.drop import DropTool
from engine.tools.stats import StatsTool
from engine.tools.equip import EquipTool
from engine.tools.unequip import UnequipTool
from engine.tools.analyze import AnalyzeTool
from engine.tools.eat import EatTool
from engine.tools.give import GiveTool
from engine.tools.open_door import OpenDoorTool
from engine.tools.close_door import CloseDoorTool
from engine.tools.toggle_starvation import ToggleStarvationTool
from engine.tools.wait import WaitTool
from engine.tools.rest import RestTool
from engine.llm_client import LLMClient

# -----------------------
# Helper: Player HUD
# -----------------------
def render_player_hud(sim: Simulator, world: WorldState, actor_id: str) -> None:
    player = world.get_npc(actor_id)
    if not player:
        return
    location_id = world.find_npc_location(actor_id)
    loc_static = world.get_location_static(location_id) if location_id else None
    loc_state = world.get_location_state(location_id) if location_id else None

    # Location summary
    loc_name = getattr(loc_static, "name", None) if loc_static else None
    loc_desc = getattr(loc_static, "description", None) if loc_static else None

    # Visible NPCs/items
    visible_npcs = []
    visible_items = []
    if loc_state:
        try:
            visible_npcs = [nid for nid in getattr(loc_state, "occupants", []) if nid != actor_id]
        except Exception:
            visible_npcs = []
        try:
            visible_items = list(getattr(loc_state, "items", []))
        except Exception:
            visible_items = []

    # Player summary
    hp = getattr(player, "hp", None)
    if hasattr(player, "attributes"):
        max_hp = getattr(player, "attributes", {}).get("constitution", hp)
        max_hp = max(1, max_hp * 2) if isinstance(max_hp, int) else hp
    else:
        max_hp = hp
    hunger = getattr(player, "hunger_stage", None)

    # Equipped summary (simple)
    equipped = []
    try:
        slots = getattr(player, "slots", {}) or {}
        for slot_name, slot_item in slots.items():
            if slot_item:
                equipped.append(f"{slot_name}:{slot_item}")
    except Exception:
        pass

    # Inventory short summary
    inv = []
    try:
        inv = list(getattr(player, "inventory", [])) if isinstance(getattr(player, "inventory", []), list) else []
    except Exception:
        inv = []
    inv_preview = inv[:3]
    more_inv = max(0, len(inv) - len(inv_preview))

    # Neighbors summary
    neighbors = []
    try:
        if loc_static and hasattr(loc_static, "hex_connections"):
            neighbors = list(loc_static.hex_connections.values())
    except Exception:
        neighbors = []

    print("\n=== STATUS ===")
    print(f"Tick: {sim.game_tick}")
    print(f"HP: {hp}/{max_hp}  Hunger: {hunger}")
    if equipped:
        print("Equipped:", ", ".join(equipped))
    else:
        print("Equipped: (none)")

    print("\n=== LOCATION ===")
    if loc_name:
        print(f"{loc_name}")
    if loc_desc:
        print(loc_desc)
    if neighbors:
        print("Neighbors:", ", ".join(neighbors))

    print("\n=== AROUND YOU ===")
    if visible_npcs:
        print("NPCs:", ", ".join(visible_npcs))
    else:
        print("NPCs: (none)")
    if visible_items:
        shown_items = visible_items[:5]
        more_items = max(0, len(visible_items) - len(shown_items))
        if shown_items:
            print("Items:", ", ".join(shown_items) + (f" (+{more_items} more)" if more_items else ""))
        else:
            print("Items: (none)")
    else:
        print("Items: (none)")

    print("\n=== INVENTORY (brief) ===")
    if inv_preview:
        print(", ".join(inv_preview) + (f" (+{more_inv} more)" if more_inv else ""))
    else:
        print("(empty)")
    print("Hints: type 'inventory' for full list; 'look' to reprint surroundings; 'stats' for details.\n")


SYSTEM_PROMPT = (
    "You are an intent detector for a text RPG. The player will type any natural language.\n"
    "Your job: map the input to EXACTLY ONE game tool and parameters, returning ONLY a single JSON object.\n"
    "Output format (no prose, no code fences): {\"tool\": string, \"params\": object}\n"
    "Available tools and schemas:\n"
    '{"tool":"look","params":{}}\n'
    '{"tool":"move","params":{"target_location":"<loc_id>"}}\n'
    '{"tool":"grab","params":{"item_id":"<item_id>"}}\n'
    '{"tool":"drop","params":{"item_id":"<item_id>"}}\n'
    '{"tool":"attack","params":{"target_id":"<npc_id>"}}\n'
    '{"tool":"talk","params":{"content":"<text>"}}\n'
    '{"tool":"talk","params":{"target_id":"<npc_id>","content":"<text>"}}\n'
    '{"tool":"talk_loud","params":{"content":"<text>"}}\n'
    '{"tool":"scream","params":{"content":"<text>"}}\n'
    '{"tool":"inventory","params":{}}\n'
    '{"tool":"stats","params":{}}\n'
    '{"tool":"equip","params":{"item_id":"<item_id>","slot":"<slot>"}}\n'
    '{"tool":"unequip","params":{"slot":"<slot>"}}\n'
    '{"tool":"analyze","params":{"item_id":"<item_id>"}}\n'
    '{"tool":"eat","params":{"item_id":"<item_id>"}}\n'
    '{"tool":"give","params":{"item_id":"<item_id>","target_id":"<npc_id>"}}\n'
    '{"tool":"open","params":{"target_location":"<loc_id>"}}\n'
    '{"tool":"close","params":{"target_location":"<loc_id>"}}\n'
    '{"tool":"toggle_starvation","params":{"enabled":true}}\n'
    '{"tool":"wait","params":{"ticks":1}}\n'
    '{"tool":"rest","params":{"ticks":1}}\n'
    "Guidelines:\n"
    "- Interpret synonyms: e.g., go/walk/head -> move; pick up -> grab; put down -> drop; yell/shout -> talk_loud; scream -> scream; check bag/backpack -> inventory; who am I/how am I -> stats; open/close gate/door -> open/close.\n"
    "- Prefer IDs present in provided context; if ambiguous, choose the most salient visible option or omit the param to let the engine validate.\n"
    "- If intent is unclear, default to {\"tool\":\"look\",\"params\":{}}.\n"
    "- If a numeric count/duration is implied (\"wait a bit\"), set ticks to a small integer (e.g., 1).\n"
    "- NEVER include any text outside the JSON."
)


# Non-blocking input helper for Windows so pygame can keep pumping frames
def read_input_with_ui(sim, prompt: str) -> str:
    try:
        import msvcrt  # type: ignore
    except Exception:
        return input(prompt)

    print(prompt, end="", flush=True)
    buf = []
    import time
    while True:
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                print("")
                return "".join(buf)
            elif ch == "\003":
                raise KeyboardInterrupt
            elif ch == "\b":
                if buf:
                    buf.pop()
                    print("\b \b", end="", flush=True)
            else:
                buf.append(ch)
                print(ch, end="", flush=True)
        # Pump UI frames to keep pygame responsive
        if hasattr(sim, "renderer") and sim.renderer:
            res = sim.renderer.run_once()
            if res is None:
                # Window closed
                return ""
        time.sleep(0.02)

def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    world = WorldState(Path("data"))
    world.load()

    narrator = Narrator(world)
    actor_id = "npc_sample"  # temporary player actor
    # Note: conversation system is in-engine; these tools route speech and conversation mgmt.
    sim = Simulator(world, narrator=narrator, player_id=actor_id)

    sim.register_tool(MoveTool())
    sim.register_tool(TalkTool())
    sim.register_tool(TalkLoudTool())
    sim.register_tool(ScreamTool())
    sim.register_tool(InterjectTool())
    sim.register_tool(LeaveConversationTool())
    sim.register_tool(LookTool())
    sim.register_tool(GrabTool())
    sim.register_tool(DropTool())
    sim.register_tool(AttackTool())
    sim.register_tool(InventoryTool())
    sim.register_tool(StatsTool())
    sim.register_tool(EquipTool())
    sim.register_tool(UnequipTool())
    sim.register_tool(AnalyzeTool())
    sim.register_tool(EatTool())
    sim.register_tool(GiveTool())
    sim.register_tool(OpenDoorTool())
    sim.register_tool(CloseDoorTool())
    sim.register_tool(ToggleStarvationTool())
    sim.register_tool(WaitTool())
    sim.register_tool(RestTool())
    # Always initialize LLM for intent detection
    llm = LLMClient(Path("config/llm.json"))
    if isinstance(getattr(llm, "endpoint", None), str) and "openrouter.ai" in llm.endpoint:
        if not getattr(llm, "api_key", None):
            raise RuntimeError("OpenRouter api_key is required in config/llm.json to play.")
    try:
        # Ensure NPC planner shares this same client/config
        sim.llm = llm
    except Exception:
        pass
    print("Type anything. Your input will be interpreted and mapped to an action. Type 'quit' to exit.")
    print("Tip: Enter an empty line to 'do nothing this turn'.")
    if getattr(args, "with_map", False) and hasattr(sim, "renderer") and sim.renderer:
        print("Map overlay active. Click location hex to drill into sublocations; use Back to return.")

    # Initial HUD before first input
    render_player_hud(sim, world, actor_id)

    # Open a run log file to capture everything that happens during this session
    # Overwrite on each new session to avoid unbounded growth/noise
    runlog_path = Path("exports/run_log.txt")
    try:
        runlog_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    log_fh = open(runlog_path, "w", encoding="utf-8")  # overwrite each run
    print(f"[Session] Logging to {runlog_path} (overwriting previous run)")

    # Simple stdout duplicator
    class _Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s)
                except Exception:
                    pass
        def flush(self):
            for st in self.streams:
                try:
                    st.flush()
                except Exception:
                    pass
    import sys as _sys
    _sys_stdout = _sys.stdout
    _sys.stdout = _Tee(_sys_stdout, log_fh)

    # Helper to log hidden reasoning blocks from the last LLM response file
    def _log_last_think(prefix: str):
        try:
            from engine.llm_client import LLMClient as _LLM
            extractor = _LLM().extract_think
            raw = ""
            try:
                with open("llm_last_response.txt", "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                raw = ""
            if raw:
                think = extractor(raw)
                if think:
                    print(f"[LLM think] {prefix}: {think}")
        except Exception:
            # Do not crash logging path
            pass

    # Also mirror the exact LLM request/response into the run log when available
    def _log_llm_io(label: str):
        try:
            # Pretty-print last request if available
            try:
                with open("llm_last_request.json", "r", encoding="utf-8") as f:
                    req_txt = f.read()
                if req_txt:
                    print(f"[LLM request] {label}:\n{req_txt}")
            except Exception:
                pass
            # Dump raw provider response body
            try:
                with open("llm_last_response.txt", "r", encoding="utf-8") as f:
                    resp_raw = f.read()
                if resp_raw:
                    print(f"[LLM response raw] {label} (first 2KB):\n{resp_raw[:2048]}")
            except Exception:
                pass
            # Dump parsed full JSON if provider returned OpenAI-like JSON
            try:
                with open("llm_last_full.json", "r", encoding="utf-8") as f:
                    resp_json = f.read()
                if resp_json:
                    print(f"[LLM response json] {label}:\n{resp_json}")
            except Exception:
                pass
        except Exception:
            # Never crash the game loop from logging
            pass

    while True:
        cmd = input("-> ").strip()
        # If user enters nothing, they 'do nothing' this turn: advance world state (NPCs act)
        if not cmd:
            # Drain pending events if any
            while sim.event_queue:
                sim.tick()
            # Run NPC cycle and advance one tick if any acted
            any_npc_acted = False
            if hasattr(sim, "run_npc_round"):
                while sim.run_npc_round():
                    any_npc_acted = True
                if any_npc_acted:
                    sim.tick()
            # Show HUD and continue loop
            render_player_hud(sim, world, actor_id)
            continue
        if cmd in {"quit", "exit"}:
            break

        # Build minimal additional context for the LLM to help with disambiguation
        player = world.get_npc(actor_id)
        location_id = world.find_npc_location(actor_id)
        loc_state = world.locations_state.get(location_id) if location_id else None
        visible_items = list(loc_state.items) if loc_state and hasattr(loc_state, "items") and isinstance(loc_state.items, list) else []
        visible_npcs = [nid for nid in (loc_state.occupants if loc_state and hasattr(loc_state, "occupants") else []) if nid != actor_id]
        inventory_items = list(player.inventory) if player and hasattr(player, "inventory") and isinstance(player.inventory, list) else []
        stats_summary = {
            "hp": getattr(player, "hp", None),
            "max_hp": getattr(player, "attributes", {}).get("constitution", getattr(player, "hp", None)) if hasattr(player, "attributes") else None,
            "hunger_stage": getattr(player, "hunger_stage", None),
        }

        additional_context = {
            "player_id": actor_id,
            "location_id": location_id,
            "visible_items": visible_items,
            "visible_npcs": visible_npcs,
            "inventory_items": inventory_items,
            "stats": stats_summary,
            "time_tick": sim.game_tick,
        }

        command = llm.parse_command(cmd, SYSTEM_PROMPT, additional_context=additional_context)
        _log_last_think("player_intent")
        _log_llm_io("player_intent")
        # Normalize common param aliases to engine schema before validation/processing
        if command and isinstance(command, dict):
            t = command.get("tool")
            params = command.get("params", {}) if isinstance(command.get("params"), dict) else {}
            if t == "move":
                # MoveTool.validate_intent expects 'target_location' (see engine/tools/move.py)
                # Accept neighbor names like "market square" by mapping to neighbor IDs when possible.
                loc = params.get("target_location") or params.get("location_id") or params.get("target") or params.get("to")
                # Normalize common display names to IDs visible from current location
                if not isinstance(loc, str) or loc not in (world.locations_static or {}):
                    try:
                        # Build a lowercase name->id map for neighbors only
                        name_to_id = {}
                        cur = world.find_npc_location(actor_id)
                        if cur:
                            static = world.get_location_static(cur)
                            # static.hex_connections is a dict of direction->neighbor_id; invert to id set
                            neighbor_ids = set(getattr(static, "hex_connections", {}).values())
                            for nid in neighbor_ids:
                                st = world.get_location_static(nid)
                                nname = getattr(st, "name", None) or getattr(st, "description", None) or nid
                                name_to_id[str(nname).lower()] = nid
                                # Also allow id itself as a key
                                name_to_id[str(nid).lower()] = nid
                        if isinstance(loc, str):
                            key = loc.strip().lower().replace("_", " ")
                            # Try exact, then try with spaces/underscores swapped
                            mapped = name_to_id.get(key) or name_to_id.get(key.replace(" ", "_")) or name_to_id.get(key.replace("_", " "))
                            if mapped:
                                loc = mapped
                    except Exception:
                        pass
                if isinstance(loc, str):
                    command["params"] = {"target_location": loc}
            elif t in {"open", "close"}:
                # Keep target_location for open/close tools if they validate on that; also mirror to location_id for broader compatibility
                tgt = params.get("target_location") or params.get("location_id") or params.get("target")
                if isinstance(tgt, str):
                    command["params"] = {"target_location": tgt, "location_id": tgt}
            elif t == "attack":
                tgt = params.get("target_id") or params.get("target") or params.get("target_ids")
                if isinstance(tgt, list):
                    command["params"] = {"target_id": tgt[0] if tgt else None}
                elif isinstance(tgt, str):
                    command["params"] = {"target_id": tgt}
            elif t in {"talk","talk_loud","scream"}:
                content = params.get("content")
                if not isinstance(content, str):
                    params["content"] = "..."
                else:
                    params["content"] = content[:200]
                command["params"] = params

        if not command or "tool" not in command or "params" not in command:
            print("I do nothing on my turn.")
            continue
        try:
            sim.process_command(actor_id, command)
        except ValueError as e:
            print("Error:", e)
            continue

        # Drain events generated by the player's action
        while sim.event_queue:
            sim.tick()

        # Run NPC cycle: each NPC acts once (LLM-driven) before returning to the player.
        # Advance global time exactly once after the full NPC round (per your timing model).
        if hasattr(sim, "run_npc_round"):
            any_npc_acted = False
            while sim.run_npc_round():
                any_npc_acted = True
            if any_npc_acted:
                sim.tick()

        # After full round and tick, show a concise HUD so the player gets needed info for free
        render_player_hud(sim, world, actor_id)

        # Ensure time advances until the player is ready again (if tools applied cooldown)
        while sim.world.get_npc(actor_id).next_available_tick > sim.game_tick:
            sim.tick()

    # Restore stdout and close log at end
    try:
        _sys.stdout = _sys_stdout
    except Exception:
        pass
    try:
        log_fh.flush()
        log_fh.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
