# HearthForge

## What This Is

HearthForge is a self-hosted agent orchestration system inspired by Perplexity Computer's architecture. It runs entirely on local hardware using Ollama (Qwen3:14b default) with no cloud dependencies.

A user describes a goal in natural language → a central "brain" decomposes it into a task DAG → specialist sub-agents are spawned at runtime to execute each task → a synthesis agent combines results into a final response.

## Architecture

```
User Goal → Orchestrator (brain) → Task DAG → Sub-Agents → Workspace (filesystem IPC) → Synthesis → Result
```

**Key components:**

- **Orchestrator** (`src/orchestrator.py`): Decomposes goals into a DAG via LLM, manages execution, records to memory, auto-learns tool knowledge from failures.
- **DAG Executor** (`src/dag.py`): Topological execution with asyncio semaphore-bounded concurrency. Resolves dependencies, runs independent tasks in parallel.
- **Sub-Agents** (`src/agent.py`): Routes tasks by agent_type (research, analyze, write, code, summarize, tool). Each gets a scoped system prompt — specialization comes from the prompt, not the model.
- **Tool Agent** (`src/tool_agent.py`): Writes and executes Python scripts to fetch live external data. Search-native: queries SearXNG for current API docs before writing scripts. Retries with error feedback — the LLM sees its previous script + the error and rewrites.
- **Search Client** (`src/search.py`): Async SearXNG client. Uses LLM to formulate search queries from task descriptions, with heuristic fallback.
- **Memory** (`src/memory.py`): Per-user JSON files at `~/.hearthforge/memory/{user_id}.json`. Three sections: rolling history (last N runs), persistent preferences, persistent tool knowledge. Injected into orchestrator/tool prompts.
- **Server** (`src/server.py`): FastAPI with async job execution, JWT auth, REST API for jobs/memory/preferences/workspaces.
- **Web UI** (`src/static/index.html`): Single-file vanilla JS frontend. Login, submit tasks, poll job progress, browse results, manage preferences/memory.

**Filesystem IPC pattern:** Agents communicate via workspace directories (`/tmp/hearthforge/{run_id}/tasks/{task_id}/`). Each task writes output.txt, status.json, and optionally script files and attempt logs. This makes everything inspectable and debuggable.

## Tech Stack

- Python 3.11+, FastAPI, Pydantic, httpx
- Ollama (Qwen3:14b) for all LLM calls
- SearXNG (Docker) for web search
- bcrypt + JWT for auth
- No databases — JSON files for memory/auth, filesystem for workspace IPC

## Code Conventions

- All config via environment variables with `HEARTHFORGE_` prefix (see `src/config.py`)
- Pydantic models for all data structures
- Async throughout (httpx for Ollama, asyncio for concurrency)
- Minimal dependencies — no LangChain, no agent frameworks
- Single model architecture: "specialization" via system prompts, not model routing
- Tool agent scripts restricted to stdlib + requests (executed in subprocess)

## Roadmap / Next Steps

**Scheduler** (next priority):
- Background task runner inside the FastAPI server lifespan
- Cron-based triggers for recurring tasks (e.g., daily BTC analysis, morning briefings)
- Store scheduled task definitions (goal, schedule, user_id, enabled/disabled)
- Visible in the web UI — create, edit, enable/disable, view run history
- Memory integration so scheduled tasks can reference their own previous runs

**Improve tool agent search accuracy:**
- SearXNG query formulation needs tuning — agents still frequently fail to find working API endpoints
- Consider fetching and parsing actual API documentation pages (web_fetch after search)
- May need to expand the tool agent prompt with more explicit guidance on common API patterns
- Evaluate whether to add curated API endpoint configs for commonly used sources (Yahoo Finance, CoinGecko, etc.)

**Infrastructure improvements:**
- SQLite for memory/auth/jobs instead of JSON files (concurrent write safety)
- WebSocket or SSE for live job progress instead of polling
- Docker/E2B sandboxing for tool script execution (security)
- Job persistence across server restarts (currently in-memory only)

**Web UI enhancements:**
- Scheduled tasks page (create, manage, view history)
- Real-time task progress (show which sub-agent is running, not just "running...")
- Markdown rendering for final output
- Mobile responsive improvements

## Key Design Decisions

- **Single model, many agents**: Unlike Perplexity Computer's 19-model approach, HearthForge uses one model for everything. This simplifies deployment but means agent quality depends heavily on prompt engineering.
- **Search-native tool agent**: Tool tasks search SearXNG before writing scripts by default (`HEARTHFORGE_SEARCH_FIRST=true`). Always searches on retries regardless of this setting.
- **Filesystem IPC over message queues**: Agents write to disk. Simple, debuggable, inspectable. Trade-off is it won't scale to thousands of concurrent runs, but that's fine for a home lab.
- **Per-user everything**: Memory, preferences, tool knowledge, and jobs are all scoped to a user_id. Multi-user from day one.
- **Graceful degradation**: If SearXNG is down, tool agent falls back to training knowledge. If a tool script fails, retries with error context. If all retries fail, downstream tasks get skipped and the synthesis works with whatever succeeded.