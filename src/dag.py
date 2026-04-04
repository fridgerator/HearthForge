"""
DAG execution engine.

Handles the core orchestration loop:
  1. Find tasks whose dependencies are all satisfied
  2. Run them (with concurrency limit since we share one GPU)
  3. Write results to workspace
  4. Repeat until DAG is complete

Uses asyncio.Semaphore to limit concurrent Ollama requests.
With a single GPU (your 5070), you probably want MAX_CONCURRENT_AGENTS=1-3
depending on how Ollama handles concurrent contexts with Qwen3:14b.
"""

import asyncio
import logging
from datetime import datetime

from agent import SubAgent
from config import MAX_CONCURRENT_AGENTS
from models import TaskDAG, TaskNode, TaskStatus
from workspace import Workspace

logger = logging.getLogger(__name__)


class DAGExecutor:
    def __init__(
        self,
        dag: TaskDAG,
        agent: SubAgent,
        workspace: Workspace,
        max_concurrent: int = MAX_CONCURRENT_AGENTS,
    ):
        self.dag = dag
        self.agent = agent
        self.workspace = workspace
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._running_tasks: set[str] = set()

    async def execute(self) -> TaskDAG:
        """
        Execute the full DAG, respecting dependencies and concurrency limits.
        Returns the DAG with all tasks populated with results.
        """
        logger.info(f"Executing DAG with {len(self.dag.tasks)} tasks (max_concurrent={self.semaphore._value})")

        while not self.dag.is_complete():
            ready = self._get_launchable_tasks()

            if not ready and not self._running_tasks:
                # Nothing ready, nothing running - we're stuck
                pending = [t for t in self.dag.tasks if t.status == TaskStatus.PENDING]
                if pending:
                    logger.error(f"DAG is stuck: {len(pending)} pending tasks with unmet dependencies")
                    for t in pending:
                        t.status = TaskStatus.SKIPPED
                        t.error = "Skipped due to unresolvable dependencies"
                        self.workspace.write_task_output(t)
                break

            if ready:
                # Launch all ready tasks concurrently (bounded by semaphore)
                await asyncio.gather(
                    *[self._run_task(task) for task in ready]
                )
            else:
                # Tasks are running but none are ready yet, wait a beat
                await asyncio.sleep(0.1)

        return self.dag

    def _get_launchable_tasks(self) -> list[TaskNode]:
        """Get tasks that are ready to run and not already running."""
        ready = self.dag.get_ready_tasks()
        return [t for t in ready if t.id not in self._running_tasks]

    async def _run_task(self, task: TaskNode) -> None:
        """Run a single task with semaphore-bounded concurrency."""
        self._running_tasks.add(task.id)

        async with self.semaphore:
            # Gather outputs from dependency tasks, keyed by task name
            # so the downstream agent knows what each result represents
            # (e.g., "Fetch NVDA price data" instead of "t1")
            dep_outputs = {}
            task_lookup = {t.id: t for t in self.dag.tasks}
            for dep_id in task.depends_on:
                output = self.workspace.read_task_output(dep_id)
                if output:
                    dep_task = task_lookup.get(dep_id)
                    label = dep_task.name if dep_task else dep_id
                    dep_outputs[label] = output

            # Write the input context to workspace (for debugging)
            self.workspace.write_task_input(task, dep_outputs)

            # Pass the task's workspace directory so tool agents can save scripts
            task_dir = self.workspace.tasks_dir / task.id

            # Execute the sub-agent
            await self.agent.execute(task, dep_outputs, workspace_task_dir=task_dir)

            # Write output to workspace (so dependents can read it)
            self.workspace.write_task_output(task)

        self._running_tasks.discard(task.id)
