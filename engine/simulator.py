from __future__ import annotations

from typing import Dict, Any, List, Optional, Protocol, Tuple
import random
import json
import math

from .world_state import WorldState
from .events import Event, make_perception_from_event
from .data_models import NPC, PerceptionEvent
import json as _json_for_cfg  # local alias to avoid shadowing
from .tools.base import Tool
from .narrator import Narrator
from rpg import combat_rules
from .llm_client import LLMClient
# Optional UI renderer is injected externally; no import here to keep engine headless by default.

class RendererProtocol(Protocol):
    def set_board(self, top_locations: List[str], sublocations_map: Dict[str, List[str]]) -> None: ...
    def update_state(self, actors: List[Dict[str, Any]], messages: Dict[str, Any]) -> None: ...
    def run_once(self) -> Optional[Tuple[str, Any]]: ...
    def shutdown(self) -> None: ...


class Simulator:
    # NOTE: Backwards-compat alias kept for compatibility. Prefer run_one_npc_turn().
    def run_npc_round(self) -> bool:
        """Deprecated alias. Use run_one_npc_turn()."""
        return self.run_one_npc_turn()

    def run_one_npc_turn(self) -> bool:
        """Execute exactly one NPC (blocking on LLM). Do NOT advance time here.
        Returns True if an NPC acted; False if the cycle completed (no NPC acted)."""
        try:
            from .npc_planner import NPCPlanner
            planner = NPCPlanner(getattr(self, "llm", None))
        except Exception as e:
            try:
                if not getattr(self, "_planner_import_failed_logged", False):
                    print(f"[Simulator] NPCPlanner import failed; NPCs will not act via LLM: {e}")
                    self._planner_import_failed_logged = True
            except Exception:
                pass
            planner = None

        world = self.world
        player_id = getattr(self, "player_id", None)

        # Initialize or refresh turn order at cycle boundaries
        if not self.npc_turn_order or self.current_npc_index >= len(self.npc_turn_order):
            self.npc_turn_order = sorted([nid for nid in getattr(world, "npcs", {}).keys() if nid != player_id])
            self.current_npc_index = 0
            if not self.npc_turn_order:
                return False  # No NPCs at all

        # Find the next eligible NPC for this single step
        while self.current_npc_index < len(self.npc_turn_order):
            nid = self.npc_turn_order[self.current_npc_index]
            self.current_npc_index += 1

            npc = world.npcs.get(nid)
            if not npc:
                continue
            # Skip dead NPCs
            if "dead" in getattr(npc, "tags", {}).get("dynamic", []):
                continue
            # If actor is busy, skip this tick for that NPC (time will advance after an action below)
            if getattr(npc, "next_available_tick", 0) > self.game_tick:
                continue

            # Build compact context for planner (use world to resolve actual location/state safely)
            loc_id = self.world.find_npc_location(nid)
            location_static = None
            location_state = None
            visible_npcs = []
            visible_items = []
            neighbors = []
            if loc_id:
                try:
                    location_static = self.world.get_location_static(loc_id)
                except Exception:
                    location_static = None
                try:
                    location_state = self.world.get_location_state(loc_id)
                except Exception:
                    location_state = None
                if location_state:
                    try:
                        # exclude self from visibility list
                        visible_npcs = [x for x in (getattr(location_state, "occupants", []) or []) if x != nid]
                    except Exception:
                        visible_npcs = []
                    try:
                        visible_items = list(getattr(location_state, "items", []) or [])
                    except Exception:
                        visible_items = []
                    try:
                        neighbors = list((getattr(location_state, "connections_state", {}) or {}).keys())
                    except Exception:
                        neighbors = []

            persona = {
                "id": getattr(npc, "id", nid),
                "name": getattr(npc, "name", nid),
                "hp": getattr(npc, "hp", None),
                "attributes": getattr(npc, "attributes", {}),
                "skills": getattr(npc, "skills", {}),
                "tags": getattr(npc, "tags", {}),
                "short_term_memory": getattr(npc, "short_term_memory", []),
                # Expose LTM and core memories/goals so planner and LLM can use them directly.
                "memories": getattr(npc, "memories", []),
                "core_memories": getattr(npc, "core_memories", []),
                "goals": getattr(npc, "goals", []),
            }
            # Build live conversation snapshot for this actor from Simulator state
            convo_snapshot = None
            try:
                convo_id = self.actor_conversation.get(nid)
                if convo_id and convo_id in self.conversations:
                    c = self.conversations[convo_id]
                    convo_snapshot = {
                        "conversation_id": c.get("conversation_id"),
                        "participants": c.get("participants", []),
                        "current_speaker": c.get("current_speaker"),
                        "turn_order": c.get("turn_order", []),
                        "last_interaction_tick": c.get("last_interaction_tick"),
                    }
            except Exception:
                convo_snapshot = None

            ctx = {
                "game_tick": getattr(world, "game_tick", 0),
                "actor": persona,
                "location": {
                    "id": loc_id,
                    "static": {
                        "name": (getattr(location_static, "name", None) if location_static is not None else None) or (getattr(location_static, "id", None) if location_static is not None else None),
                        "description": getattr(location_static, "description", "") if location_static is not None else "",
                    },
                    "neighbors": neighbors,
                    "connections_state": getattr(location_state, "connections_state", {}) if location_state is not None else {},
                    "occupants": visible_npcs,
                    "items": visible_items,
                },
                "available_tools": list(self.tools.keys()),
                "recent_memories": getattr(world, "recent_memories", []),
                "conversation": convo_snapshot,
            }

            # Blocking LLM call for exactly one NPC
            action = None
            if planner is not None:
                try:
                    action = planner.plan(ctx)
                    # If available, log hidden reasoning from last LLM response to run log (non-fatal)
                    try:
                        from .llm_client import LLMClient as _LLM
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
                                print(f"[LLM think] npc_plan {nid}: {think}")
                    except Exception:
                        pass
                except Exception as e:
                    print("[NPCPlanner] Error planning for", nid, ":", e)

            if isinstance(action, dict) and "tool" in action:
                # Runtime guard: block conversation speech when it's not the actor's turn
                try:
                    tool_name = action.get("tool")
                    if ctx.get("conversation") and ctx["conversation"].get("current_speaker") != nid:
                        if tool_name in {"talk"}:
                            # Convert blocked speech into a visible wait action so a bubble appears.
                            action = {"tool": "wait", "params": {"ticks": 1}}
                except Exception:
                    pass

                if action:
                    try:
                        self.process_command(nid, action)
                    except Exception as e:
                        print("[Simulator] Failed to execute NPC action for", nid, ":", e)

                # Drain events produced during this NPC action synchronously without advancing time.
                # Do NOT push renderer state on each individual event; we push once per tick in tick().
                try:
                    while getattr(self, "event_queue", []):
                        ready_events = [e for e in self.event_queue if e.tick <= self.game_tick]
                        if not ready_events:
                            break
                        self.event_queue = [e for e in self.event_queue if e.tick > self.game_tick]
                        for evt in ready_events:
                            self.handle_event(evt)
                except Exception:
                    pass

                # Do not advance time here; advance once after the full NPC cycle completes
                return True  # One NPC acted; stop here to avoid overload

            # If planner returned no action or actor was blocked, continue scanning this cycle

        # If we reached here, we exhausted the list; reset to start a new cycle next time
        self.current_npc_index = 0
        return False
    def __init__(
        self,
        world: WorldState,
        narrator: Optional[Narrator] = None,
        player_id: Optional[str] = None,
    ):
        self.world = world
        # Optional external renderer adapter with a simple interface:
        #  - set_board(top_locations, sublocations_map)
        #  - update_state(actors, messages)
        #  - run_once() -> ("enter", loc) | ("back", None) | ("noop", None)
        #  - shutdown()
        self.renderer: Optional[RendererProtocol] = None
        self.game_tick = 0
        self.event_queue: List[Event] = []
        self.tools: Dict[str, Tool] = {}
        self.narrator = narrator or Narrator(world)
        self.player_id = player_id
        self.starvation_enabled = True
        self.llm: Optional[LLMClient] = None  # Initialized lazily on first use

        # Memory config knobs with runtime overrides from config/llm.json if present
        self.perception_buffer_size = 30
        self.retrieval_top_k = 6
        try:
            # Lazy read config; avoid hard dependency on path by asking world (if it exposes), else project default
            import os
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "llm.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = _json_for_cfg.load(f)
                mem = (cfg or {}).get("memory") or {}
                self.perception_buffer_size = int(mem.get("perception_buffer_size", self.perception_buffer_size))
                self.retrieval_top_k = int(mem.get("retrieval_top_k", self.retrieval_top_k))
        except Exception:
            pass
         
        # Turn tracking state
        self.current_npc_index = 0
        self.npc_turn_order = []

        # UI state
        self._last_actor_msgs: Dict[str, str] = {}
        self._ui_focus_location: Optional[str] = None
        # Internal meta payload for renderer (non-actor keys)
        self._ui_meta: Dict[str, Any] = {}

        # In-memory conversation state
        # conversations: {conversation_id: {participants, turn_order, current_speaker, start_tick, last_interaction_tick, history: [{speaker, tick, content}], location_id}}
        self.conversations: Dict[str, Dict[str, Any]] = {}
        # Map actor -> conversation_id (only one active conversation per actor for now)
        self.actor_conversation: Dict[str, str] = {}
        # Internal flags
        self._planner_import_failed_logged = False

        # Event dispatch table replacing long if/elif chain in handle_event
        self.event_handlers = {
            "describe_location": self._handle_describe_location,
            "move": self._handle_move,
            "grab": self._handle_grab,
            "drop": self._handle_drop,
            "eat": self._handle_eat,
            "attack_attempt": self._handle_attack_attempt,
            "attack_hit": self._handle_attack_hit,
            "attack_missed": self._handle_attack_missed,
            "damage_applied": self._handle_damage_applied,
            "talk": self._handle_talk,
            "talk_loud": self._handle_talk_loud,
            "scream": self._handle_scream,
            "inventory": self._handle_inventory,
            "stats": self._handle_stats,
            "equip": self._handle_equip,
            "unequip": self._handle_unequip,
            "analyze": self._handle_analyze,
            "give": self._handle_give,
            "toggle_starvation": self._handle_toggle_starvation,
            "open_connection": self._handle_open_close_connection,
            "close_connection": self._handle_open_close_connection,
            "npc_died": self._handle_npc_died,
            "wait": self._handle_wait,
            "rest": self._handle_rest,
            "leave_conversation": self._handle_leave_conversation,
        }

    def register_tool(self, tool: Tool):
        self.tools[tool.name] = tool

    def process_command(self, actor_id: str, command: Dict[str, Any]):
        tool_name = command.get("tool")
        tool = self.tools.get(tool_name)
        actor = self.world.get_npc(actor_id)
        if not tool:
            raise ValueError(f"Unknown tool {command['tool']}")
        if actor.next_available_tick > self.game_tick:
            raise ValueError("Actor is busy")
        params = command.get("params", {}) if isinstance(command.get("params"), dict) else {}
        # Normalize common param aliases to the canonical schema expected by tools
        try:
            if isinstance(tool_name, str):
                t = tool_name
                if t in {"move", "open", "close"}:
                    loc = params.get("target_location") or params.get("location_id") or params.get("target") or params.get("to")
                    if isinstance(loc, str) and loc:
                        params["target_location"] = loc
                if t == "attack":
                    tgt = params.get("target_id")
                    if not isinstance(tgt, str):
                        if isinstance(params.get("target"), str):
                            params["target_id"] = params["target"]
                        elif isinstance(params.get("target_ids"), list) and params["target_ids"]:
                            first = params["target_ids"][0]
                            if isinstance(first, str):
                                params["target_id"] = first
                if t in {"talk", "talk_loud", "scream"}:
                    content = params.get("content")
                    params["content"] = content[:200] if isinstance(content, str) else "..."
                if t == "give":
                    recip = params.get("recipient_id") or params.get("target_id")
                    if isinstance(recip, str):
                        params["target_id"] = recip
                if t in {"equip", "unequip"}:
                    slot = params.get("slot") or params.get("equipment_slot")
                    if isinstance(slot, str):
                        params["slot"] = slot
        except Exception:
            pass
        if not tool.validate_intent(params, self.world, actor):
            raise ValueError("Invalid intent")

        # Compute time cost per command to avoid shared Tool state issues.
        time_cost = getattr(tool, "time_cost", 1)
        if getattr(tool, "name", "") in {"wait", "rest"}:
            try:
                time_cost = max(1, int(params.get("ticks", 1)))
            except Exception:
                time_cost = 1

        events = tool.generate_events(params, self.world, actor, self.game_tick)
        self.event_queue.extend(events)
        actor.next_available_tick = self.game_tick + time_cost

    def npc_think(self, npc: NPC) -> Optional[Dict[str, Any]]:
        """Deprecated: Use NPCPlanner.plan via run_npc_round. Retained for compatibility."""
        try:
            from .npc_planner import NPCPlanner
            planner = NPCPlanner(getattr(self, "llm", None))
        except Exception:
            return None

        # Build a minimal context similar to run_npc_round for this NPC
        current_loc = self.world.find_npc_location(npc.id)
        if not current_loc:
            return None
        loc_state = self.world.get_location_state(current_loc)
        loc_static = self.world.get_location_static(current_loc)
        neighbors = list(loc_state.connections_state.keys())
        occupants = [oid for oid in loc_state.occupants if oid != npc.id]
        items_here = list(loc_state.items)

        persona = {
            "id": npc.id,
            "name": npc.name,
            "hp": npc.hp,
            "attributes": getattr(npc, "attributes", {}),
            "skills": getattr(npc, "skills", {}),
            "tags": npc.tags,
            "short_term_memory": getattr(npc, "short_term_memory", []),
            "memories": getattr(npc, "memories", []),
            "core_memories": getattr(npc, "core_memories", []),
            "goals": getattr(npc, "goals", []),
        }

        # Conversation snapshot from simulator state
        convo_snapshot = None
        try:
            convo_id = self.actor_conversation.get(npc.id)
            if convo_id and convo_id in self.conversations:
                c = self.conversations[convo_id]
                convo_snapshot = {
                    "conversation_id": c.get("conversation_id"),
                    "participants": c.get("participants", []),
                    "current_speaker": c.get("current_speaker"),
                    "turn_order": c.get("turn_order", []),
                    "last_interaction_tick": c.get("last_interaction_tick"),
                }
        except Exception:
            pass

        ctx = {
            "game_tick": getattr(self.world, "game_tick", 0),
            "actor": persona,
            "location": {
                "id": current_loc,
                "static": {
                    "name": loc_static.name if hasattr(loc_static, "name") else getattr(loc_static, "description", ""),
                    "description": getattr(loc_static, "description", ""),
                },
                "neighbors": neighbors,
                "connections_state": getattr(loc_state, "connections_state", {}),
                "occupants": occupants,
                "items": items_here,
            },
            "available_tools": list(self.tools.keys()),
            "recent_memories": getattr(self.world, "recent_memories", []),
            "conversation": convo_snapshot,
        }

        action = planner.plan(ctx)
        return action

    def tick(self):
        """
        Advance global time by one tick, apply world passive effects (e.g. hunger),
        and process any events scheduled for this tick. This does NOT make NPCs
        choose actions; NPC actions are executed explicitly via run_npc_round()
        between player turns.
        """
        self.game_tick += 1
        if self.starvation_enabled:
            hunger_events = self.world.update_hunger(self.game_tick)
            self.event_queue.extend(hunger_events)

        # Drain only events whose tick <= current
        ready_events = [e for e in self.event_queue if e.tick <= self.game_tick]
        self.event_queue = [e for e in self.event_queue if e.tick > self.game_tick]
        for event in ready_events:
            self.handle_event(event)
        # After all events for this tick have been handled and actor bubbles recorded, update the renderer once.
        self._renderer_push_state()

    def set_renderer(self, renderer_adapter: Any):
        """Attach a renderer adapter (pygame-based UI)."""
        # Allow Any for call sites, but store as Protocol-typed
        self.renderer = renderer_adapter  # type: ignore[assignment]
        try:
            # Build initial board from known locations and sublocations (simple: none for now)
            top_locations = list(self.world.locations_static.keys())
            # Build sublocations map from dynamic state (LocationState.sublocations)
            sub_map: Dict[str, List[str]] = {}
            for loc_id in top_locations:
                try:
                    loc_state = self.world.get_location_state(loc_id)
                    subs = getattr(loc_state, "sublocations", []) or []
                    sub_map[loc_id] = [str(s) for s in subs]
                except Exception:
                    sub_map[loc_id] = []
            if hasattr(self.renderer, "set_board"):
                self.renderer.set_board(top_locations, sub_map)  # type: ignore[call-arg]

            # Seed initial connections_state snapshot for UI
            try:
                # Dynamic connection status snapshot (directional)
                snapshot: Dict[str, Dict[str, Any]] = {}
                for loc_id, loc_state in self.world.locations_state.items():
                    cs = getattr(loc_state, "connections_state", {}) or {}
                    snap_entry: Dict[str, Any] = {}
                    for nid, meta in cs.items():
                        status = (meta or {}).get("status", "open")
                        snap_entry[str(nid)] = {"status": status}
                    snapshot[str(loc_id)] = snap_entry
                self._ui_meta["__connections_state__"] = snapshot
            except Exception:
                self._ui_meta["__connections_state__"] = {}

            # Deprecated static adjacency snapshot removed: layout now derives from dynamic connections_state.
            try:
                # Clean any legacy key if present
                if "__static_neighbors__" in self._ui_meta:
                    self._ui_meta.pop("__static_neighbors__", None)
            except Exception:
                pass

            # Track layout signature for dynamic world changes
            try:
                self._ui_meta["__layout_signature__"] = {
                    "tops": sorted([str(x) for x in self.world.locations_static.keys()]),
                    "subs": {str(k): list(map(str, getattr(self.world.get_location_state(k), "sublocations", []) or []))
                             for k in self.world.locations_static.keys()},
                }
            except Exception:
                self._ui_meta["__layout_signature__"] = {}

            # Push initial state
            self._renderer_push_state()
        except Exception:
            pass

    def _compact_actor_list(self) -> List[Dict[str, Any]]:
        """Build minimal list of actors with types and location labels for UI."""
        actors: List[Dict[str, Any]] = []
        for npc_id, npc in self.world.npcs.items():
            loc_id = self.world.find_npc_location(npc_id)
            if not loc_id:
                continue
            a_type = "player" if npc_id == self.player_id else "npc"
            # Simple enemy detection by tag
            if "enemy" in npc.tags.get("static", []) or "enemy" in npc.tags.get("dynamic", []):
                a_type = "enemy"
            # If the location state carries a chosen sublocation for this npc (optional future), read it; else None
            subloc = None
            try:
                st = self.world.get_location_state(loc_id)
                # Optional: if world stores an assignment map like occupants_by_subloc, we could read it here
                # For now keep None; clicking into sublocation view will just show empty hexes until populated.
            except Exception:
                pass
            actors.append({
                "id": npc_id,
                "name": npc.name,
                "type": a_type,
                "location": loc_id,
                "sublocation": subloc,
            })
        return actors


    # ---------- God Mode helpers (UI-driven dev tools, no LLM involved) ----------

    def _build_inspector_for_actor(self, npc_id: str) -> Dict[str, Any]:
        try:
            npc = self.world.get_npc(npc_id)
        except Exception:
            return {"type": "actor", "actor": {"id": npc_id, "name": npc_id}}
        # Inventory with resolved names
        inv_entries: List[Dict[str, Any]] = []
        for item_id in list(getattr(npc, "inventory", []) or []):
            try:
                inst = self.world.get_item_instance(item_id)
                bp = self.world.get_item_blueprint(inst.blueprint_id)
                inv_entries.append({"id": item_id, "name": getattr(bp, "name", inst.blueprint_id)})
            except Exception:
                inv_entries.append({"id": item_id, "name": item_id})
        # Memories (light)
        mem_entries = []
        try:
            for m in getattr(npc, "memories", [])[:20]:
                try:
                    mem_entries.append({"text": getattr(m, "text", ""), "status": getattr(m, "status", "active")})
                except Exception:
                    mem_entries.append({"text": str(m), "status": "active"})
        except Exception:
            pass
        # Goals (light)
        goal_entries = []
        try:
            for g in getattr(npc, "goals", [])[:20]:
                try:
                    goal_entries.append({"text": getattr(g, "text", ""), "status": getattr(g, "status", "active")})
                except Exception:
                    goal_entries.append({"text": str(g), "status": "active"})
        except Exception:
            pass
        actor_payload = {
            "id": npc.id,
            "name": npc.name,
            "hp": getattr(npc, "hp", 0),
            "hunger_stage": getattr(npc, "hunger_stage", ""),
            "attributes": getattr(npc, "attributes", {}),
        }
        return {
            "type": "actor",
            "actor": actor_payload,
            "inventory": inv_entries,
            "goals": goal_entries,
            "memories": mem_entries,
        }

    def _build_inspector_for_location(self, loc_id: str) -> Dict[str, Any]:
        try:
            ls = self.world.get_location_static(loc_id)
            st = self.world.get_location_state(loc_id)
        except Exception:
            return {"type": "location", "location": {"id": loc_id, "name": loc_id, "description": ""}, "state": {}}
        # LocationStatic doesn't have 'name' field by schema; use id as name, description as detail.
        loc_payload = {
            "id": getattr(ls, "id", loc_id),
            "name": getattr(ls, "id", loc_id),
            "description": getattr(ls, "description", ""),
        }
        state_payload = {
            "occupants": list(getattr(st, "occupants", []) or []),
            "items": list(getattr(st, "items", []) or []),
        }
        return {"type": "location", "location": loc_payload, "state": state_payload}

    def _gm_add_memory(self, npc_id: str, text: str) -> None:
        """Deterministically add a memory via the 'reason' event handler without perception/noise."""
        try:
            evt = Event(
                event_type="reason",
                tick=self.game_tick,
                actor_id=npc_id,
                target_ids=[],
                payload={"desired_outcome": {"add_memory": {"text": str(text)[:1000]}}},
            )
            # Use world.apply_event directly to avoid narration/perception side-effects for GM ops
            self.world.apply_event(evt)
        except Exception as e:
            try:
                print(f"[GM] add_memory failed: {e}")
            except Exception:
                pass
    

    def _gm_add_goal(self, npc_id: str, text: str) -> None:
        try:
            evt = Event(
                event_type="reason",
                tick=self.game_tick,
                actor_id=npc_id,
                target_ids=[],
                payload={"desired_outcome": {"add_goal": {"text": str(text)[:500]}}},
            )
            self.world.apply_event(evt)
        except Exception as e:
            try:
                print(f"[GM] add_goal failed: {e}")
            except Exception:
                pass

    def _gm_spawn_npc(self, location_id: str) -> Optional[str]:
        """Create a simple NPC and place at location_id."""
        try:
            # Generate unique id
            idx = 1
            while True:
                cand = f"npc_gm_{idx}"
                if cand not in self.world.npcs:
                    break
                idx += 1
            nid = cand
            from .data_models import NPC  # local import to avoid cycles at import time
            npc = NPC(
                id=nid,
                name=f"GM NPC {idx}",
                hp=10,
            )
            self.world.npcs[nid] = npc
            # Place in location occupants
            try:
                st = self.world.get_location_state(location_id)
            except Exception:
                # Create minimal LocationState if missing (defensive)
                from .data_models import LocationState
                self.world.locations_state[location_id] = LocationState(id=location_id)
                st = self.world.locations_state[location_id]
            if nid not in st.occupants:
                st.occupants.append(nid)
            return nid
        except Exception as e:
            try:
                print(f"[GM] spawn_npc failed: {e}")
            except Exception:
                pass
            return None

    def _gm_spawn_item(self, location_id: str) -> Optional[str]:
        """Spawn an item instance in the given location. Prefer 'rock' blueprint if available."""
        try:
            # Choose blueprint
            bp_id = None
            if "rock" in self.world.item_blueprints:
                bp_id = "rock"
            elif self.world.item_blueprints:
                bp_id = next(iter(self.world.item_blueprints.keys()))
            else:
                print("[GM] No item blueprints available; cannot spawn item.")
                return None
            # Generate unique instance id
            idx = 1
            while True:
                cand = f"item_gm_{idx}"
                if cand not in self.world.item_instances:
                    break
                idx += 1
            iid = cand
            from .data_models import ItemInstance
            inst = ItemInstance(id=iid, blueprint_id=bp_id, current_location=location_id, owner_id=None)
            self.world.item_instances[iid] = inst
            # Attach to location state
            try:
                st = self.world.get_location_state(location_id)
            except Exception:
                from .data_models import LocationState
                self.world.locations_state[location_id] = LocationState(id=location_id)
                st = self.world.locations_state[location_id]
            if iid not in st.items:
                st.items.append(iid)
            return iid
        except Exception as e:
            try:
                print(f"[GM] spawn_item failed: {e}")
            except Exception:
                pass
            return None

    def _gm_move_actor(self, npc_id: str, to_location_id: str) -> bool:
        """Deterministically move an actor to a target location without narration/perception."""
        try:
            if npc_id not in self.world.npcs:
                return False
            # Ensure target location exists
            if to_location_id not in self.world.locations_state:
                # Create minimal LocationState if missing
                from .data_models import LocationState
                self.world.locations_state[to_location_id] = LocationState(id=to_location_id)
            # Remove from current
            cur = self.world.find_npc_location(npc_id)
            if cur and npc_id in self.world.locations_state[cur].occupants:
                try:
                    self.world.locations_state[cur].occupants.remove(npc_id)
                except ValueError:
                    pass
            # Add to target
            st = self.world.get_location_state(to_location_id)
            if npc_id not in st.occupants:
                st.occupants.append(npc_id)
            return True
        except Exception:
            return False

    def _gm_delete_npc(self, npc_id: str) -> bool:
        """Remove an NPC from world; drop inventory/equipped items to their location."""
        try:
            npc = self.world.npcs.get(npc_id)
            if not npc:
                return False
            # Leave any active conversation
            try:
                self._leave_conversation(npc_id)
            except Exception:
                pass
            # Determine current location
            loc_id = self.world.find_npc_location(npc_id)
            # Drop inventory and equipped items at location (if any)
            if loc_id:
                try:
                    st = self.world.get_location_state(loc_id)
                except Exception:
                    st = None
                if st:
                    # Items to drop: inventory + equipped
                    all_items = list(getattr(npc, "inventory", []) or [])
                    try:
                        for slot, item_id in (getattr(npc, "slots", {}) or {}).items():
                            if item_id:
                                all_items.append(item_id)
                                npc.slots[slot] = None
                    except Exception:
                        pass
                    for item_id in all_items:
                        try:
                            if item_id not in st.items:
                                st.items.append(item_id)
                            inst = self.world.item_instances.get(item_id)
                            if inst:
                                inst.owner_id = None
                                inst.current_location = loc_id
                        except Exception:
                            pass
                    # Clear inventory
                    try:
                        npc.inventory.clear()
                    except Exception:
                        pass
                    # Remove from occupants
                    try:
                        if npc_id in st.occupants:
                            st.occupants.remove(npc_id)
                    except Exception:
                        pass
            # Remove cached UI message
            try:
                self._last_actor_msgs.pop(npc_id, None)
            except Exception:
                pass
            # Finally delete NPC from world
            self.world.npcs.pop(npc_id, None)
            return True
        except Exception:
            return False

    def _gm_delete_item(self, item_id: str) -> bool:
        """Delete an item instance from the world, removing references from owner/location."""
        try:
            inst = self.world.item_instances.get(item_id)
            if not inst:
                return False
            # Remove from owner inventory/slots
            if inst.owner_id and inst.owner_id in self.world.npcs:
                try:
                    owner = self.world.npcs[inst.owner_id]
                    if item_id in owner.inventory:
                        owner.inventory.remove(item_id)
                    # If equipped in any slot, unequip
                    for slot, eq in list(owner.slots.items()):
                        if eq == item_id:
                            owner.slots[slot] = None
                except Exception:
                    pass
            # Remove from location items
            if inst.current_location and inst.current_location in self.world.locations_state:
                try:
                    st = self.world.locations_state[inst.current_location]
                    if item_id in st.items:
                        st.items.remove(item_id)
                except Exception:
                    pass
            # Remove instance
            self.world.item_instances.pop(item_id, None)
            return True
        except Exception:
            return False

    def _gm_create_location(self, location_id: str, description: str = "") -> bool:
        """Create a new empty location with minimal static/state entries."""
        try:
            if not isinstance(location_id, str) or not location_id:
                return False
            if location_id in self.world.locations_state or location_id in self.world.locations_static:
                return False
            from .data_models import LocationStatic, LocationState
            self.world.locations_static[location_id] = LocationStatic(id=location_id, description=str(description or ""))
            self.world.locations_state[location_id] = LocationState(id=location_id)
            return True
        except Exception:
            return False

    def _gm_delete_location(self, location_id: str) -> bool:
        """Delete a location if it has no occupants. Removes connections and detaches items."""
        try:
            st = self.world.locations_state.get(location_id)
            if st and st.occupants:
                # Refuse to delete if occupants present
                return False
            # Remove connections pointing to or from this location
            try:
                if st:
                    for nb in list((st.connections_state or {}).keys()):
                        try:
                            other = self.world.locations_state.get(nb)
                            if other and location_id in other.connections_state:
                                other.connections_state.pop(location_id, None)
                        except Exception:
                            pass
                    st.connections_state.clear()
            except Exception:
                pass
            # Remove reverse references from any other location states (defensive sweep)
            try:
                for loc, s in self.world.locations_state.items():
                    if loc == location_id:
                        continue
                    if location_id in (s.connections_state or {}):
                        s.connections_state.pop(location_id, None)
                    # Remove from any sublocation lists
                    try:
                        if location_id in (s.sublocations or []):
                            s.sublocations = [x for x in s.sublocations if x != location_id]
                    except Exception:
                        pass
            except Exception:
                pass
            # Detach items (keep instances, just remove from location)
            try:
                if st:
                    for item_id in list(st.items or []):
                        try:
                            st.items.remove(item_id)
                            inst = self.world.item_instances.get(item_id)
                            if inst:
                                inst.current_location = None
                        except Exception:
                            pass
            except Exception:
                pass
            # Remove static/state entries
            self.world.locations_state.pop(location_id, None)
            self.world.locations_static.pop(location_id, None)
            return True
        except Exception:
            return False

    def _gm_connect_locations(self, a: str, b: str, status: str = "open") -> bool:
        """Ensure an undirected connection exists between locations a and b."""
        try:
            if a == b:
                return False
            if a not in self.world.locations_state or b not in self.world.locations_state:
                return False
            st_a = self.world.locations_state[a]
            st_b = self.world.locations_state[b]
            ent_a = st_a.connections_state.setdefault(b, {})
            ent_b = st_b.connections_state.setdefault(a, {})
            ent_a["status"] = "open" if status != "closed" else "closed"
            ent_b["status"] = "open" if status != "closed" else "closed"
            # Attempt to infer directions from static if unknown
            try:
                if "direction" not in ent_a:
                    stat_a = self.world.locations_static.get(a)
                    if stat_a:
                        for d, nb in (getattr(stat_a, "hex_connections", {}) or {}).items():
                            if nb == b:
                                ent_a["direction"] = d
                                break
                if "direction" not in ent_b and "direction" in ent_a:
                    from .world_state import HEX_DIR_INVERSE
                    inv = HEX_DIR_INVERSE.get(ent_a.get("direction"))
                    if inv:
                        ent_b["direction"] = inv
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _gm_disconnect_locations(self, a: str, b: str) -> bool:
        """Remove any connection entries between locations a and b."""
        try:
            if a in self.world.locations_state:
                self.world.locations_state[a].connections_state.pop(b, None)
            if b in self.world.locations_state:
                self.world.locations_state[b].connections_state.pop(a, None)
            return True
        except Exception:
            return False

    def _gm_set_edge_status(self, a: str, b: str, status: str) -> bool:
        """Set status of edge (a,b) to 'open' or 'closed' in both directions, creating entries if missing."""
        try:
            if a not in self.world.locations_state or b not in self.world.locations_state:
                return False
            st_a = self.world.locations_state[a]
            st_b = self.world.locations_state[b]
            ent_a = st_a.connections_state.setdefault(b, {})
            ent_b = st_b.connections_state.setdefault(a, {})
            st = "closed" if str(status).lower() == "closed" else "open"
            ent_a["status"] = st
            ent_b["status"] = st
            return True
        except Exception:
            return False

    def _gm_remove_memory(self, npc_id: str) -> bool:
        """Remove the most recent memory entry from an NPC, if any."""
        try:
            npc = self.world.npcs.get(npc_id)
            if not npc or not getattr(npc, "memories", None):
                return False
            npc.memories.pop()
            return True
        except Exception:
            return False

    def _gm_remove_goal(self, npc_id: str) -> bool:
        """Remove the most recent goal entry from an NPC, if any."""
        try:
            npc = self.world.npcs.get(npc_id)
            if not npc or not getattr(npc, "goals", None):
                return False
            npc.goals.pop()
            return True
        except Exception:
            return False

    def handle_renderer_command(self, cmd: Tuple[str, Any], refresh: bool = True) -> None:
        """
        Consume commands emitted by the renderer. Commands are tuples (name, payload)
        where payload is typically a string or dict depending on the command.
        """
        try:
            name = cmd[0] if isinstance(cmd, tuple) and len(cmd) > 0 else None
            payload = cmd[1] if isinstance(cmd, tuple) and len(cmd) > 1 else None
            if not isinstance(name, str):
                return

            # No-op view commands (renderer already updated its internal view state)
            if name in {"noop", "enter", "back"}:
                return

            def _refresh_conn_snapshot():
                try:
                    snapshot: Dict[str, Dict[str, Any]] = {}
                    for loc_id, loc_state in self.world.locations_state.items():
                        cs = getattr(loc_state, "connections_state", {}) or {}
                        snap_entry: Dict[str, Any] = {}
                        for nid, meta in cs.items():
                            status = (meta or {}).get("status", "open")
                            entry = {"status": status}
                            direction = (meta or {}).get("direction")
                            if direction:
                                entry["direction"] = direction
                            snap_entry[str(nid)] = entry
                        snapshot[str(loc_id)] = snap_entry
                    self._ui_meta["__connections_state__"] = snapshot
                except Exception:
                    pass

            # Inspection payloads (update the God Mode panel inspector)
            if name == "inspect_actor" and isinstance(payload, str):
                self._ui_meta["__inspector__"] = self._build_inspector_for_actor(payload)
            elif name == "inspect_location" and isinstance(payload, str):
                self._ui_meta["__inspector__"] = self._build_inspector_for_location(payload)

            # GM content editing
            elif name == "gm_add_memory" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                text = payload.get("text", "")
                if isinstance(npc_id, str) and isinstance(text, str):
                    self._gm_add_memory(npc_id, text)
                    self._ui_meta["__inspector__"] = self._build_inspector_for_actor(npc_id)
            elif name == "gm_add_goal" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                text = payload.get("text", "")
                if isinstance(npc_id, str) and isinstance(text, str):
                    self._gm_add_goal(npc_id, text)
                    self._ui_meta["__inspector__"] = self._build_inspector_for_actor(npc_id)
            elif name == "gm_remove_memory" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                if isinstance(npc_id, str) and self._gm_remove_memory(npc_id):
                    self._ui_meta["__inspector__"] = self._build_inspector_for_actor(npc_id)
            elif name == "gm_remove_goal" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                if isinstance(npc_id, str) and self._gm_remove_goal(npc_id):
                    self._ui_meta["__inspector__"] = self._build_inspector_for_actor(npc_id)

            # GM actor/item spawn/delete/move
            elif name == "gm_spawn_npc" and isinstance(payload, dict):
                loc = payload.get("location_id")
                if isinstance(loc, str):
                    nid = self._gm_spawn_npc(loc)
                    if nid:
                        self._ui_meta["__inspector__"] = self._build_inspector_for_actor(nid)
            elif name == "gm_spawn_item" and isinstance(payload, dict):
                loc = payload.get("location_id")
                if isinstance(loc, str):
                    iid = self._gm_spawn_item(loc)
                    if iid:
                        self._ui_meta["__inspector__"] = self._build_inspector_for_location(loc)
            elif name == "gm_move_actor" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                to_loc = payload.get("to_location_id")
                if isinstance(npc_id, str) and isinstance(to_loc, str) and self._gm_move_actor(npc_id, to_loc):
                    self._ui_meta["__inspector__"] = self._build_inspector_for_actor(npc_id)
            elif name == "gm_delete_npc" and isinstance(payload, dict):
                npc_id = payload.get("npc_id")
                if isinstance(npc_id, str) and self._gm_delete_npc(npc_id):
                    # Clear inspector if it was targeting this actor
                    insp = self._ui_meta.get("__inspector__", {})
                    if isinstance(insp, dict) and insp.get("type") == "actor":
                        if ((insp.get("actor") or {}).get("id") == npc_id):
                            self._ui_meta["__inspector__"] = {}
            elif name == "gm_delete_item" and isinstance(payload, dict):
                item_id = payload.get("item_id")
                if isinstance(item_id, str) and self._gm_delete_item(item_id):
                    # No specific inspector refresh (item might be in location/actor)
                    pass

            # GM location graph and topology edits
            elif name == "gm_create_location" and isinstance(payload, dict):
                loc = payload.get("location_id")
                desc = payload.get("description", "")
                if isinstance(loc, str) and self._gm_create_location(loc, str(desc or "")):
                    self._ui_meta["world_layout_changed"] = True
                    self._ui_meta["__inspector__"] = self._build_inspector_for_location(loc)
                    _refresh_conn_snapshot()
            elif name == "gm_delete_location" and isinstance(payload, dict):
                loc = payload.get("location_id")
                if isinstance(loc, str) and self._gm_delete_location(loc):
                    self._ui_meta["world_layout_changed"] = True
                    # If inspector was this location, clear it
                    insp = self._ui_meta.get("__inspector__", {})
                    if isinstance(insp, dict) and insp.get("type") == "location":
                        if ((insp.get("location") or {}).get("id") == loc):
                            self._ui_meta["__inspector__"] = {}
                    _refresh_conn_snapshot()
            elif name == "gm_connect" and isinstance(payload, dict):
                a = payload.get("a"); b = payload.get("b")
                if isinstance(a, str) and isinstance(b, str) and self._gm_connect_locations(a, b, status="open"):
                    _refresh_conn_snapshot()
            elif name == "gm_disconnect" and isinstance(payload, dict):
                a = payload.get("a"); b = payload.get("b")
                if isinstance(a, str) and isinstance(b, str) and self._gm_disconnect_locations(a, b):
                    _refresh_conn_snapshot()
            elif name == "gm_set_edge_status" and isinstance(payload, dict):
                a = payload.get("a"); b = payload.get("b"); st = payload.get("status", "open")
                if isinstance(a, str) and isinstance(b, str) and self._gm_set_edge_status(a, b, st):
                    _refresh_conn_snapshot()

            # After processing, optionally refresh a frame
            if refresh:
                self._renderer_push_state()
        except Exception:
            # Keep UI resilient; swallow errors
            pass
    def _renderer_push_state(self):
        if not getattr(self, "renderer", None):
            return
        try:
            # Detect structural world changes (dynamic layout) and notify renderer if needed
            try:
                prev_sig = (self._ui_meta or {}).get("__layout_signature__", {})
                cur_tops = sorted([str(x) for x in self.world.locations_static.keys()])
                cur_subs = {str(k): list(map(str, getattr(self.world.get_location_state(k), "sublocations", []) or []))
                            for k in self.world.locations_static.keys()}
                cur_sig = {"tops": cur_tops, "subs": cur_subs}
                if prev_sig != cur_sig:
                    # Update board on renderer
                    if hasattr(self.renderer, "set_board"):
                        self.renderer.set_board(cur_tops, cur_subs)  # type: ignore[call-arg]
                    # Emit event-like flag so UI can optionally react
                    self._ui_meta["world_layout_changed"] = True
                    self._ui_meta["__layout_signature__"] = cur_sig
                else:
                    self._ui_meta.pop("world_layout_changed", None)
            except Exception:
                pass

            # Merge UI meta into messages channel for renderer
            merged_msgs = dict(self._last_actor_msgs)
            try:
                if self._ui_meta:
                    for k, v in self._ui_meta.items():
                        merged_msgs[k] = v
                # Ensure connections_state snapshot is always present and enriched with directions
                if "__connections_state__" not in merged_msgs:
                    snapshot: Dict[str, Dict[str, Any]] = {}
                    for loc_id, loc_state in self.world.locations_state.items():
                        cs = getattr(loc_state, "connections_state", {}) or {}
                        snap_entry: Dict[str, Any] = {}
                        for nid, meta in cs.items():
                            status = (meta or {}).get("status", "open")
                            direction = (meta or {}).get("direction", None)
                            entry: Dict[str, Any] = {"status": status}
                            if direction:
                                entry["direction"] = direction
                            snap_entry[str(nid)] = entry
                        snapshot[str(loc_id)] = snap_entry
                    merged_msgs["__connections_state__"] = snapshot

                # Derive layout neighbors dynamically from current connections_state
                try:
                    layout_neighbors: Dict[str, Dict[str, bool]] = {}
                    for loc_id, loc_state in self.world.locations_state.items():
                        cs = getattr(loc_state, "connections_state", {}) or {}
                        undirected: Dict[str, bool] = {}
                        for neighbor_id in cs.keys():
                            undirected[str(neighbor_id)] = True
                        layout_neighbors[str(loc_id)] = undirected
                    # Keep key name for renderer compatibility
                    merged_msgs["__static_neighbors__"] = layout_neighbors
                except Exception as e:
                    try:
                        print(f"[Renderer] Failed to build dynamic layout neighbors: {e}")
                    except Exception:
                        pass
            except Exception:
                pass

            # Optional: filter messages/actors by focused location if set (basic example)
            actor_list = self._compact_actor_list()
            if self._ui_focus_location:
                try:
                    focus = self._ui_focus_location
                    actor_list = [a for a in actor_list if a.get("location") == focus]
                    merged_msgs["__focus__"] = {"location": focus}
                except Exception:
                    pass

            if hasattr(self.renderer, "update_state"):
                self.renderer.update_state(actor_list, merged_msgs)  # type: ignore[call-arg]
            # Allow renderer to process input and draw a frame
            cmd = self.renderer.run_once() if hasattr(self.renderer, "run_once") else None  # type: ignore[call-arg]
            if isinstance(cmd, tuple):
                # When called from within _renderer_push_state, avoid recursive refresh
                try:
                    self.handle_renderer_command(cmd, refresh=False)
                except Exception:
                    pass
        except Exception:
            pass

    def _record_actor_last_message(self, event: Event):
        """Store a compact JSON message for the chat bubble of the actor."""
        try:
            actor_key = event.actor_id or ""
            if not actor_key:
                return

            # Previously suppressed inventory/stats/analyze; now allow them so more actors display bubbles.
            # No early return here; all event types can create bubbles.

            # Filtered compact JSON: include only essential fields
            msg = {"t": event.event_type}
            if event.target_ids:
                msg["targets"] = event.target_ids
            if event.payload:
                payload = dict(event.payload)
                content = payload.get("content")
                if isinstance(content, str):
                    payload["content"] = content[:160]
                msg["p"] = payload
            self._last_actor_msgs[actor_key] = json.dumps(msg, ensure_ascii=False)
        except Exception:
            pass

    def handle_event(self, event: Event):
        handler = self.event_handlers.get(event.event_type)
        if handler:
            handler(event)
        else:
            # Fallback for simple world mutations without bespoke logic
            try:
                print(f"[Simulator] No handler for event_type='{event.event_type}'. Applying to world and narrating.")
            except Exception:
                pass
            self.world.apply_event(event)
            msg = self.narrator.render(event)
            if msg:
                print(msg)
        # Common post-processing
        self.record_perception(event)
        self._record_actor_last_message(event)
        self._gc_conversations()
        # Optional: log chosen tool/params lightly to run log via stdout tee
        try:
            if event and event.actor_id and event.event_type:
                # Print a compact trace; params already normalized in bubbles
                print(f"[trace] {event.actor_id} -> {event.event_type}")
        except Exception:
            pass

    # --- Individual event handlers ---

    def _emit_narration(self, event: Event):
        try:
            msg = self.narrator.render(event)
            if msg:
                print(msg)
        except Exception:
            pass

    def _handle_describe_location(self, event: Event):
        self._emit_narration(event)

    def _handle_move(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_grab(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_drop(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_eat(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_attack_attempt(self, event: Event):
        attacker = self.world.get_npc(event.actor_id)
        target = self.world.get_npc(event.target_ids[0])
        result = combat_rules.resolve_attack(self.world, attacker, target)
        payload = {
            "to_hit": result["to_hit"],
            "target_ac": result["target_ac"],
        }
        if result["hit"]:
            payload["damage"] = result["damage"]
            self.event_queue.append(
                Event(
                    event_type="attack_hit",
                    tick=self.game_tick,
                    actor_id=event.actor_id,
                    target_ids=event.target_ids,
                    payload=payload,
                )
            )
            self.event_queue.append(
                Event(
                    event_type="damage_applied",
                    tick=self.game_tick,
                    actor_id=event.actor_id,
                    target_ids=event.target_ids,
                    payload={
                        "amount": result["damage"],
                        "damage_type": combat_rules.get_weapon(self.world, attacker).damage_type,
                    },
                )
            )
        else:
            self.event_queue.append(
                Event(
                    event_type="attack_missed",
                    tick=self.game_tick,
                    actor_id=event.actor_id,
                    target_ids=event.target_ids,
                    payload=payload,
                )
            )
        self._emit_narration(event)

    def _handle_attack_hit(self, event: Event):
        self._emit_narration(event)

    def _handle_attack_missed(self, event: Event):
        self._emit_narration(event)

    def _handle_damage_applied(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)
        target = self.world.get_npc(event.target_ids[0])
        if target.hp <= 0 and "dead" not in target.tags.get("dynamic", []):
            loc_id = self.world.find_npc_location(target.id)
            self.event_queue.append(
                Event(
                    event_type="npc_died",
                    tick=self.game_tick,
                    actor_id=target.id,
                    target_ids=[loc_id] if loc_id else [],
                )
            )

    def _handle_talk(self, event: Event):
        # Conversation flow handling:
        speaker_id = event.actor_id
        content = event.payload.get("content", "")
        target_id = event.payload.get("recipient_id") or (event.target_ids[0] if event.target_ids else None)
        payload_convo_id = event.payload.get("conversation_id")
        is_interject = bool(event.payload.get("interject"))
        current_loc = self.world.find_npc_location(speaker_id)

        if is_interject and isinstance(payload_convo_id, str):
            convo = self.conversations.get(payload_convo_id)
            if convo:
                # Validate co-location with conversation location
                if current_loc and current_loc == convo.get("location_id"):
                    # Add if not already a participant
                    if speaker_id not in convo["participants"]:
                        convo["participants"].append(speaker_id)
                        self.actor_conversation[speaker_id] = payload_convo_id
                        # Join at end of queue
                        if speaker_id != convo.get("current_speaker"):
                            if speaker_id not in convo.get("turn_order", []):
                                convo["turn_order"].append(speaker_id)
                    # If it is their turn right now, accept line; else just log as aside without turn advance
                    if convo.get("current_speaker") == speaker_id:
                        convo["history"].append({"speaker": speaker_id, "tick": self.game_tick, "content": content})
                        convo["last_interaction_tick"] = self.game_tick
                        self._emit_narration(event)
                        self._advance_conversation_turn(payload_convo_id, hint_target=target_id)
                    else:
                        # Allow interjecting content as history but don't advance turn
                        convo["history"].append({"speaker": speaker_id, "tick": self.game_tick, "content": content})
                        convo["last_interaction_tick"] = self.game_tick
                        self._emit_narration(event)
            return

        # Normal talk handling
        convo_id = self.actor_conversation.get(speaker_id)

        if convo_id is None:
            # Start a new conversation if possible
            location_id = current_loc
            participants = [speaker_id]
            if target_id:
                # Only add target if co-located
                if self.world.find_npc_location(target_id) == location_id:
                    participants.append(target_id)
            if len(participants) < 2:
                # Not enough participants for a convo; still narrate the line as standalone talk
                self._emit_narration(event)
            else:
                convo_id = f"convo_{speaker_id}_{self.game_tick}"
                self.conversations[convo_id] = {
                    "conversation_id": convo_id,
                    "participants": participants[:],
                    "turn_order": [pid for pid in participants if pid != speaker_id],
                    "current_speaker": speaker_id,
                    "start_tick": self.game_tick,
                    "last_interaction_tick": self.game_tick,
                    "history": [{"speaker": speaker_id, "tick": self.game_tick, "content": content}],
                    "location_id": location_id,
                }
                for pid in participants:
                    self.actor_conversation[pid] = convo_id
                self._emit_narration(event)
                # Advance turn: targeted speech moves target to front if in participants
                self._advance_conversation_turn(convo_id, hint_target=target_id)
        else:
            # Must be the speaker's turn
            convo = self.conversations.get(convo_id)
            if convo and convo.get("current_speaker") == speaker_id:
                # Append to history and narrate
                convo["history"].append({"speaker": speaker_id, "tick": self.game_tick, "content": content})
                convo["last_interaction_tick"] = self.game_tick
                self._emit_narration(event)
                # Advance turn
                self._advance_conversation_turn(convo_id, hint_target=target_id)

    def _handle_talk_loud(self, event: Event):
        self._emit_narration(event)

    def _handle_scream(self, event: Event):
        self._emit_narration(event)

    def _handle_inventory(self, event: Event):
        self._emit_narration(event)

    def _handle_stats(self, event: Event):
        self._emit_narration(event)

    def _handle_equip(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_unequip(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_analyze(self, event: Event):
        self._emit_narration(event)

    def _handle_give(self, event: Event):
        # Simple world mutation; expect payload with item_id/recipient_id for clarity but keep target_ids compat
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_toggle_starvation(self, event: Event):
        self.starvation_enabled = event.payload.get("enabled", True)
        if not self.starvation_enabled:
            for npc in self.world.npcs.values():
                npc.hunger_stage = "sated"
                npc.last_meal_tick = self.game_tick
        self._emit_narration(event)

    def _handle_open_close_connection(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)
        # Push a fresh connections_state snapshot to UI meta so renderer can draw open/closed edges
        try:
            snapshot: Dict[str, Dict[str, Any]] = {}
            for loc_id, loc_state in self.world.locations_state.items():
                try:
                    # Keep only neighbor->{"status": ...}
                    cs = getattr(loc_state, "connections_state", {}) or {}
                    snap_entry: Dict[str, Any] = {}
                    for nid, meta in cs.items():
                        try:
                            status = (meta or {}).get("status", "open")
                        except Exception:
                            status = "open"
                        snap_entry[str(nid)] = {"status": status}
                    snapshot[str(loc_id)] = snap_entry
                except Exception:
                    continue
            # Store under a reserved key consumed by renderer.update_state
            self._ui_meta["__connections_state__"] = snapshot
        except Exception:
            pass

    def _handle_npc_died(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)
        # Prune last message cache for dead actors to avoid unbounded growth
        try:
            if event.actor_id:
                self._last_actor_msgs.pop(event.actor_id, None)
        except Exception:
            pass

    def _handle_wait(self, event: Event):
        self._emit_narration(event)

    def _handle_rest(self, event: Event):
        self.world.apply_event(event)
        self._emit_narration(event)

    def _handle_leave_conversation(self, event: Event):
        # Remove actor from their active conversation
        self._leave_conversation(event.actor_id)

    def record_perception(self, event: Event):
        """Add a simplified perception entry to actors in the same or adjacent locations per rules."""
        if event.event_type in {"describe_location", "wait"}:
            return

        # Determine the primary location where the event is perceived
        if event.event_type == "move":
            location_id = event.target_ids[0] if event.target_ids else None
        elif event.event_type == "npc_died":
            location_id = event.target_ids[0] if event.target_ids else None
        else:
            location_id = self.world.find_npc_location(event.actor_id)

        if not location_id:
            return

        recipients: set[str] = set()
        try:
            loc_state = self.world.get_location_state(location_id)
            # Same-location recipients (excluding the actor)
            for npc_id in getattr(loc_state, "occupants", []):
                if npc_id != event.actor_id:
                    recipients.add(npc_id)
        except Exception:
            pass

        # Noise propagation rules
        try:
            if event.event_type in {"scream", "talk_loud"}:
                loc_static = self.world.get_location_static(location_id)
                state_here = self.world.get_location_state(location_id)
                for neighbor_id in getattr(loc_static, "hex_connections", {}).values():
                    conn = getattr(state_here, "connections_state", {}).get(neighbor_id, {})
                    is_open = conn.get("status", "open") == "open"
                    if event.event_type == "scream" or is_open:
                        neighbor_state = self.world.get_location_state(neighbor_id)
                        for npc_id in getattr(neighbor_state, "occupants", []):
                            # If neighbor location has an elevated_vantage_point tag, allow perception even if door closed (visual), but this block is for audio
                            recipients.add(npc_id)
        except Exception:
            pass

        # Append as structured PerceptionEvent objects and cap buffer
        for npc_id in recipients:
            try:
                npc = self.world.get_npc(npc_id)
                # Elevated vantage point: allow additional cross-location perception for visual events even if door closed
                try:
                    visual_events = {"grab","drop","equip","unequip","attack_hit","attack_missed","damage_applied","inventory","stats","analyze"}
                    if event.event_type in visual_events:
                        # If recipient has elevated_vantage_point inherent tag, they can also perceive from neighbors
                        tags = (npc.tags or {})
                        inh = set((tags.get("inherent") or []))
                        if "elevated_vantage_point" in inh:
                            # No extra work here beyond inclusion; rule already increases recipients earlier
                            pass
                except Exception:
                    pass
                pe: PerceptionEvent = make_perception_from_event(event, location_id=location_id)
                npc.short_term_memory.append(pe)
                # Cap STM size using configured buffer
                cap = max(1, int(getattr(self, "perception_buffer_size", 30)))
                while len(npc.short_term_memory) > cap:
                    npc.short_term_memory.pop(0)
            except Exception:
                continue

    # Conversation helpers
    def _advance_conversation_turn(self, convo_id: str, hint_target: Optional[str] = None):
        convo = self.conversations.get(convo_id)
        if not convo:
            return
        current = convo.get("current_speaker")
        turn_order: List[str] = convo.get("turn_order", [])
        participants: List[str] = convo.get("participants", [])

        # Ensure turn_order only contains current participants except current speaker
        turn_order = [p for p in turn_order if p in participants and p != current]

        # Target rule: if hint_target in participants, move it to the front
        if hint_target and hint_target in participants and hint_target != current:
            # Ensure target is in queue at most once, then move to front
            turn_order = [pid for pid in turn_order if pid != hint_target]
            turn_order.insert(0, hint_target)

        # Move current to end
        if current and current in participants:
            turn_order.append(current)

        # Pop next speaker
        next_speaker = turn_order.pop(0) if turn_order else None
        convo["turn_order"] = turn_order
        convo["current_speaker"] = next_speaker
        convo["last_interaction_tick"] = self.game_tick

        # Dissolve if fewer than 2 participants remain
        if len(participants) < 2 or not next_speaker:
            self._dissolve_conversation(convo_id)

    def _leave_conversation(self, actor_id: str):
        convo_id = self.actor_conversation.get(actor_id)
        if not convo_id:
            return
        convo = self.conversations.get(convo_id)
        if not convo:
            self.actor_conversation.pop(actor_id, None)
            return
        participants: List[str] = convo.get("participants", [])
        if actor_id in participants:
            participants.remove(actor_id)
        # Remove from queues
        if actor_id == convo.get("current_speaker"):
            # If others remain, immediately advance to next speaker rather than setting None
            convo["current_speaker"] = None
            # Advance turn to keep flow going
            self._advance_conversation_turn(convo_id)
        # Remove from queues
        convo["turn_order"] = [p for p in convo.get("turn_order", []) if p != actor_id]
        self.actor_conversation.pop(actor_id, None)
        # Dissolve if fewer than 2 participants
        if len(participants) < 2:
            self._dissolve_conversation(convo_id)
        else:
            convo["last_interaction_tick"] = self.game_tick

    def _dissolve_conversation(self, convo_id: str):
        convo = self.conversations.pop(convo_id, None)
        if not convo:
            return
        for pid in list(convo.get("participants", [])):
            if self.actor_conversation.get(pid) == convo_id:
                self.actor_conversation.pop(pid, None)

    def _gc_conversations(self, timeout: int = 300):
        # Remove conversations that are stale or location participants dispersed
        to_remove = []
        for convo_id, convo in self.conversations.items():
            if self.game_tick - convo.get("last_interaction_tick", 0) > timeout:
                to_remove.append(convo_id)
                continue
            # If participants are no longer co-located, dissolve
            loc = convo.get("location_id")
            if not loc:
                continue
            still_here = [pid for pid in convo.get("participants", []) if self.world.find_npc_location(pid) == loc]
            if len(still_here) < 2:
                to_remove.append(convo_id)
        for cid in to_remove:
            self._dissolve_conversation(cid)
