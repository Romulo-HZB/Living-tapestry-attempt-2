from typing import Dict, Optional, Any, List, Tuple
from .llm_client import LLMClient
from .data_models import Memory
import json
import re

PLANNER_SYSTEM_PROMPT = (
    "You are an action planner for a deterministic text-sim.\n"
    "Return ONLY a single JSON object: {\"tool\": string, \"params\": object} or null. No prose, no code fences.\n"
    "A 'tool_schemas' section and tiny examples will be provided in the user payload; obey them strictly.\n"
    "Rules:\n"
    "- Choose exactly one tool per turn.\n"
    "- Keep params minimal and valid; prefer IDs from context.\n"
    "- If no sensible action, return null.\n"
    "- If in a conversation and not current speaker, prefer null; consider interject ONLY for brief, meaningful asides.\n"
    "- Working memory is provided; consider goals, core memories, and recent perceptions when deciding.\n"
    "- When idle: prefer varied low-impact actions like talk with short emotes (e.g., 'nods.', 'hums.'), or wait; avoid repeating the same action consecutively.\n"
    "- Avoid selecting 'look' more than once every 5 turns; use it sparingly.\n"
    "- Use 'move' only to valid open neighbors.\n"
    "- Use 'attack' only if co-located and context justifies.\n"
    "- For durations like wait/rest without a number, use ticks=1.\n"
    "\n"
    "Embodiment and action:\n"
    "You are controlling a single embodied actor in a physical world. Choose exactly one concrete next action that physically advances the actor’s goal (e.g., move toward a target, open/close a door, talk/talk_loud when speech itself advances the goal).\n"
    "\n"
    "Navigation:\n"
    "If you intend to investigate something not in your current location, choose move toward an OPEN neighbor from context.location.connections_state. If a connection is closed, choose open (or close) first or pick an alternate OPEN route.\n"
    "\n"
    "Targeted speech:\n"
    "Only use talk/talk_loud when speech itself advances the goal. When speaking to someone present, include target_id. If the relevant person is elsewhere, move instead.\n"
    "\n"
    "Repetition hint:\n"
    "You receive repetition_hint = {last_tool_by_actor, avoid_repeat_within, look_cooldown}. Do not pick last_tool_by_actor again within avoid_repeat_within turns unless necessary. Avoid 'look' within look_cooldown. If you previously indicated you would investigate, prefer 'move' next.\n"
    "\n"
    "Hidden reasoning:\n"
    "Before deciding, write brief hidden reasoning inside <think>...</think>. Then output ONLY one JSON object with the command.\n"
)

# Minimal per-tool schemas and tiny examples used to assist the model and validate planner output.
_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "move": {
        "required": [],
        "one_of": [["target_location"]],
        "example": {"tool": "move", "params": {"target_location": "market_square"}},
    },
    "open": {
        "required": ["target_location"],
        "example": {"tool": "open", "params": {"target_location": "alley"}},
    },
    "close": {
        "required": ["target_location"],
        "example": {"tool": "close", "params": {"target_location": "market_square"}},
    },
    "attack": {
        "required": ["target_id"],
        "example": {"tool": "attack", "params": {"target_id": "npc_enemy"}},
    },
    "talk": {
        "required": ["content"],
        "optional": ["target_id"],
        "example": {"tool": "talk", "params": {"target_id": "npc_guard", "content": "Good day."}},
    },
    "talk_loud": {
        "required": ["content"],
        "example": {"tool": "talk_loud", "params": {"content": "Hello up there!"}},
    },
    "scream": {
        "required": ["content"],
        "example": {"tool": "scream", "params": {"content": "Help!"}},
    },
    "grab": {
        "required": ["item_id"],
        "example": {"tool": "grab", "params": {"item_id": "item_rusty_sword_1"}},
    },
    "drop": {
        "required": ["item_id"],
        "example": {"tool": "drop", "params": {"item_id": "item_rusty_sword_1"}},
    },
    "equip": {
        "required": ["item_id", "slot"],
        "example": {"tool": "equip", "params": {"item_id": "item_leather_armor_1", "slot": "torso"}},
    },
    "unequip": {
        "required": ["slot"],
        "example": {"tool": "unequip", "params": {"slot": "torso"}},
    },
    "inventory": {"required": [], "example": {"tool": "inventory", "params": {}}},
    "stats": {"required": [], "example": {"tool": "stats", "params": {}}},
    "analyze": {"required": ["item_id"], "example": {"tool": "analyze", "params": {"item_id": "item_apple_1"}}},
    "eat": {"required": ["item_id"], "example": {"tool": "eat", "params": {"item_id": "item_apple_1"}}},
    "give": {
        "required": ["item_id", "target_id"],
        "example": {"tool": "give", "params": {"item_id": "item_apple_1", "target_id": "npc_bard"}},
    },
    "toggle_starvation": {"required": ["enabled"], "example": {"tool": "toggle_starvation", "params": {"enabled": False}}},
    "wait": {"required": [], "optional": ["ticks"], "example": {"tool": "wait", "params": {"ticks": 1}}},
    "rest": {"required": [], "optional": ["ticks"], "example": {"tool": "rest", "params": {"ticks": 1}}},
    "interject": {
        "required": ["conversation_id", "content"],
        "example": {"tool": "interject", "params": {"conversation_id": "convo_123", "content": "Wait."}},
    },
    "leave_conversation": {"required": [], "example": {"tool": "leave_conversation", "params": {}}},
}

def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    return re.findall(r"[a-z0-9_]+", text)

def _score_memory(keywords: List[str], m: Memory) -> float:
    if not isinstance(m, Memory):
        # Legacy dict fallback
        blob = json.dumps(m, ensure_ascii=False)
        txt = blob
        tick = (m.get("tick") if isinstance(m, dict) else 0) or 0
        score = 0.0
        for k in keywords:
            if k in txt.lower():
                score += 1.0
        # recency boost
        score += min(2.0, tick / 100000.0)
        return score
    score = 0.0
    txt = f"{m.text} {json.dumps(m.payload, ensure_ascii=False)}".lower()
    for k in keywords:
        if k in txt:
            score += 1.0
    # status weighting
    if getattr(m, "status", "active") == "archived":
        score *= 0.6
    elif getattr(m, "status", "active") in {"recalled", "active"}:
        score *= 1.0
    elif getattr(m, "status", "active") == "consolidated":
        score *= 1.2
    # confidence weighting
    try:
        score *= max(0.3, min(1.2, float(getattr(m, "confidence", 1.0))))
    except Exception:
        pass
    # recency boost
    try:
        score += min(2.0, float(getattr(m, "tick", 0)) / 100000.0)
    except Exception:
        pass
    return score

def _memory_to_dict(m: Any) -> Any:
    try:
        # Avoid importing dataclasses.asdict to keep deps light; Memory likely has __dict__
        if isinstance(m, Memory):
            return {
                "text": getattr(m, "text", ""),
                "tick": getattr(m, "tick", 0),
                "priority": getattr(m, "priority", "normal"),
                "status": getattr(m, "status", "active"),
                "source_id": getattr(m, "source_id", None),
                "confidence": getattr(m, "confidence", 1.0),
                "is_secret": getattr(m, "is_secret", False),
                "payload": getattr(m, "payload", {}) or {},
            }
        if isinstance(m, dict):
            return m
    except Exception:
        pass
    return m

def build_working_memory(context: Dict[str, Any], retrieval_top_k: int = 6, max_stm: int = 10) -> Dict[str, Any]:
    """
    Build a compact working memory slice from actor data:
    - goals (top few)
    - core_memories
    - short_term_memory (recent perception events)
    - retrieved long-term memories (keyword search)
    """
    actor = context.get("actor") or {}
    wm: Dict[str, Any] = {}
    # goals
    goals = (actor.get("goals") or []) if isinstance(actor.get("goals"), list) else []
    wm["goals"] = goals[:5]
    # core memories
    core = (actor.get("core_memories") or []) if isinstance(actor.get("core_memories"), list) else []
    wm["core_memories"] = core[:10]
    # short-term perception
    stm = (actor.get("short_term_memory") or []) if isinstance(actor.get("short_term_memory"), list) else []
    wm["perceptions"] = stm[-max_stm:]
    # build keyword set from recent perception payloads + conversation + location/topic hints
    keywords: List[str] = []
    # actor name/id
    for k in _tokenize(actor.get("name") or "") + _tokenize(actor.get("id") or ""):
        if k not in keywords:
            keywords.append(k)
    # location/topic
    loc = context.get("location") or {}
    for k in _tokenize((loc.get("static") or {}).get("description") or ""):
        if k not in keywords:
            keywords.append(k)
    convo = context.get("conversation") or {}
    for h in (convo.get("history") or [])[-4:]:
        if isinstance(h, dict):
            for k in _tokenize(h.get("content") or ""):
                if k not in keywords:
                    keywords.append(k)
    for p in stm[-max_stm:]:
        if isinstance(p, dict):
            payload = p.get("payload") or {}
            for k in _tokenize(json.dumps(payload, ensure_ascii=False)):
                if k not in keywords:
                    keywords.append(k)
    # retrieve from LTM provided in context.actor.memories if present; planner gets NPC objects indirectly,
    # but the simulator currently passes persona without full memories. If present, use it. Else, empty list.
    ltm: List[Any] = actor.get("memories") or []
    scored: List[Tuple[float, Any]] = []
    for m in ltm:
        try:
            if isinstance(m, Memory):
                scored.append((_score_memory(keywords, m), m))
            else:
                scored.append((_score_memory(keywords, m), m))
        except Exception:
            continue
    scored.sort(key=lambda t: t[0], reverse=True)
    top = [m for _, m in scored[:retrieval_top_k]]
    # Ensure JSON-safe
    wm["retrieved_memories"] = [_memory_to_dict(m) for m in top]
    # Also ensure core/goals/perceptions are JSON-safe if they accidentally contain dataclasses
    wm["core_memories"] = [_memory_to_dict(m) for m in wm.get("core_memories", [])]
    # Ensure Goal dataclasses are serializable
    def _goal_to_dict(g: Any) -> Any:
        if isinstance(g, dict):
            return g
        try:
            return {
                "text": getattr(g, "text", ""),
                "type": getattr(g, "type", "note"),
                "priority": getattr(g, "priority", "normal"),
                "status": getattr(g, "status", "active"),
                "payload": getattr(g, "payload", {}) or {},
                "expiry_tick": getattr(g, "expiry_tick", None),
            }
        except Exception:
            return getattr(g, "__dict__", g)
    wm["goals"] = [_goal_to_dict(g) for g in wm.get("goals", [])]
    # PerceptionEvent dataclasses may appear; convert robustly
    def _perception_to_dict(p: Any) -> Any:
        if isinstance(p, dict):
            return p
        try:
            return {
                "event_type": getattr(p, "event_type", getattr(p, "type", "")),
                "tick": getattr(p, "tick", 0),
                "actor_id": getattr(p, "actor_id", None),
                "target_ids": list(getattr(p, "target_ids", []) or []),
                "payload": getattr(p, "payload", {}) or {},
                "location_id": getattr(p, "location_id", None),
            }
        except Exception:
            return getattr(p, "__dict__", p)
    wm["perceptions"] = [_perception_to_dict(p) for p in wm.get("perceptions", [])]
    return wm

class NPCPlanner:
    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()

    def plan(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Compose repetition hints from STM
        stm = ((context.get("actor") or {}).get("short_term_memory") or [])
        last_tool = None
        actor_id = (context.get("actor") or {}).get("id")
        for m in reversed(stm[-6:]):
            if isinstance(m, dict) and m.get("actor_id") == actor_id:
                last_tool = m.get("event_type")
                break
        repetition_hint = {"last_tool_by_actor": last_tool, "avoid_repeat_within": 2, "look_cooldown": 5}

        # Build working memory slice and attach to the context sent to the model
        working_memory = build_working_memory(context)

        # Sanitize actor.memories in the outer context to avoid dataclass leakage
        ctx_copy = dict(context)
        actor_copy = dict(ctx_copy.get("actor") or {})
        if isinstance(actor_copy.get("memories"), list):
            actor_copy["memories"] = [_memory_to_dict(m) for m in actor_copy["memories"]]
        if isinstance(actor_copy.get("core_memories"), list):
            actor_copy["core_memories"] = [_memory_to_dict(m) for m in actor_copy["core_memories"]]
        # Sanitize goals as well
        def _goal_to_dict(g: Any) -> Any:
            if isinstance(g, dict):
                return g
            try:
                return {
                    "text": getattr(g, "text", ""),
                    "type": getattr(g, "type", "note"),
                    "priority": getattr(g, "priority", "normal"),
                    "status": getattr(g, "status", "active"),
                    "payload": getattr(g, "payload", {}) or {},
                    "expiry_tick": getattr(g, "expiry_tick", None),
                }
            except Exception:
                return getattr(g, "__dict__", g)
        if isinstance(actor_copy.get("goals"), list):
            actor_copy["goals"] = [_goal_to_dict(g) for g in actor_copy["goals"]]
        # Sanitize short_term_memory as well (PerceptionEvent)
        if isinstance(actor_copy.get("short_term_memory"), list):
            def _perception_to_dict(p: Any) -> Any:
                if isinstance(p, dict):
                    return p
                try:
                    return {
                        "event_type": getattr(p, "event_type", getattr(p, "type", "")),
                        "tick": getattr(p, "tick", 0),
                        "actor_id": getattr(p, "actor_id", None),
                        "target_ids": list(getattr(p, "target_ids", []) or []),
                        "payload": getattr(p, "payload", {}) or {},
                        "location_id": getattr(p, "location_id", None),
                    }
                except Exception:
                    return getattr(p, "__dict__", p)
            actor_copy["short_term_memory"] = [_perception_to_dict(p) for p in actor_copy["short_term_memory"]]
        # Ensure available_tools is JSON-serializable (it may contain objects in some paths)
        if isinstance(ctx_copy.get("available_tools"), list):
            ctx_copy["available_tools"] = [t if isinstance(t, str) else str(getattr(t, "name", t)) for t in ctx_copy["available_tools"]]
        ctx_copy["actor"] = actor_copy

        # Optionally add a concise neighbor names mapping to aid navigation if available in location data.
        # This is additive context only; engine/tool schemas remain unchanged.
        loc_static = ((context.get("location") or {}).get("static") or {})
        # Build open-neighbor label map (id->label). Labels may fall back to id if not known.
        neighbor_names = {}
        try:
            conns = ((context.get("location") or {}).get("connections_state") or {})
            if isinstance(conns, dict):
                for nid, meta in (conns or {}).items():
                    if isinstance(meta, dict) and (meta.get("status") == "open"):
                        neighbor_names[str(nid)] = str(nid)
        except Exception:
            neighbor_names = {}
        
        # Build schemas/examples for only the tools available in this context
        tool_schemas = {}
        tool_examples = {}
        try:
            for t in (ctx_copy.get("available_tools") or []):
                spec = _SCHEMAS.get(t)
                if spec:
                    tool_schemas[t] = {k: v for k, v in spec.items() if k in {"required", "optional", "one_of"}}
                    ex = spec.get("example")
                    if ex:
                        tool_examples[t] = ex
        except Exception:
            tool_schemas = {}
            tool_examples = {}

        user_payload = {
            "context": ctx_copy,
            "working_memory": working_memory,
            "repetition_hint": repetition_hint,
            "neighbor_names": neighbor_names,
            "tool_schemas": tool_schemas,
            "tool_examples": tool_examples,
            "input": "Decide the next action. Respect repetition_hint.last_tool_by_actor and avoid repeating the same tool within repetition_hint.avoid_repeat_within turns. Do not choose look if last use was within look_cooldown turns."
        }
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload)},
        ]
        reply = self.llm.chat(messages)
        extractor = getattr(self.llm, "_strip_think_and_extract_json", None)
        parsed = extractor(reply) if callable(extractor) else None

        # Helper: schema-level validation for simple checks
        def _validate_schema(t: str, p: Dict[str, Any]) -> Optional[str]:
            spec = _SCHEMAS.get(t)
            if not spec:
                return None
            # required keys
            for rk in spec.get("required", []) or []:
                if rk not in p:
                    return f"missing required param '{rk}'"
            # one_of groups: at least one present
            for group in spec.get("one_of", []) or []:
                if not any(k in p for k in group):
                    return f"one of {group} is required"
            return None

        def _normalize(t: str, p: Dict[str, Any]) -> Dict[str, Any]:
            p = dict(p or {})
            if t in {"talk", "talk_loud", "scream"}:
                content = p.get("content")
                p["content"] = content[:200] if isinstance(content, str) else "..."
            if t == "move":
                loc = p.get("target_location") or p.get("location_id") or p.get("target") or p.get("to")
                if isinstance(loc, str):
                    p["target_location"] = loc
            if t == "open" or t == "close":
                loc = p.get("target_location") or p.get("location_id") or p.get("target")
                if isinstance(loc, str):
                    p["target_location"] = loc
            if t == "attack":
                tgt = p.get("target_id") or p.get("target")
                if isinstance(tgt, list) and tgt:
                    p["target_id"] = tgt[0]
                elif isinstance(tgt, str):
                    p["target_id"] = tgt
            return p

        def _stage2_repair(prev_obj: Any, err: str, t_hint: Optional[str]) -> Optional[Dict[str, Any]]:
            try:
                schema = _SCHEMAS.get(t_hint or "") or {}
                example = schema.get("example") or {}
                clarifier = {
                    "context": {"error": err, "last_output": prev_obj, "expected_schema": schema, "example": example},
                    "instruction": "Repair your output to satisfy expected_schema. Return ONLY a single JSON object {tool, params}."
                }
                messages2 = [
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(clarifier)},
                ]
                reply2 = self.llm.chat(messages2)
                parsed2 = extractor(reply2) if callable(extractor) else None
                if isinstance(parsed2, dict):
                    return parsed2
            except Exception:
                return None
            return None

        # Stage 1: parse and normalize
        if not isinstance(parsed, dict):
            return None
        tool = parsed.get("tool")
        params = parsed.get("params", {}) if isinstance(parsed.get("params"), dict) else {}
        if tool is None or (isinstance(tool, str) and tool.strip().lower() in {"null", "none"}):
            return None
        valid_tools = {
            "move","talk","talk_loud","scream","look","grab","drop","attack",
            "inventory","stats","equip","unequip","analyze","eat","give",
            "open","close","toggle_starvation","wait","rest","interject","leave_conversation",
        }
        if tool not in valid_tools:
            # Stage 3 fallback immediately
            print(f"[NPCPlanner] invalid tool: {tool}")
            return {"tool": "wait", "params": {"ticks": 1}}
        params = _normalize(tool, params)
        err1 = _validate_schema(tool, params)
        if err1 is None:
            return {"tool": tool, "params": params}

        # Stage 2: re-ask with terse state mirror (schema + example)
        repaired = _stage2_repair(parsed, err1, tool)
        if isinstance(repaired, dict):
            tool2 = repaired.get("tool")
            params2 = repaired.get("params", {}) if isinstance(repaired.get("params"), dict) else {}
            if tool2 in valid_tools:
                params2 = _normalize(tool2, params2)
                err2 = _validate_schema(tool2, params2)
                if err2 is None:
                    return {"tool": tool2, "params": params2}
                else:
                    print(f"[NPCPlanner] repair still invalid: {err2}")

        # Stage 3: final fallback
        print(f"[NPCPlanner] falling back to wait due to: {err1}")
        return {"tool": "wait", "params": {"ticks": 1}}
