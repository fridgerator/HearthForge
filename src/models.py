"""
Data models for the orchestrator.
Uses Pydantic for validation and serialization to/from the filesystem.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentType(str, Enum):
    """
    The type of sub-agent to spawn. Each gets a different system prompt.
    This is how we get "specialization" from a single model - the system
    prompt scopes the agent's behavior, not the model itself.
    """
    RESEARCH = "research"
    ANALYZE = "analyze"
    WRITE = "write"
    CODE = "code"
    SUMMARIZE = "summarize"
    TOOL = "tool"


class TaskNode(BaseModel):
    """
    A single node in the task DAG. Represents one unit of work
    that a sub-agent will execute.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str                           # Human-readable task name
    description: str                    # What this task should accomplish
    agent_type: AgentType               # Which specialist prompt to use
    depends_on: list[str] = Field(default_factory=list)  # IDs of prerequisite tasks
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None           # Output from the agent
    error: str | None = None            # Error message if failed
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_ready(self) -> bool:
        return self.status == TaskStatus.PENDING

    @property
    def is_done(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)


class TaskDAG(BaseModel):
    """
    The full task graph. Created by the orchestrator from a user's goal.
    """
    goal: str                           # Original user prompt
    tasks: list[TaskNode]               # All task nodes
    created_at: datetime = Field(default_factory=datetime.now)

    def get_task(self, task_id: str) -> TaskNode | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all completed."""
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        return [
            t for t in self.tasks
            if t.is_ready and all(dep in completed_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.is_done for t in self.tasks)

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.tasks)

    def get_completed_results(self) -> dict[str, str]:
        """Map of task_id -> result for all completed tasks."""
        return {
            t.id: t.result
            for t in self.tasks
            if t.status == TaskStatus.COMPLETED and t.result
        }


class OrchestratorResult(BaseModel):
    """Final output from the orchestrator back to the user."""
    goal: str
    dag: TaskDAG
    final_output: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    elapsed_seconds: float
    run_id: str | None = None
