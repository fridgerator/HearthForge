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
  ~/.hearthforge/memory/{user_id}.json

  Each user gets their own file. The "default" user is used for CLI mode.
  When the web interface is added, each authenticated user gets a user_id
  and their own memory file.

Context window budget:
  Memory is injected into system prompts, so it consumes tokens. The module
  provides a render method that formats memory for prompt injection and
  respects a configurable character limit to avoid blowing the context.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from config import MEMORY_DIR, MEMORY_MAX_HISTORY

logger = logging.getLogger(__name__)


# --- Data Models ---

class RunSummary(BaseModel):
    """Compact summary of a single orchestrator run for history."""
    timestamp: str
    goal: str
    outcome: str                    # "success", "partial", "failed"
    summary: str                    # Brief summary of what was produced
    task_count: int
    completed_count: int
    failed_count: int
    elapsed_seconds: float
    workspace_id: str | None = None  # Reference to workspace for full details

class ToolKnowledgeEntry(BaseModel):
    """A learned fact about an external API or data source."""
    source: str                     # e.g. "coingecko", "yahoo_finance"
    fact: str                       # e.g. "OHLCV endpoint deprecated, use /market_chart/range"
    learned_at: str
    confidence: str = "confirmed"   # "confirmed" or "suspected"

class UserPreference(BaseModel):
    """A user preference, either explicit or inferred."""
    key: str                        # e.g. "stock_analysis_indicators"
    value: str                      # e.g. "Always include RSI, MACD, support/resistance"
    source: str = "explicit"        # "explicit" (user told us) or "inferred" (observed)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class UserMemory(BaseModel):
    """Complete memory state for a single user."""
    user_id: str
    history: list[RunSummary] = Field(default_factory=list)
    preferences: list[UserPreference] = Field(default_factory=list)
    tool_knowledge: list[ToolKnowledgeEntry] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# --- Memory Manager ---

class MemoryManager:
    """
    Manages per-user memory files.

    Usage:
        memory = MemoryManager(user_id="myuser")
        mem = memory.load()
        memory.add_run(result)
        memory.add_tool_knowledge("coingecko", "Use /market_chart/range not /ohlcv/history")
        memory.save()

        # Inject into prompts
        context = memory.render_for_prompt(max_chars=3000)
    """

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.memory_dir = MEMORY_DIR
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._state: UserMemory | None = None

    @property
    def file_path(self) -> Path:
        return self.memory_dir / f"{self.user_id}.json"

    def load(self) -> UserMemory:
        """Load memory from disk, or create empty state if no file exists."""
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text())
                self._state = UserMemory(**data)
                logger.debug(f"Loaded memory for user '{self.user_id}': "
                             f"{len(self._state.history)} history, "
                             f"{len(self._state.preferences)} prefs, "
                             f"{len(self._state.tool_knowledge)} tool facts")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to load memory for '{self.user_id}', starting fresh: {e}")
                self._state = UserMemory(user_id=self.user_id)
        else:
            self._state = UserMemory(user_id=self.user_id)
            logger.debug(f"No memory file for user '{self.user_id}', starting fresh")

        return self._state

    def save(self) -> None:
        """Persist memory to disk."""
        if not self._state:
            return
        self._state.updated_at = datetime.now().isoformat()
        self.file_path.write_text(self._state.model_dump_json(indent=2))
        logger.debug(f"Saved memory for user '{self.user_id}'")

    @property
    def state(self) -> UserMemory:
        if not self._state:
            self.load()
        return self._state

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
        """
        Record a completed run in history.
        Automatically trims to MEMORY_MAX_HISTORY entries.
        """
        # Determine outcome
        if failed_tasks == 0:
            outcome = "success"
        elif completed_tasks > 0:
            outcome = "partial"
        else:
            outcome = "failed"

        # Create a brief summary (first 300 chars of output, truncated at sentence)
        summary = self._make_summary(final_output)

        entry = RunSummary(
            timestamp=datetime.now().isoformat(),
            goal=goal,
            outcome=outcome,
            summary=summary,
            task_count=total_tasks,
            completed_count=completed_tasks,
            failed_count=failed_tasks,
            elapsed_seconds=elapsed_seconds,
            workspace_id=workspace_id,
        )

        self.state.history.append(entry)

        # Trim oldest entries if over limit
        if len(self.state.history) > MEMORY_MAX_HISTORY:
            trimmed = len(self.state.history) - MEMORY_MAX_HISTORY
            self.state.history = self.state.history[-MEMORY_MAX_HISTORY:]
            logger.debug(f"Trimmed {trimmed} old history entries")

        self.save()

    def _make_summary(self, output: str, max_len: int = 300) -> str:
        """Create a brief summary from the full output."""
        if len(output) <= max_len:
            return output.strip()
        # Try to cut at a sentence boundary
        truncated = output[:max_len]
        last_period = truncated.rfind(".")
        if last_period > max_len // 2:
            return truncated[:last_period + 1].strip()
        return truncated.strip() + "..."

    # --- Preferences (persistent) ---

    def set_preference(self, key: str, value: str, source: str = "explicit") -> None:
        """Set or update a user preference. Overwrites if key already exists."""
        # Remove existing with same key
        self.state.preferences = [p for p in self.state.preferences if p.key != key]
        self.state.preferences.append(UserPreference(
            key=key,
            value=value,
            source=source,
        ))
        self.save()
        logger.info(f"Set preference '{key}' for user '{self.user_id}'")

    def get_preference(self, key: str) -> str | None:
        """Get a preference value by key."""
        for pref in self.state.preferences:
            if pref.key == key:
                return pref.value
        return None

    def remove_preference(self, key: str) -> bool:
        """Remove a preference. Returns True if it existed."""
        before = len(self.state.preferences)
        self.state.preferences = [p for p in self.state.preferences if p.key != key]
        removed = len(self.state.preferences) < before
        if removed:
            self.save()
        return removed

    # --- Tool Knowledge (persistent) ---

    def add_tool_knowledge(
        self,
        source: str,
        fact: str,
        confidence: str = "confirmed",
    ) -> None:
        """
        Record a learned fact about an external data source.
        Deduplicates by source+fact combination.
        """
        # Check for duplicate
        for existing in self.state.tool_knowledge:
            if existing.source == source and existing.fact == fact:
                logger.debug(f"Tool knowledge already recorded: {source} - {fact[:50]}")
                return

        self.state.tool_knowledge.append(ToolKnowledgeEntry(
            source=source,
            fact=fact,
            learned_at=datetime.now().isoformat(),
            confidence=confidence,
        ))
        self.save()
        logger.info(f"Added tool knowledge: {source} - {fact[:80]}")

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
        sections = []
        budget_remaining = max_chars

        # Section 1: Tool Knowledge
        if self.state.tool_knowledge:
            lines = ["## Known API/Tool Facts"]
            for tk in self.state.tool_knowledge:
                line = f"- [{tk.source}] {tk.fact}"
                lines.append(line)
            section = "\n".join(lines)
            if len(section) < budget_remaining:
                sections.append(section)
                budget_remaining -= len(section)

        # Section 2: Preferences
        if self.state.preferences:
            lines = ["## User Preferences"]
            for pref in self.state.preferences:
                line = f"- {pref.key}: {pref.value}"
                lines.append(line)
            section = "\n".join(lines)
            if len(section) < budget_remaining:
                sections.append(section)
                budget_remaining -= len(section)

        # Section 3: Recent History (newest first, fit as many as budget allows)
        if self.state.history:
            lines = ["## Recent Task History (newest first)"]
            for run in reversed(self.state.history):
                line = (
                    f"- [{run.timestamp[:16]}] \"{run.goal}\" → {run.outcome} "
                    f"({run.completed_count}/{run.task_count} tasks, {run.elapsed_seconds:.0f}s)"
                    f"\n  Summary: {run.summary[:150]}"
                )
                if len("\n".join(lines + [line])) < budget_remaining:
                    lines.append(line)
                else:
                    break
            if len(lines) > 1:  # More than just the header
                section = "\n".join(lines)
                sections.append(section)

        if not sections:
            return ""

        return "\n\n".join(sections)

    def render_tool_knowledge_for_agent(self) -> str:
        """
        Render just the tool knowledge section for injection into
        the tool agent's system prompt. Kept separate since tool agents
        need this but don't need full history/preferences.
        """
        if not self.state.tool_knowledge:
            return ""

        lines = [
            "## KNOWN ISSUES WITH EXTERNAL APIS (from previous runs):",
            "Use this information to avoid repeating known failures:",
        ]
        for tk in self.state.tool_knowledge:
            lines.append(f"- [{tk.source}] {tk.fact}")

        return "\n".join(lines)
