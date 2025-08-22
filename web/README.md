# Web UI

The previous browser UI has been intentionally removed to unblock a clean redesign.

What remains intact:

- Flask/Socket.IO server (`web/server.py`) and all REST endpoints
- World/simulator/engine functionality

You can still interact via:

- GET `/api/state`
- GET `/api/locations`
- GET `/api/actors`
- POST `/api/action` { "action": { tool, params } }

Temporary page at `web/static/index.html` lists these.

Rebuild plan (sketch):

- `ui/` SPA with a modular renderer (hex grid, editor tools, inspector)
- Decouple layout math from rendering
- Deterministic snapping and meta-grid overlay

# Living Tapestry - Web Interface

This is a web-based interface for the Living Tapestry RPG engine, replacing the previous pygame hexagonal map interface.

## Features

- Modern web-based interface that works on any device with a browser
- Real-time game state updates using WebSockets
- Natural language command input parsed by LLM
- Visual hexagonal map representation
- Player status panel with HP, hunger, inventory, and equipment
- Location details showing occupants and items
- Game log showing events and responses
- Responsive design that works on desktop and mobile

## How It Works

The web interface uses the LLM to parse natural language commands into game actions:

1. Player types a natural language command like "go to the market square"
2. The LLM parses this into a structured command like `{"tool": "move", "params": {"target_location": "market_square"}}`
3. The command is sent to the game engine which executes it
4. The game state is updated and sent back to all connected clients

This allows for rich, dynamic interactions while maintaining the deterministic core of the engine.

## Installation

1. Make sure you have Python 3.7+ installed
2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Running the Web Server

### Windows:
Double-click on `start_web_server.bat` or run:
```
python web/server.py
```

### Linux/Mac:
Run:
```
chmod +x start_web_server.sh
./start_web_server.sh
```

Or directly:
```
python web/server.py
```

## Accessing the Interface

Once the server is running, open your browser and go to:
```
http://localhost:5000
```

## Using the Interface

### Natural Language Commands
Enter natural language commands in the input box:
- `go to the market square` - Move to a location
- `look around` - Examine your surroundings
- `pick up the apple` - Pick up an item
- `check my inventory` - Check your inventory
- `equip the rusty sword in my main hand` - Equip an item
- `attack the enemy` - Attack an NPC
- `talk to the guard: Hello there!` - Talk to an NPC
- `wait a moment` - Wait for a turn

### Map Navigation
Click on location names in the hex grid to quickly enter movement commands.

### Examples Panel
The examples panel on the right shows common command patterns to help you get started.

## Development

The web interface consists of:
- `web/server.py` - Flask backend that serves game state and handles actions
- `web/static/index.html` - Main HTML page with CSS and JavaScript
- `start_web_server.bat/.sh` - Launch scripts for Windows/Linux-Mac

## Architecture

The web interface communicates with the game engine through:
1. REST API endpoints for initial state and actions
2. WebSocket connections for real-time state updates
3. LLM parsing endpoint for natural language command interpretation

All game logic remains in the core engine - the web interface is just a presentation layer that leverages the LLM for natural language understanding.