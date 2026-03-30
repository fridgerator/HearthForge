"""
Filesystem-based workspace for inter-agent communication.

Inspired by Perplexity Computer's filesystem IPC pattern:
each task gets a directory, agents write outputs as files,
dependent agents read from their dependencies' directories.

This makes everything inspectable and debuggable - you can
literally `cat` any task's output to see what happened.

Workspace layout:
  /tmp/hearthforge/
    └── {run_id}/
        ├── goal.txt              # Original user prompt
        ├── dag.json              # Full task DAG
        ├── tasks/
        │   ├── t1/
        │   │   ├── input.json    # Task definition + dependency outputs
        │   │   ├── output.txt    # Agent's response
        │   │   └── status.json   # Status, timing, errors
        │   ├── t2/
        │   │   └── ...
        │   └── ...
        └── result.md             # Final synthesized output
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from config import WORKSPACE_ROOT
from models import TaskDAG, TaskNode, TaskStatus


class Workspace:
    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S_") + str(uuid.uuid4())[:6]
        self.root = WORKSPACE_ROOT / self.run_id
        self.tasks_dir = self.root / "tasks"

    def initialize(self, goal: str, dag: TaskDAG) -> None:
        """Create the workspace directory structure."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(exist_ok=True)

        # Write the original goal
        (self.root / "goal.txt").write_text(goal)

        # Write the full DAG
        (self.root / "dag.json").write_text(dag.model_dump_json(indent=2))

        # Create a directory for each task
        for task in dag.tasks:
            task_dir = self.tasks_dir / task.id
            task_dir.mkdir(exist_ok=True)
            self._write_status(task)

    def write_task_input(self, task: TaskNode, dependency_outputs: dict[str, str]) -> None:
        """Write the input context for a task (its definition + dependency results)."""
        task_dir = self.tasks_dir / task.id
        input_data = {
            "task": task.model_dump(mode="json"),
            "dependency_outputs": dependency_outputs,
        }
        (task_dir / "input.json").write_text(json.dumps(input_data, indent=2))

    def write_task_output(self, task: TaskNode) -> None:
        """Write a task's output and update its status file."""
        task_dir = self.tasks_dir / task.id
        if task.result:
            (task_dir / "output.txt").write_text(task.result)
        self._write_status(task)

    def read_task_output(self, task_id: str) -> str | None:
        """Read a task's output (used by dependent tasks)."""
        output_file = self.tasks_dir / task_id / "output.txt"
        if output_file.exists():
            return output_file.read_text()
        return None

    def write_final_result(self, result: str) -> None:
        """Write the final synthesized output."""
        (self.root / "result.md").write_text(result)

    def _write_status(self, task: TaskNode) -> None:
        """Write task status metadata."""
        task_dir = self.tasks_dir / task.id
        status = {
            "id": task.id,
            "name": task.name,
            "status": task.status.value,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "error": task.error,
        }
        (task_dir / "status.json").write_text(json.dumps(status, indent=2))

    @property
    def result_path(self) -> Path:
        return self.root / "result.md"
