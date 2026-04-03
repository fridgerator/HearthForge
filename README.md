# HearthForge

A self-hosted, single-model agent orchestration system inspired by Perplexity Computer's
architecture. Uses a DAG-based task decomposition pattern with filesystem-based IPC and
a central orchestrator ("brain") that spawns specialist sub-agents at runtime.

## Architecture Overview

```
User Prompt
    │
    ▼
┌──────────────────────────────┐
│       Orchestrator (Brain)   │
│  - Decomposes goal into DAG  │
│  - Manages task lifecycle    │
│  - Resolves dependencies     │
│  - Synthesizes final output  │
└──────────────┬───────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│ Agent  │ │ Agent  │ │ Agent  │    ← spawned at runtime
│research│ │analyze │ │ write  │
└───┬────┘ └───┬────┘ └───┬────┘
    │          │          │
    ▼          ▼          ▼
┌─────────────────────────────┐
│   Shared Filesystem (IPC)   │
│  /tasks/{task_id}/          │
│    ├── input.json           │
│    ├── output.json          │
│    └── status.json          │
└─────────────────────────────┘
        │              │
        ▼              ▼
┌──────────────┐ ┌────────────┐
│   Ollama     │ │  SearXNG   │
│   (Qwen)     │ │  (Search)  │
└──────────────┘ └────────────┘
```

## Core Concepts

- **Orchestrator**: Receives a natural language goal, uses the LLM to decompose it
  into a task DAG, then manages execution of each task node.
- **Task DAG**: A directed acyclic graph where each node is a task with typed
  input/output and dependency edges. Tasks with no unmet dependencies can run
  in parallel (queued to the LLM).
- **Sub-Agents**: Each task node runs as a sub-agent with its own scoped system
  prompt. The orchestrator spawns them, passes context, and collects results.
- **Tool Agent**: A special agent type that writes and executes Python scripts to
  fetch live data from external APIs. Search-native: queries SearXNG for current
  API docs before writing scripts.
- **Filesystem IPC**: Agents communicate through a shared workspace directory.
  Each task writes its output to a known path. Dependent tasks read from their
  dependencies' output paths.
- **Memory**: Per-user persistent memory with rolling history, preferences, and
  learned tool knowledge that feeds back into task planning.
- **Single Model**: Unlike Perplexity's 19-model approach, this uses one model
  (Qwen3:14b) for everything. The "specialization" comes from the system prompt
  and context given to each sub-agent, not from model routing.

## Project Structure

```
hearthforge/
├── src/
│   ├── server.py                # FastAPI web server (main entry point)
│   ├── orchestrator.py          # Brain - goal decomposition + DAG execution
│   ├── agent.py                 # Sub-agent runner - executes a single task via Ollama
│   ├── tool_agent.py            # Tool agent - LLM-generated scripts for data fetching
│   ├── search.py                # SearXNG search client for tool agent
│   ├── dag.py                   # DAG data structures and topological execution
│   ├── ollama_client.py         # Async Ollama HTTP client
│   ├── prompts.py               # System prompts for orchestrator and agent types
│   ├── models.py                # Pydantic models for tasks, DAGs, agent configs
│   ├── memory.py                # Per-user persistent memory system
│   ├── auth.py                  # User authentication (JWT + bcrypt)
│   ├── workspace.py             # Filesystem workspace manager (IPC layer)
│   ├── config.py                # Configuration (Ollama URL, model, paths)
│   └── main.py                  # CLI entrypoint
├── static/
│   └── index.html           # Web UI (single-file, no build step)
├── searxng-config/
│   └── settings.yml         # SearXNG configuration
├── docker-compose.searxng.yml  # Docker compose for SearXNG
└── requirements.txt
```

## Requirements

- Python 3.11+
- Ollama running with Qwen3:14b (or any model)
- Docker (for SearXNG search)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start SearXNG (for search-native tool agent)
docker compose -f docker-compose.searxng.yml up -d

# 3. Set your Ollama endpoint
export OLLAMA_HOST=http://<your-ollama-ip>:11434

# 4a. Run the web server
python src/server.py
# → http://localhost:8742

# 4b. Or use the CLI
python src/main.py --user myuser "Research the top 3 Python async frameworks and write a summary"
python src/main.py --user myuser --interactive
```

## Configuration

All settings are configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen3:14b` | Model to use |
| `HEARTHFORGE_WORKSPACE` | `/tmp/hearthforge` | Workspace root for task IPC |
| `HEARTHFORGE_MAX_CONCURRENT` | `3` | Max parallel agent tasks |
| `HEARTHFORGE_MAX_RETRIES` | `3` | Retry attempts per task |
| `HEARTHFORGE_MEMORY_DIR` | `~/.hearthforge/memory` | Per-user memory storage |
| `HEARTHFORGE_HOST` | `0.0.0.0` | Server bind address |
| `HEARTHFORGE_PORT` | `8742` | Server port |
| `HEARTHFORGE_SEARXNG_URL` | `http://localhost:8080` | SearXNG instance URL |
| `HEARTHFORGE_SEARCH_FIRST` | `true` | Search before writing tool scripts |
| `HEARTHFORGE_JWT_SECRET` | (dev default) | **Change this in production** |
