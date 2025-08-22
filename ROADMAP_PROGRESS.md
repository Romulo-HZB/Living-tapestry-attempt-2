# Roadmap Progress

This document tracks the implementation status of the engine against the design roadmap in `Follow this`.

## Completed Work

- **Phase 1 – Data Schemas and WorldState**
  - Dataclasses for NPCs, locations and items are defined in `engine/data_models.py`.
  - `engine/world_state.py` loads all JSON data from the `data/` directory.
  - The world loader is exercised by the CLI and web server startup.

- **Phase 2 – Basic Event Loop**
  - `engine/simulator.py` implements a simple event queue and tick system.
  - Tools exist for looking, moving and grabbing items (`engine/tools`).
  - The CLI demo demonstrates moving an actor and picking up an item.
  - Actors have `next_available_tick` and tools have `time_cost`, providing the
    beginnings of the Action‑Time system.
  - `scripts/cli_game.py` implements an interactive command loop.

- **Phase 3 – LLM Command Parser**
  - `engine/llm_client.py` connects to an OpenAI-compatible endpoint, including OpenRouter.
  - `scripts/cli_game.py` can use the LLM to parse free text when `--llm` is supplied.

- **Phase 4 – Additional Tools**
  - A basic `attack` tool allows damaging other actors.
  - A `talk` tool enables simple speech output.
  - A `talk_loud` tool lets actors shout to adjacent locations if passages are open.
  - Item instances now store `current_location`, `owner_id`, `item_state`,
    `inventory` and `tags` fields. The `WorldState` assigns locations to items on
    load and updates ownership when items are grabbed.
  - NPCs now record simple perception events in `short_term_memory` whenever
    actions occur in their location.
  - `cli_game.py` has a `mem` command to inspect the player's recent memories.
  - Combat resolution now follows the ATTACK_ATTEMPT -> ATTACK_HIT/MISSED ->
    DAMAGE_APPLIED event chain with deterministic rules in `rpg/combat_rules.py`.
  - A `drop` tool lets actors place carried items in their current location.
  - A `stats` tool reports an actor's hit points, attributes and skills.
  - `equip` and `unequip` tools let actors manage equipment slots.
  - `look` now reports visible items and other actors in the location.
  - An `analyze` tool reports item details.
  - A `scream` tool lets actors broadcast messages; nearby NPCs record the event in their memories.
  - A basic hunger system tracks when actors last ate, updates their hunger stage each tick, and applies starvation damage over time.
  - An `eat` tool allows consuming food items to reset hunger.
  - A `give` tool transfers items between actors occupying the same location.
  - `open` and `close` tools toggle passage status between locations, and `move` respects closed connections.
  - A simple NPC think cycle lets non-player actors wander or comment when idle.
  - NPCs can now initiate attacks against other actors when sharing a location.
  - A `wait` tool lets actors deliberately pass time without acting.
  - A `rest` tool lets actors recover hit points over time.

## Outstanding Tasks

- Expand the toolset and improve combat handling (Phase 4).
- Develop NPC AI with memory and conversation systems (Phase 5).
- Build polish features such as the narrator, fallback system and tag rules.

## New Notes and Technical Decisions

- LLM failure modes and retries
  - Risk: Parser drift over time/context.
  - Plan: Introduce strict schema validation for command parsing (Pydantic or equivalent) with deterministic repair attempts:
    - Minimal edit distance to the nearest valid tool name when unknown tool appears.
    - Fill required fields when inferable (e.g., default target_id to null).
    - Tiered prompts for retries: generic -> per-tool clarifier -> “state mirror” echo that shows last invalid JSON and the expected schema.
    - Version every tool schema via a tool_version field to aid migrations.

- Tool explosion and coupling
  - Risk: Many small tools with duplicated logic (validation, permissions, range, line-of-sight).
  - Plan: Add shared validators in engine/validators.py:
    - validate_visibility(actor, target)
    - validate_reach(actor, target, weapon)
    - validate_permission(actor, item)
    - validate_container_access(actor, container)
  - These keep tools thin, consistent, and centrally testable.

- Pathfinding and knowledge
  - Risk: Pathfinding using ground truth can leak knowledge.
  - Plan:
    - Pathfinding uses only edges between known_locations for a given actor; unknown/unverified connections are not traversable.
    - Cache the last computed path and invalidate on connection_state change events for determinism and performance.

## Recent Progress

- Conversation skeleton (Phase 5, core infrastructure)
  - Added in-engine conversation state in Simulator: participants, current_speaker, turn_order, history, last_interaction_tick, location pinning.
  - Extended talk handling to:
    - Start a conversation when addressing a co-located target.
    - Enforce turn-taking for participants.
    - Support targeted speech to bias the next speaker.
    - Handle interjections via a tool that joins an ongoing conversation.
    - Auto-dissolve on timeout or if participants disperse or drop below two.
  - New tools:
    - InterjectTool (join an existing conversation and speak)
    - LeaveConversationTool (exit conversation)
  - Narrator now differentiates interjections in talk narration.
  - CLI: Registered new tools and added a simple 'conv' debug view under mem block to inspect current conversation state.
  - Conversation flow is exercised via the CLI; the old demo script has been removed.

- Web Interface (New)
  - Replaced the pygame hexagonal map interface with a modern web-based interface
  - Created Flask backend with REST API and WebSocket support
  - Built responsive HTML/CSS/JavaScript frontend with hexagonal grid visualization
  - Implemented real-time game state updates
  - Added natural language command parsing
  - Maintained full compatibility with existing game engine

## Next Steps

- Implement engine/validators.py and gradually refactor tools to use shared validation.
- Add leave_conversation and interject commands to CLI input for direct testing.
- Add simple theft-detection scaffold to GrabTool to emit social events per design §10.8.
- Add Pydantic schemas for command parsing and introduce the retry/repair pipeline.
- Create a minimal pathfinding module constrained by known_locations with cache invalidation on connection events.
