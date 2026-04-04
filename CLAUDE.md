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
- **Database** (`src/database.py`): SQLite storage layer at `~/.hearthforge/hearthforge.db`. Handles users, memory (history/preferences/tool knowledge), conversations, and job persistence. WAL mode for concurrent safety. Short-lived connections per operation. Key tables: `conversations` (thread of jobs), `jobs` (requires `conversation_id`), `memory_history`, `memory_preferences`, `memory_tool_knowledge`.
- **Memory** (`src/memory.py`): Per-user memory backed by SQLite. Three sections: rolling history (last N runs), persistent preferences, persistent tool knowledge. Loads into in-memory cache for prompt rendering. Injected into orchestrator/tool prompts.
- **Server** (`src/server.py`): FastAPI with async job execution, JWT auth, REST API for conversations/jobs/memory/preferences/workspaces. Jobs persist to SQLite (survive server restarts). Serves the built React frontend from `frontend/dist/` with SPA fallback. Conversation endpoints: `POST /api/conversations`, `GET /api/conversations`, `GET /api/conversations/:id/jobs`.
- **Frontend** (`frontend/`): React + Vite + TypeScript SPA. Chat-first UI — input pinned at bottom, scrolling message log above, collapsible task execution details per response. Auth state via React Context, server data via TanStack Query (React Query v5). React Router v6 for client-side navigation. Proxies `/api` to the FastAPI server in dev mode (`npm run dev`).
  - `ChatPage.tsx` — primary view at `/chat/:conversationId`; creates a conversation on first message, loads all jobs in a conversation on route, submits each message as a job with `conversation_id`
  - `ChatMessage.tsx` — user bubble (right) / assistant bubble (left) with loading/error/completed states
  - `ChatInput.tsx` — auto-resizing textarea, Enter to send, disabled while job is running
  - `TaskDetails.tsx` — collapsible block inside each assistant message; fetches workspace data lazily on expand
  - `Layout.tsx` — sidebar with "New Chat" button and recent conversations list

**Filesystem IPC pattern:** Agents communicate via workspace directories (`/tmp/hearthforge/{run_id}/tasks/{task_id}/`). Each task writes output.txt, status.json, and optionally script files and attempt logs. This stays as filesystem (not SQLite) — write-once semantics with no concurrency conflicts, and the file layout is a debugging feature.

**Data migration:** On first startup after the SQLite change, the server automatically imports existing `users.json` and `memory/*.json` into the database. Can also be run manually via `python src/migrate.py` (supports `--dry-run`).

## Tech Stack

- Python 3.11+, FastAPI, Pydantic, httpx
- Ollama (Qwen3:14b) for all LLM calls
- SearXNG (Docker) for web search
- SQLite (WAL mode) for memory, auth, and job persistence (`~/.hearthforge/hearthforge.db`)
- bcrypt + JWT for auth
- React 18 + Vite + TypeScript (frontend)
- TanStack Query v5 (server state), React Context (auth), React Router v6 (routing)

## Frontend Development

```bash
cd frontend
npm install
npm run dev      # dev server at :5173, proxies /api to :8742
npm run build    # builds to frontend/dist/ (served by FastAPI in production)
```

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
- WebSocket or SSE for live job progress instead of polling
- Docker/E2B sandboxing for tool script execution (security)

**Web UI enhancements:**
- Scheduled tasks page (create, manage, view history)
- Real-time task progress (show which sub-agent is running, not just "running...")
- Mobile responsive improvements

## Key Design Decisions

- **Single model, many agents**: Unlike Perplexity Computer's 19-model approach, HearthForge uses one model for everything. This simplifies deployment but means agent quality depends heavily on prompt engineering.
- **Search-native tool agent**: Tool tasks search SearXNG before writing scripts by default (`HEARTHFORGE_SEARCH_FIRST=true`). Always searches on retries regardless of this setting.
- **SQLite for persistent state, filesystem for workspace IPC**: Memory, auth, and jobs use SQLite (ACID, concurrent-safe). Workspace task outputs stay as files — write-once with no concurrency conflicts, and the directory layout is useful for debugging. Trade-off is workspaces won't scale to thousands of concurrent runs, but that's fine for a home lab.
- **Per-user everything**: Memory, preferences, tool knowledge, and jobs are all scoped to a user_id. Multi-user from day one.
- **Graceful degradation**: If SearXNG is down, tool agent falls back to training knowledge. If a tool script fails, retries with error context. If all retries fail, downstream tasks get skipped and the synthesis works with whatever succeeded.
- **Conversations group jobs into threads**: Every job belongs to a `conversation_id`. The frontend creates a conversation on the first message in a new chat, then submits all follow-up messages as jobs in that conversation. Route is `/chat/:conversationId`. Sidebar lists conversations (not individual jobs).
- **Conversational context via prompt injection**: `Orchestrator.run()` receives the last 3 completed jobs in the conversation as `{goal, summary}` pairs (150-char summaries of `final_output`). These are prepended to both the decomposition and synthesis prompts so the LLM can resolve follow-ups like "now do the same for ETH". Approach is text injection, not multi-turn LLM message arrays.
- **Chat-first UI, jobs as messages**: Each submitted goal is a job; the frontend presents jobs as a chat conversation. User messages appear right-aligned, assistant responses left-aligned. Task execution details (DAG, per-agent output, errors) are collapsed inside each assistant message and fetched lazily.
- **`run_id` on OrchestratorResult**: The workspace `run_id` is returned on every completed job so the frontend can fetch task-level details without a separate lookup.