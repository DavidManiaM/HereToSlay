Always generate a commit message after implementing something big / important. The commit message should be a concise summary of the change and should not contain any double quotes.

# Role & Objective
You are an expert Python software engineer and game architect. Your task is to design and implement a complex, fully functional digital adaptation of the card/board game "Here to Slay" using **PyGame**.

# Core Architectural Constraint: Extreme Modifiability
The absolute highest priority for this project is **modifiability**. I will be creating a heavily modified variant of this game later, introducing new rules, custom card types, and altered win conditions. 

Therefore, you must strictly avoid hardcoding game logic. You are required to design a highly modular and extensible architecture:
- **Data-Driven Design:** All cards (Leaders, Heroes, Monsters, Items, Magic, Modifiers) must be defined in external configuration files (JSON or YAML). The code should only parse and interpret these files.
- **Rules Engine:** Implement a flexible rules engine. Use design patterns like the Strategy pattern for evaluating conditions, and an Event Bus / Observer pattern for game actions so that cards can easily interrupt or modify events (e.g., playing a Modifier or Challenge card).
- **Decoupled UI:** Strictly separate the PyGame rendering/input layer from the core game state and logic. The core game should ideally be playable head-to-head via terminal before PyGame is even attached.

# Environment & Tooling
- You must use **`uv`** for all Python dependency management, virtual environment setup, and script execution. Do not use standard `pip` or `venv` directly.

# Memory & Context Management
Because this is a complex, multi-step project, you must actively manage your own context so you don't lose track of the architecture as the codebase grows. 
Before writing any actual game logic:
1. Create a `docs/` (or `_memory/`) directory in the project root.
2. Create and continuously update detailed Markdown files to store architectural decisions, constraints, and progress. At a minimum, generate:
   - `architecture_notes.md`: High-level system design (State Machine for turn phases, Event Bus structure, separation of concerns).
   - `card_schemas.md`: The exact JSON/YAML schemas (consider using `pydantic` for validation) defining how cards and their specific effects are structured.
   - `rules_engine.md`: Documentation on how the game loop works, how win conditions are checked, and how modifiers/challenges intercept the event queue.
   - `build_plan.md`: A granular, step-by-step implementation roadmap divided into logical phases (e.g., Core State -> Event System -> CLI loop -> PyGame UI -> Card Implementation).

# Immediate Next Steps
1. Initialize the Python project using `uv init` and add `pygame` (plus any other architecture-supporting libraries you deem necessary, like `pydantic`).
2. Create the `docs/` directory and draft the foundational markdown files mentioned above.
3. Create the `build_plan.md` and the core ideas from `architecture_notes.md'.
4. **Pause.** Wait for my feedback and approval on the architecture and build plan before you begin writing the core Python game source code.