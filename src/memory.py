"""
Per-user persistent memory for the orchestrator.

Three sections, two retention policies:

ROLLING (count-based, oldest drops off):
  - history: Recent run summaries (goal, outcome, timestamp, task breakdown).
    Keeps the last N entries. Injected into the orchestrator's decomposition
    prompt so it can reference prior work ("update the report from last time").

PERSISTENT (never expires, grows over time):
  - preferences: User-specific patterns and preferences, either explicitly set
    or learned from usage. Things like "prefers concise output", "always include
    RSI in stock analysis", "uses Boglehead investing philosophy".
  - tool_knowledge: Learned facts about external APIs and data sources.
    When a tool agent discovers that an endpoint is deprecated or a specific
    URL pattern works, it gets recorded here so future runs don't repeat
    the same mistakes.

Storage:
  SQLite database at ~/.hearthforge/hearthforge.db

Context window budget:
  Memory is injected into system prompts, so it consumes tokens. The module
  provides a render method that formats memory for prompt injection and
  respects a configurable character limit to avoid blowing the context.
"""

import logging
from datetime import datetime

from pydantic import BaseModel, Field

import database as db
from config import MEMORY_MAX_HISTORY

logger = logging.getLogger(__name__)


# --- Data Models (kept for type safety and prompt rendering) ---

class RunSummary(BaseModel):
    """Compact summary of a single orchestrator run for history."""
    timestamp: str
    goal: str
    outcome: str
    summary: str
    task_count: int
    completed_count: int
    failed_count: int
    elapsed_seconds: float
    workspace_id: str | None = None

class ToolKnowledgeEntry(BaseModel):
    """A learned fact about an external API or data source."""
    source: str
    fact: str
    learned_at: str
    confidence: str = "confirmed"

class UserPreference(BaseModel):
    """A user preference, either explicit or inferred."""
    key: str
    value: str
    source: str = "explicit"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# --- Memory Manager ---

class MemoryManager:
    """
    Manages per-user memory backed by SQLite.

    Usage:
        memory = MemoryManager(user_id="myuser")
        memory.load()  # loads from DB into in-memory state for prompt rendering
        memory.add_run(result)
        memory.add_tool_knowledge("coingecko", "Use /market_chart/range not /ohlcv/history")

        # Inject into prompts
        context = memory.render_for_prompt(max_chars=3000)
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        # In-memory cache for prompt rendering (loaded from DB)
        self._history: list[RunSummary] = []
        self._preferences: list[UserPreference] = []
        self._tool_knowledge: list[ToolKnowledgeEntry] = []
        self._loaded = False

    def load(self):
        """Load memory from SQLite into in-memory state for prompt rendering."""
        # History (newest first from DB, reverse to oldest-first for internal use)
        rows = db.get_history(self.user_id, limit=MEMORY_MAX_HISTORY)
        self._history = [
            RunSummary(
                timestamp=r["timestamp"], goal=r["goal"], outcome=r["outcome"],
                summary=r["summary"], task_count=r["task_count"],
                completed_count=r["completed_count"], failed_count=r["failed_count"],
                elapsed_seconds=r["elapsed_seconds"], workspace_id=r.get("workspace_id"),
            )
            for r in reversed(rows)  # oldest first internally
        ]

        # Preferences
        rows = db.get_preferences(self.user_id)
        self._preferences = [
            UserPreference(key=r["key"], value=r["value"], source=r["source"],
                           created_at=r["created_at"])
            for r in rows
        ]

        # Tool knowledge
        rows = db.get_tool_knowledge(self.user_id)
        self._tool_knowledge = [
            ToolKnowledgeEntry(source=r["source"], fact=r["fact"],
                               learned_at=r["learned_at"], confidence=r["confidence"])
            for r in rows
        ]

        self._loaded = True
        logger.debug(f"Loaded memory for user '{self.user_id}': "
                     f"{len(self._history)} history, "
                     f"{len(self._preferences)} prefs, "
                     f"{len(self._tool_knowledge)} tool facts")
        return self

    def _ensure_loaded(self):
        if not self._loaded:
            self.load()

    # --- Properties for backward compat with orchestrator/server ---

    @property
    def history(self) -> list[RunSummary]:
        self._ensure_loaded()
        return self._history

    @property
    def preferences(self) -> list[UserPreference]:
        self._ensure_loaded()
        return self._preferences

    @property
    def tool_knowledge(self) -> list[ToolKnowledgeEntry]:
        self._ensure_loaded()
        return self._tool_knowledge

    # Compat: orchestrator accesses memory.state.preferences, memory.state.history etc.
    @property
    def state(self):
        self._ensure_loaded()
        return self

    # --- History (rolling) ---

    def add_run(
        self,
        goal: str,
        final_output: str,
        total_tasks: int,
        completed_tasks: int,
        failed_tasks: int,
        elapsed_seconds: float,
        workspace_id: str | None = None,
    ) -> None:
        """Record a completed run in history."""
        if failed_tasks == 0:
            outcome = "success"
        elif completed_tasks > 0:
            outcome = "partial"
        else:
            outcome = "failed"

        summary = self._make_summary(final_output)
        timestamp = datetime.now().isoformat()

        db.add_history(
            self.user_id,
            timestamp=timestamp, goal=goal, outcome=outcome, summary=summary,
            task_count=total_tasks, completed_count=completed_tasks,
            failed_count=failed_tasks, elapsed_seconds=elapsed_seconds,
            workspace_id=workspace_id, max_entries=MEMORY_MAX_HISTORY,
        )

        # Update in-memory cache
        self._history.append(RunSummary(
            timestamp=timestamp, goal=goal, outcome=outcome, summary=summary,
            task_count=total_tasks, completed_count=completed_tasks,
            failed_count=failed_tasks, elapsed_seconds=elapsed_seconds,
            workspace_id=workspace_id,
        ))
        if len(self._history) > MEMORY_MAX_HISTORY:
            self._history = self._history[-MEMORY_MAX_HISTORY:]

        logger.debug(f"Recorded run to memory for user '{self.user_id}'")

    def _make_summary(self, output: str, max_len: int = 300) -> str:
        """Create a brief summary from the full output."""
        if len(output) <= max_len:
            return output.strip()
        truncated = output[:max_len]
        last_period = truncated.rfind(".")
        if last_period > max_len // 2:
            return truncated[:last_period + 1].strip()
        return truncated.strip() + "..."

    # --- Preferences (persistent) ---

    def set_preference(self, key: str, value: str, source: str = "explicit") -> None:
        """Set or update a user preference."""
        db.set_preference(self.user_id, key, value, source)
        # Update in-memory cache
        self._preferences = [p for p in self._preferences if p.key != key]
        self._preferences.append(UserPreference(key=key, value=value, source=source))
        logger.info(f"Set preference '{key}' for user '{self.user_id}'")

    def get_preference(self, key: str) -> str | None:
        """Get a preference value by key."""
        return db.get_preference(self.user_id, key)

    def remove_preference(self, key: str) -> bool:
        """Remove a preference. Returns True if it existed."""
        removed = db.remove_preference(self.user_id, key)
        if removed:
            self._preferences = [p for p in self._preferences if p.key != key]
        return removed

    # --- Tool Knowledge (persistent) ---

    def add_tool_knowledge(
        self,
        source: str,
        fact: str,
        confidence: str = "confirmed",
    ) -> None:
        """Record a learned fact about an external data source."""
        added = db.add_tool_knowledge(self.user_id, source, fact, confidence)
        if added:
            self._tool_knowledge.append(ToolKnowledgeEntry(
                source=source, fact=fact,
                learned_at=datetime.now().isoformat(), confidence=confidence,
            ))
            logger.info(f"Added tool knowledge: {source} - {fact[:80]}")
        else:
            logger.debug(f"Tool knowledge already recorded: {source} - {fact[:50]}")

    # --- Prompt Rendering ---

    def render_for_prompt(self, max_chars: int = 4000) -> str:
        """
        Render memory into a text block suitable for injection into
        system prompts. Respects a character budget to avoid blowing
        the context window.

        Priority order (if budget is tight):
          1. Tool knowledge (most actionable, prevents repeated failures)
          2. Preferences (shapes task planning)
          3. Recent history (provides context for references to past work)
        """
        self._ensure_loaded()
        sections = []
        budget_remaining = max_chars

        # Section 1: Tool Knowledge
        if self._tool_knowledge:
            lines = ["## Known API/Tool Facts"]
            for tk in self._tool_knowledge:
                line = f"- [{tk.source}] {tk.fact}"
                lines.append(line)
            section = "\n".join(lines)
            if len(section) < budget_remaining:
                sections.append(section)
                budget_remaining -= len(section)

        # Section 2: Preferences
        if self._preferences:
            lines = ["## User Preferences"]
            for pref in self._preferences:
                line = f"- {pref.key}: {pref.value}"
                lines.append(line)
            section = "\n".join(lines)
            if len(section) < budget_remaining:
                sections.append(section)
                budget_remaining -= len(section)

        # Section 3: Recent History (newest first, fit as many as budget allows)
        if self._history:
            lines = ["## Recent Task History (newest first)"]
            for run in reversed(self._history):
                line = (
                    f"- [{run.timestamp[:16]}] \"{run.goal}\" → {run.outcome} "
                    f"({run.completed_count}/{run.task_count} tasks, {run.elapsed_seconds:.0f}s)"
                    f"\n  Summary: {run.summary[:150]}"
                )
                if len("\n".join(lines + [line])) < budget_remaining:
                    lines.append(line)
                else:
                    break
            if len(lines) > 1:
                section = "\n".join(lines)
                sections.append(section)

        if not sections:
            return ""

        return "\n\n".join(sections)

    def render_tool_knowledge_for_agent(self) -> str:
        """
        Render just the tool knowledge section for injection into
        the tool agent's system prompt.
        """
        self._ensure_loaded()
        if not self._tool_knowledge:
            return ""

        lines = [
            "## KNOWN ISSUES WITH EXTERNAL APIS (from previous runs):",
            "Use this information to avoid repeating known failures:",
        ]
        for tk in self._tool_knowledge:
            lines.append(f"- [{tk.source}] {tk.fact}")

        return "\n".join(lines)

    # --- Compat: save() is now a no-op (writes go directly to SQLite) ---

    def save(self) -> None:
        pass
