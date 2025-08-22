# Living Tapestry RPG Engine

A text-based RPG engine with AI-driven NPC behavior, featuring a deterministic world simulation and LLM-powered character interactions.

## Features

- **Deterministic Simulation**: All world changes happen through a centralized event system
- **AI-Driven NPCs**: Characters make decisions based on memories, goals, and perceptions
- **Rich Interaction System**: Movement, combat, conversation, item management, and more
- **Modular Architecture**: Extensible tool system for adding new actions
- **Web Interface**: Modern browser-based interface (see `web/` directory)
- **LLM Integration**: Natural language command interpretation

## Installation

1. Make sure you have Python 3.7+ installed
2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Running the Game

### Command Line Interface
```
python scripts/cli_game.py
```

Enter natural language commands like:
- "move to the market square"
- "pick up the apple"
- "talk to the guard"
- "attack the enemy"

### Web Interface
A modern web-based interface is available in the `web/` directory:

1. Start the web server:
   ```
   python web/server.py
   ```
2. Open your browser and go to `http://localhost:5000`

See `web/README.md` for more details.

## Project Structure

- `engine/` - Core game engine components
- `data/` - Game data (NPCs, locations, items)
- `scripts/` - CLI entry point and export utility (legacy demos removed)
- `web/` - Web-based interface
- `rpg/` - Combat and RPG mechanics
- `ui/` - Legacy pygame interface (deprecated)

## Architecture

The engine follows a clean architecture pattern with:

1. **Data Models**: Core dataclasses for NPCs, locations, items, etc.
2. **World State**: Manages all game data and deterministic state changes
3. **Event System**: Central mechanism for all world changes
4. **Tools**: Action implementations that validate intents and generate events
5. **Simulator**: Orchestrates the game loop and handles NPC turns
6. **LLM Integration**: For natural language command interpretation and NPC decision making

## Extending the Engine

To add new actions:
1. Create a new tool in `engine/tools/` inheriting from `Tool`
2. Register it with the simulator in `scripts/cli_game.py`
3. Add it to the LLM prompt in `engine/npc_planner.py` if needed

## License

This project is licensed under the MIT License - see the LICENSE file for details.