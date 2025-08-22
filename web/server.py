import os
import sys
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit
import json
from typing import Dict, Tuple, List, Optional, Any

# Add project root to path
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

# --- WebNarrator: extend Narrator to also emit narration over Socket.IO ---
class WebNarrator(Narrator):
    def __init__(self, world: WorldState, socketio: SocketIO):
        super().__init__(world)
        self._socketio = socketio

    def render(self, event, extra: Optional[Dict[str, Any]] = None) -> str:
        msg = super().render(event, extra)
        try:
            if msg:
                payload = {
                    "tick": getattr(event, "tick", None),
                    "event_type": getattr(event, "event_type", ""),
                    "actor_id": getattr(event, "actor_id", None),
                    "text": msg,
                }
                # Broadcast to all connected clients
                self._socketio.emit("log_line", payload)
        except Exception:
            # Socket emission should never break narration
            pass
        return msg
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'your-secret-key-change-this'
socketio = SocketIO(app, cors_allowed_origins="*")

# -----------------------
# Hex layout helpers
# -----------------------
# We adopt a FLAT-TOP axial coordinate system for hexes.
# Canonical six directions (clockwise): E, NE, NW, W, SW, SE
# Axial deltas (q, r):
#   E  = (+1,  0)
#   NE = (+1, -1)
#   NW = ( 0, -1)
#   W  = (-1,  0)
#   SW = (-1, +1)
#   SE = ( 0, +1)
_HEX_DIR_DELTAS = {
    "E":  ( 1,  0),
    "NE": ( 1, -1),
    "NW": ( 0, -1),
    "W":  (-1,  0),
    "SW": (-1,  1),
    "SE": ( 0,  1),
}

# Normalize a variety of direction spellings used in static data
# to one of the six canonical codes above. This keeps the map
# consistent and avoids "infinite-degree" graphs: each location
# can only have up to six neighbors (one per side).
def _normalize_dir(raw: str) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower().replace("-", "_")
    if key in {"east"}:
        return "E"
    if key in {"west"}:
        return "W"
    if key in {"north_east", "northeast"}:
        return "NE"
    if key in {"north_west", "northwest"}:
        return "NW"
    if key in {"south_west", "southwest"}:
        return "SW"
    if key in {"south_east", "southeast"}:
        return "SE"
    # Graceful aliases for cardinal-only inputs on flat-top:
    # map "south" to SE (down), and "north" to NW (up)
    if key == "south":
        return "SE"
    if key == "north":
        return "NW"
    return None

def _compute_axial_coordinates(world) -> Dict[str, Tuple[int, int]]:
    """
    Compute axial (q,r) for every location by BFS using canonical hex directions.

    Priority of direction sources for each edge (cur -> neighbor):
      1) Dynamic connections_state[cur][neighbor].direction if present and canonical
      2) Static hex_connections on cur (canonicalized)

    This allows newly created/edited edges (with set directions) to determine
    layout immediately, while still falling back to static authoring when
    dynamic directions are not defined.

    - Prefer a stable root: 'town_square' if present, else any location.
    - If multiple disconnected components exist, place each subsequent component
      far apart on the q-axis to avoid overlap.
    """
    from collections import deque

    coords: Dict[str, Tuple[int, int]] = {}
    if not world.locations_static:
        return coords

    # Unplaced set to support multiple components if needed
    unplaced = set(world.locations_static.keys())

    def bfs_component(start_id: str, base_q: int, base_r: int) -> None:
        coords[start_id] = (base_q, base_r)
        unplaced.discard(start_id)
        pos_to_id = { (base_q, base_r): start_id }
        dq = deque([start_id])

        while dq:
            cur = dq.popleft()
            cq, cr = coords[cur]

            # Build combined neighbor list. Prefer dynamic, union with static.
            edge_meta = _build_edges_for(cur, world) or {}
            # Build static reverse map neighbor -> direction (canonicalized)
            try:
                st = world.get_location_static(cur)
                static_map = {}
                for dkey, nb in (getattr(st, "hex_connections", {}) or {}).items():
                    canon = _normalize_dir(dkey)
                    if canon:
                        static_map[str(nb)] = canon
            except Exception:
                static_map = {}

            # Neighbor id set
            neighbor_ids = set(edge_meta.keys()) | set(static_map.keys())

            for nb in neighbor_ids:
                # Already placed -> nothing to do
                if nb in coords:
                    continue

                # Decide a direction to try first
                canon = _normalize_dir((edge_meta.get(nb) or {}).get("direction")) if nb in edge_meta else None
                if not canon:
                    canon = static_map.get(nb)

                # Generate candidate targets in priority order: preferred dir first, then remaining dirs
                dir_order = list(_HEX_DIR_DELTAS.keys())
                if canon in dir_order:
                    dir_order = [canon] + [d for d in dir_order if d != canon]

                placed_here = False
                for d in dir_order:
                    dq1, dr1 = _HEX_DIR_DELTAS[d]
                    target = (cq + dq1, cr + dr1)
                    # Free slot?
                    if target not in pos_to_id:
                        coords[nb] = target
                        pos_to_id[target] = nb
                        if nb in unplaced:
                            unplaced.discard(nb)
                        dq.append(nb)
                        placed_here = True
                        break

                # If all six adjacent slots are occupied, skip for now (will be placed when reached from another node)
                if not placed_here:
                    continue

    # Choose first component root
    if "town_square" in world.locations_static:
        start = "town_square"
    else:
        start = next(iter(world.locations_static.keys()))

    bfs_component(start, 0, 0)

    # Place remaining disconnected components far apart to avoid overlap
    component_index = 1
    while unplaced:
        sid = next(iter(unplaced))
        # Separate components by a large gap on q to prevent visual overlap
        bfs_component(sid, component_index * 1000, 0)
        component_index += 1

    return coords

def _build_edges_for(loc_id: str, world) -> Dict[str, Dict]:
    """
    Build a stable derived edge map for a location:
      neighbor_id -> {status: 'open'|'closed', direction: 'E'|'NE'|...}
    Uses dynamic status when available, direction from dynamic if present,
    else falls back to static hex_connections to infer direction.
    """
    edges: Dict[str, Dict] = {}

    # Start from dynamic to preserve current open/closed status
    try:
        dyn = world.get_location_state(loc_id)
        for nb, meta in (getattr(dyn, "connections_state", {}) or {}).items():
            status = (meta or {}).get("status", "open")
            d_raw = (meta or {}).get("direction")
            edges[str(nb)] = {"status": status, "direction": _normalize_dir(d_raw) if d_raw else None}
    except Exception:
        pass

    # Fill any missing directions from static layout
    try:
        st = world.get_location_static(loc_id)
        for dkey, nb in (getattr(st, "hex_connections", {}) or {}).items():
            canon = _normalize_dir(dkey)
            rec = edges.setdefault(str(nb), {"status": "open", "direction": None})
            if rec.get("direction") is None and canon is not None:
                rec["direction"] = canon
    except Exception:
        pass

    return edges
# Global game state
world = None
simulator = None
llm_client = None
player_id = "npc_sample"

# System prompt for LLM command parsing
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

def initialize_game():
    """Initialize the game world and simulator"""
    global world, simulator, llm_client
    
    world = WorldState(Path("data"))
    world.load()
    
    # Seed NPC memories/goals for a meaningful LLM-driven run
    try:
        # Example: encourage the bard to roam and perform, and the blacksmith to seek materials
        if "npc_bard" in world.npcs:
            bard = world.npcs["npc_bard"]
            # Light, safe seed memories/goals (dataclass objects accepted by engine)
            from engine.data_models import Memory, Goal
            bard.memories.append(Memory(text="I love to play music in the town square.", tick=0, priority="normal", status="active", source_id="system", confidence=0.9, is_secret=False, payload={"topic":"music","place":"town_square"}))
            bard.goals.append(Goal(text="Perform a short tune for townsfolk.", type="task", priority="normal", status="active", payload={"place":"town_square"}, expiry_tick=None))
        if "npc_blacksmith" in world.npcs:
            smith = world.npcs["npc_blacksmith"]
            from engine.data_models import Memory, Goal
            smith.memories.append(Memory(text="Running low on scrap metal; check the market.", tick=0, priority="normal", status="active", source_id="system", confidence=0.85, is_secret=False, payload={"topic":"materials","place":"market_square"}))
            smith.goals.append(Goal(text="Acquire materials from the market or trade.", type="task", priority="normal", status="active", payload={"place":"market_square"}, expiry_tick=None))
        if "npc_guard" in world.npcs:
            guard = world.npcs["npc_guard"]
            from engine.data_models import Memory, Goal
            guard.memories.append(Memory(text="Keep watch over the town and discourage brawls.", tick=0, priority="normal", status="active", source_id="system", confidence=0.9, is_secret=False, payload={"topic":"order"}))
            guard.goals.append(Goal(text="Patrol between town_square and market_square.", type="routine", priority="normal", status="active", payload={"route":["town_square","market_square"]}, expiry_tick=None))
    except Exception as _e:
        # Non-fatal if data model changes; continue without seeding
        pass

    narrator = WebNarrator(world, socketio)
    simulator = Simulator(world, narrator=narrator, player_id=player_id)
    
    # Initialize LLM client
    try:
        llm_client = LLMClient(Path("config/llm.json"))
    except Exception as e:
        print(f"Warning: Could not initialize LLM client: {e}")
        llm_client = None
    
    # Attach shared LLM client to simulator for NPC planning
    try:
        if llm_client:
            simulator.llm = llm_client
    except Exception:
        pass

    # Register all tools
    simulator.register_tool(MoveTool())
    simulator.register_tool(TalkTool())
    simulator.register_tool(TalkLoudTool())
    simulator.register_tool(ScreamTool())
    simulator.register_tool(InterjectTool())
    simulator.register_tool(LeaveConversationTool())
    simulator.register_tool(LookTool())
    simulator.register_tool(GrabTool())
    simulator.register_tool(DropTool())
    simulator.register_tool(AttackTool())
    simulator.register_tool(InventoryTool())
    simulator.register_tool(StatsTool())
    simulator.register_tool(EquipTool())
    simulator.register_tool(UnequipTool())
    simulator.register_tool(AnalyzeTool())
    simulator.register_tool(EatTool())
    simulator.register_tool(GiveTool())
    simulator.register_tool(OpenDoorTool())
    simulator.register_tool(CloseDoorTool())
    simulator.register_tool(ToggleStarvationTool())
    simulator.register_tool(WaitTool())
    simulator.register_tool(RestTool())

# Initialize game on startup
initialize_game()

@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('static', 'index.html')

@app.route('/api/state')
def get_state():
    """Get current game state"""
    if not simulator:
        return jsonify({"error": "Game not initialized"}), 500
    
    # Get player location
    player_location = world.find_npc_location(player_id)
    
    # Get location details
    location_static = None
    location_state = None
    if player_location:
        try:
            location_static = world.get_location_static(player_location)
            location_state = world.get_location_state(player_location)
        except Exception:
            pass
    
    # Get visible NPCs and items
    visible_npcs = []
    visible_items = []
    if location_state:
        try:
            visible_npcs = [nid for nid in getattr(location_state, "occupants", []) if nid != player_id]
        except Exception:
            pass
        try:
            visible_items = list(getattr(location_state, "items", []))
        except Exception:
            pass
    
    # Get player details
    player = world.get_npc(player_id)
    inventory = []
    equipped = {}
    if player:
        try:
            inventory = list(getattr(player, "inventory", []))
        except Exception:
            pass
        try:
            equipped = dict(getattr(player, "slots", {}))
        except Exception:
            pass

    # Resolve helpers for names
    def _resolve_item(iid: str) -> Dict[str, str]:
        try:
            inst = world.item_instances.get(iid)
            if inst is None:
                return {"id": str(iid), "name": str(iid)}
            bp = world.item_blueprints.get(getattr(inst, "blueprint_id", ""))
            name = getattr(bp, "name", None) or getattr(inst, "blueprint_id", str(iid))
            return {"id": str(iid), "name": str(name)}
        except Exception:
            return {"id": str(iid), "name": str(iid)}

    def _resolve_npc(nid: str) -> Dict[str, str]:
        try:
            npc = world.npcs.get(nid)
            return {"id": str(nid), "name": getattr(npc, "name", str(nid)) if npc else str(nid)}
        except Exception:
            return {"id": str(nid), "name": str(nid)}

    inventory_resolved = [_resolve_item(i) for i in (inventory or [])]
    equipped_resolved: Dict[str, Optional[Dict[str, str]]] = {}
    try:
        for slot, iid in (equipped or {}).items():
            if iid:
                equipped_resolved[str(slot)] = _resolve_item(iid)
            else:
                equipped_resolved[str(slot)] = None
    except Exception:
        equipped_resolved = {}

    # Primary equipped label preference: main_hand else first non-null
    equipped_primary = None
    try:
        if isinstance(equipped, dict) and equipped.get("main_hand"):
            equipped_primary = _resolve_item(equipped["main_hand"])["name"]
        else:
            for _slot, _iid in (equipped or {}).items():
                if _iid:
                    equipped_primary = _resolve_item(_iid)["name"]
                    break
    except Exception:
        pass

    occupants_resolved = [_resolve_npc(n) for n in (visible_npcs or [])]
    items_resolved = [_resolve_item(i) for i in (visible_items or [])]

    # Get connections
    connections = {}
    if location_state and hasattr(location_state, "connections_state"):
        try:
            connections = dict(getattr(location_state, "connections_state", {}))
        except Exception:
            pass

    # Build response
    state = {
        "game_tick": simulator.game_tick,
        "player": {
            "id": player_id,
            "name": player.name if player else "Unknown",
            "hp": getattr(player, "hp", 0) if player else 0,
            "max_hp": getattr(player, "attributes", {}).get("constitution", 10) * 2 if player else 10,
            "hunger_stage": getattr(player, "hunger_stage", "sated") if player else "sated",
            "inventory": inventory,
            "inventory_resolved": inventory_resolved,
            "equipped": equipped,
            "equipped_resolved": equipped_resolved,
            "equipped_primary_label": equipped_primary,
            "attributes": getattr(player, "attributes", {}) if player else {},
            "skills": getattr(player, "skills", {}) if player else {}
        },
        "location": {
            "id": player_location,
            "name": getattr(location_static, "name", player_location) if location_static else player_location,
            "description": getattr(location_static, "description", "") if location_static else "",
            "occupants": visible_npcs,
            "occupants_resolved": occupants_resolved,
            "items": visible_items,
            "items_resolved": items_resolved,
            "connections": connections
        },
        "world": {
            "locations": list(world.locations_static.keys())
        }
    }
    
    return jsonify(state)

@app.route('/api/locations')
def get_locations():
    """Get all locations with their connections and hex metadata (axial coords, edges).
    Backward compatible: existing fields remain; new fields:
      - hex: {"q": int, "r": int, "orientation": "flat"}
      - edges: {neighbor_id: {"status": "open"|"closed", "direction": "E"|"NE"|"NW"|"W"|"SW"|"SE"}}
    """
    # Compute axial coordinates once per request (small worlds; can be cached later if needed)
    coords = _compute_axial_coordinates(world)

    locations = {}
    for loc_id, loc_static in world.locations_static.items():
        try:
            loc_state = world.get_location_state(loc_id)
            connections = getattr(loc_state, "connections_state", {})
        except Exception:
            connections = {}

        # Derive hex metadata
        q, r = coords.get(loc_id, (0, 0))
        edges = _build_edges_for(loc_id, world)

        locations[loc_id] = {
            "id": loc_id,
            "name": getattr(loc_static, "name", loc_id),
            "description": getattr(loc_static, "description", ""),
            # Existing fields preserved for compatibility
            "connections": connections,
            "hex_connections": getattr(loc_static, "hex_connections", {}),
            # New fields for proper hex rendering and finite-degree constraints
            "hex": {"q": q, "r": r, "orientation": "flat"},
            "edges": edges,
        }

    return jsonify(locations)

@app.route('/api/actors')
def get_actors():
    """Get all actors in the world"""
    actors = []
    for npc_id, npc in world.npcs.items():
        location = world.find_npc_location(npc_id)
        actors.append({
            "id": npc_id,
            "name": npc.name,
            "location": location,
            "type": "player" if npc_id == player_id else "npc",
            "hp": npc.hp,
            "hunger_stage": npc.hunger_stage
        })
    
    return jsonify(actors)

@app.route('/api/parse-command', methods=['POST'])
def parse_command():
    """Parse natural language command using LLM"""
    if not llm_client:
        return jsonify({"error": "LLM client not available"}), 500
    
    data = request.json
    user_input = data.get('command', '')
    
    if not user_input:
        return jsonify({"error": "No command provided"}), 400
    
    try:
        # Get current state for context
        state_response = get_state()
        state_data = state_response.json if hasattr(state_response, 'json') else state_response.get_json()
        
        # Build additional context for the LLM
        player = state_data.get("player", {})
        location = state_data.get("location", {})
        
        additional_context = {
            "player_id": player_id,
            "location_id": location.get("id"),
            "visible_items": location.get("items", []),
            "visible_npcs": location.get("occupants", []),
            "inventory_items": player.get("inventory", []),
            "stats": {
                "hp": player.get("hp"),
                "max_hp": player.get("max_hp"),
                "hunger_stage": player.get("hunger_stage"),
            },
            "time_tick": simulator.game_tick,
        }
        
        # Parse command using LLM
        command = llm_client.parse_command(user_input, SYSTEM_PROMPT, additional_context=additional_context)
        
        return jsonify({"command": command})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/action', methods=['POST'])
def perform_action():
    """Perform a game action"""
    if not simulator:
        return jsonify({"error": "Game not initialized"}), 500
    
    data = request.json
    action = data.get('action', {})
    
    try:
        # Process the command
        simulator.process_command(player_id, action)
        
        # Drain events generated by the player's action
        while simulator.event_queue:
            simulator.tick()
        
        # Run NPC cycle
        any_npc_acted = False
        while simulator.run_npc_round():
            any_npc_acted = True
        if any_npc_acted:
            simulator.tick()
        
        # Ensure time advances until the player is ready again
        while world.get_npc(player_id).next_available_tick > simulator.game_tick:
            simulator.tick()
        
        # Emit updated state to all clients
        socketio.emit('state_update', get_state().json)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    emit('state_update', get_state().json)

# ========== World/Editor API (creator-tool endpoints) ==========

@app.route('/api/world', methods=['GET'])
def api_world_snapshot():
    """Return a compact world snapshot for editor UIs."""
    try:
        # Locations with occupants/items and dynamic connections
        locations = []
        for loc_id, st in world.locations_state.items():
            try:
                stat = world.get_location_static(loc_id)
            except Exception:
                stat = None
            locations.append({
                "id": str(loc_id),
                "description": getattr(stat, "description", ""),
                "occupants": list(getattr(st, "occupants", []) or []),
                "items": list(getattr(st, "items", []) or []),
                "connections": dict(getattr(st, "connections_state", {}) or {}),
            })
        # NPCs with their current location
        npcs = []
        for nid, npc in world.npcs.items():
            npcs.append({
                "id": nid,
                "name": getattr(npc, "name", nid),
                "location_id": world.find_npc_location(nid),
                "hp": getattr(npc, "hp", 0),
            })
        return jsonify({"locations": locations, "npcs": npcs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _emit_refresh():
    try:
        socketio.emit('state_update', get_state().json)
    except Exception:
        pass


# ----- Location endpoints -----
@app.route('/api/locations/create', methods=['POST'])
def api_loc_create():
    data = request.json or {}
    loc = data.get("location_id")
    desc = data.get("description", "")
    if not isinstance(loc, str) or not loc:
        return jsonify({"error": "location_id required"}), 400
    ok = simulator._gm_create_location(loc, str(desc or ""))
    if not ok:
        return jsonify({"error": "failed to create"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/locations/delete', methods=['POST'])
def api_loc_delete():
    data = request.json or {}
    loc = data.get("location_id")
    if not isinstance(loc, str) or not loc:
        return jsonify({"error": "location_id required"}), 400
    ok = simulator._gm_delete_location(loc)
    if not ok:
        return jsonify({"error": "failed to delete (occupied?)"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/locations/connect', methods=['POST'])
def api_loc_connect():
    data = request.json or {}
    a = data.get("a"); b = data.get("b")
    status = str(data.get("status", "open")).lower()
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        return jsonify({"error": "invalid a/b"}), 400
    ok = simulator._gm_connect_locations(a, b, status=status)
    if not ok:
        return jsonify({"error": "failed to connect"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/locations/disconnect', methods=['POST'])
def api_loc_disconnect():
    data = request.json or {}
    a = data.get("a"); b = data.get("b")
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        return jsonify({"error": "invalid a/b"}), 400
    ok = simulator._gm_disconnect_locations(a, b)
    if not ok:
        return jsonify({"error": "failed to disconnect"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/edges/status', methods=['POST'])
def api_edge_status():
    data = request.json or {}
    a = data.get("a"); b = data.get("b")
    status = data.get("status", "open")
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        return jsonify({"error": "invalid a/b"}), 400
    ok = simulator._gm_set_edge_status(a, b, status)
    if not ok:
        return jsonify({"error": "failed to set status"}), 400
    _emit_refresh()
    return jsonify({"success": True})


# ----- NPC endpoints -----
@app.route('/api/npcs/spawn', methods=['POST'])
def api_npc_spawn():
    data = request.json or {}
    name = data.get("name") or None
    loc = data.get("location_id")
    if not isinstance(loc, str) or not loc:
        return jsonify({"error": "location_id required"}), 400
    nid = simulator._gm_spawn_npc(loc)
    if not nid:
        return jsonify({"error": "failed to spawn"}), 400
    if isinstance(name, str) and nid in world.npcs:
        try:
            world.npcs[nid].name = name
        except Exception:
            pass
    _emit_refresh()
    return jsonify({"success": True, "npc_id": nid})

@app.route('/api/npcs/delete', methods=['POST'])
def api_npc_delete():
    data = request.json or {}
    npc_id = data.get("npc_id")
    if not isinstance(npc_id, str) or not npc_id:
        return jsonify({"error": "npc_id required"}), 400
    ok = simulator._gm_delete_npc(npc_id)
    if not ok:
        return jsonify({"error": "failed to delete"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/npcs/move', methods=['POST'])
def api_npc_move():
    data = request.json or {}
    npc_id = data.get("npc_id")
    to = data.get("to_location_id")
    if not isinstance(npc_id, str) or not isinstance(to, str):
        return jsonify({"error": "npc_id and to_location_id required"}), 400
    ok = simulator._gm_move_actor(npc_id, to)
    if not ok:
        return jsonify({"error": "failed to move"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/npcs/memory/add', methods=['POST'])
def api_npc_mem_add():
    data = request.json or {}
    npc_id = data.get("npc_id"); text = data.get("text", "")
    if not isinstance(npc_id, str) or not isinstance(text, str) or not text:
        return jsonify({"error": "npc_id and text required"}), 400
    simulator._gm_add_memory(npc_id, text)
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/npcs/memory/remove', methods=['POST'])
def api_npc_mem_remove():
    data = request.json or {}
    npc_id = data.get("npc_id")
    if not isinstance(npc_id, str):
        return jsonify({"error": "npc_id required"}), 400
    ok = simulator._gm_remove_memory(npc_id)
    if not ok:
        return jsonify({"error": "failed"}), 400
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/npcs/goal/add', methods=['POST'])
def api_npc_goal_add():
    data = request.json or {}
    npc_id = data.get("npc_id"); text = data.get("text", "")
    if not isinstance(npc_id, str) or not isinstance(text, str) or not text:
        return jsonify({"error": "npc_id and text required"}), 400
    simulator._gm_add_goal(npc_id, text)
    _emit_refresh()
    return jsonify({"success": True})

@app.route('/api/npcs/goal/remove', methods=['POST'])
def api_npc_goal_remove():
    data = request.json or {}
    npc_id = data.get("npc_id")
    if not isinstance(npc_id, str):
        return jsonify({"error": "npc_id required"}), 400
    ok = simulator._gm_remove_goal(npc_id)
    if not ok:
        return jsonify({"error": "failed"}), 400
    _emit_refresh()
    return jsonify({"success": True})


# ----- Item endpoints -----
@app.route('/api/items/spawn', methods=['POST'])
def api_item_spawn():
    data = request.json or {}
    loc = data.get("location_id")
    bp = data.get("blueprint_id")
    if not isinstance(loc, str) or not loc:
        return jsonify({"error": "location_id required"}), 400
    # Use GM helper if no explicit blueprint, else create directly
    if not bp:
        iid = simulator._gm_spawn_item(loc)
    else:
        try:
            # Create instance directly mirroring _gm_spawn_item behavior
            idx = 1
            while True:
                cand = f"item_gm_{idx}"
                if cand not in world.item_instances:
                    break
                idx += 1
            iid = cand
            from engine.data_models import ItemInstance, LocationState
            inst = ItemInstance(id=iid, blueprint_id=bp, current_location=loc, owner_id=None)
            world.item_instances[iid] = inst
            if loc not in world.locations_state:
                world.locations_state[loc] = LocationState(id=loc)
            st = world.locations_state[loc]
            if iid not in st.items:
                st.items.append(iid)
        except Exception as e:
            return jsonify({"error": f"failed: {e}"}), 400
    if not iid:
        return jsonify({"error": "failed to spawn"}), 400
    _emit_refresh()
    return jsonify({"success": True, "item_id": iid})

@app.route('/api/items/delete', methods=['POST'])
def api_item_delete():
    data = request.json or {}
    item_id = data.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        return jsonify({"error": "item_id required"}), 400
    ok = simulator._gm_delete_item(item_id)
    if not ok:
        return jsonify({"error": "failed"}), 400
    _emit_refresh()
    return jsonify({"success": True})


# ----- Edge direction endpoint (canonical hex directions: E, NE, NW, W, SW, SE) -----
@app.route('/api/edges/direction', methods=['POST'])
def api_edge_set_direction():
    """
    Set the direction metadata on an edge in BOTH directions using canonical codes:
      E, NE, NW, W, SW, SE
    Creates the edge entries if missing, preserves open/closed status if present.
    """
    data = request.json or {}
    a = data.get("a"); b = data.get("b"); d = data.get("direction")
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        return jsonify({"error": "invalid a/b"}), 400
    if d not in {"E","NE","NW","W","SW","SE"}:
        return jsonify({"error": "direction must be one of E,NE,NW,W,SW,SE"}), 400
    try:
        st_a = world.locations_state.get(a)
        st_b = world.locations_state.get(b)
        if st_a is None or st_b is None:
            return jsonify({"error": "unknown locations"}), 400
        ent_a = st_a.connections_state.setdefault(b, {})
        ent_b = st_b.connections_state.setdefault(a, {})
        ent_a["status"] = ent_a.get("status", "open")
        ent_b["status"] = ent_b.get("status", "open")
        inverse = {"E":"W","W":"E","NE":"SW","SW":"NE","NW":"SE","SE":"NW"}
        ent_a["direction"] = d
        ent_b["direction"] = inverse[d]
        _emit_refresh()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/world/reset_hexgrid', methods=['POST'])
def api_world_reset_hexgrid():
    """
    Hard reset the top-level world into a canonical flat-top hex cross of four locations
    with consistent static hex_connections and dynamic edges with directions.

    Layout (axial, flat-top):
        tavern         = town_square + NE
        market_square  = town_square + SE
        alley          = town_square + SW

    - Creates missing locations
    - Moves any NPCs from deleted/unknown locations into town_square
    - Rewrites static.hex_connections and dynamic connections_state with open status and directions
    - Attempts to delete any other locations
    - Persists updated JSON back to data/locations/* files
    """
    try:
        keep_ids = ["town_square", "tavern", "market_square", "alley"]
        # Ensure base locations exist (create if missing)
        for loc in keep_ids:
            if loc not in world.locations_state:
                simulator._gm_create_location(loc, description=f"{loc.replace('_',' ').title()}")

        # Move actors in unknown/extra locations to town_square, then delete extras
        hub = "town_square"
        for loc_id in list(world.locations_state.keys()):
            if loc_id in keep_ids:
                continue
            st = world.locations_state.get(loc_id)
            if not st:
                continue
            # Move occupants to hub
            for npc_id in list(getattr(st, "occupants", []) or []):
                try:
                    simulator._gm_move_actor(npc_id, hub)
                except Exception:
                    pass
            # Attempt delete
            try:
                simulator._gm_delete_location(loc_id)
            except Exception:
                pass

        # Canonical static hex layout from hub
        layout = {
            "town_square": {"NE": "tavern", "SE": "market_square", "SW": "alley"},
            "tavern":      {"SW": "town_square"},
            "market_square": {"NW": "town_square"},
            "alley":       {"NE": "town_square"},
        }

        # Normalize tags helper
        def ensure(loc_id):
            try:
                ls = world.locations_static.get(loc_id)
                if ls is None:
                    from engine.data_models import LocationStatic
                    world.locations_static[loc_id] = LocationStatic(id=loc_id, description=f"{loc_id.replace('_',' ').title()}")
            except Exception:
                pass

        for loc_id in keep_ids:
            ensure(loc_id)

        # Apply static hex_connections and dynamic connections with directions
        inv = {"E":"W","W":"E","NE":"SW","SW":"NE","NW":"SE","SE":"NW"}
        for a, conns in layout.items():
            try:
                st = world.locations_static.get(a)
                if st:
                    st.hex_connections = {k: v for k, v in conns.items()}
            except Exception:
                pass
            # ensure dynamic entries
            try:
                st_a = world.locations_state.get(a)
                if st_a is None:
                    from engine.data_models import LocationState
                    world.locations_state[a] = LocationState(id=a)
                    st_a = world.locations_state[a]
                for d, b in conns.items():
                    # forward
                    ent_a = st_a.connections_state.setdefault(b, {})
                    ent_a["status"] = ent_a.get("status", "open")
                    ent_a["direction"] = d
                    # back
                    st_b = world.locations_state.get(b)
                    if st_b is None:
                        from engine.data_models import LocationState
                        world.locations_state[b] = LocationState(id=b)
                        st_b = world.locations_state[b]
                    ent_b = st_b.connections_state.setdefault(a, {})
                    ent_b["status"] = ent_b.get("status", "open")
                    ent_b["direction"] = inv[d]
            except Exception:
                pass

        # Persist to JSON files on disk
        try:
            import json as _json
            base = Path("data") / "locations"
            base.mkdir(parents=True, exist_ok=True)
            # Write keepers
            for loc_id in keep_ids:
                ls = world.locations_static.get(loc_id)
                st = world.locations_state.get(loc_id)
                if ls:
                    static_path = base / f"{loc_id}_static.json"
                    with open(static_path, "w", encoding="utf-8") as f:
                        _json.dump({
                            "id": ls.id,
                            "description": getattr(ls, "description", ""),
                            "tags": getattr(ls, "tags", {"inherent": []}),
                            "hex_connections": getattr(ls, "hex_connections", {}),
                        }, f, ensure_ascii=False, indent=2)
                if st:
                    state_path = base / f"{loc_id}_state.json"
                    with open(state_path, "w", encoding="utf-8") as f:
                        _json.dump({
                            "id": st.id,
                            "occupants": list(getattr(st, "occupants", []) or []),
                            "items": list(getattr(st, "items", []) or []),
                            "sublocations": list(getattr(st, "sublocations", []) or []),
                            "transient_effects": list(getattr(st, "transient_effects", []) or []),
                            "connections_state": getattr(st, "connections_state", {}) or {},
                        }, f, ensure_ascii=False, indent=2)
        except Exception:
            # Persistence failure is non-fatal for runtime view
            pass

        _emit_refresh()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Create static directory if it doesn't exist
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    if not os.path.exists(static_dir):
        os.makedirs(static_dir)
    
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)