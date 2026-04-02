"""
Sub-agent executor.

Each task in the DAG gets executed by a sub-agent with:
  1. A specialist system prompt (based on agent_type)
  2. The task description
  3. Outputs from dependency tasks (context)

The agent calls Ollama, gets a response, and writes it
to the filesystem workspace. EXCEPT for "tool" type tasks,
which are routed to the ToolAgent for script generation + execution.

Both tool and non-tool tasks support retries with error context.
"""

import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

from config import AGENT_TEMPERATURES, MAX_RETRIES
from models import AgentType, TaskNode, TaskStatus

# Agent types where Qwen3 thinking mode wastes tokens without benefit.
# Write/summarize tasks just reformat or condense existing content — they
# don't need chain-of-thought. Analyze is excluded because it genuinely
# needs reasoning to interpret data and extract insights.
NO_THINK_AGENTS = {AgentType.WRITE, AgentType.SUMMARIZE}
from ollama_client import OllamaClient
from prompts import get_agent_prompt
from tool_agent import ToolAgent

logger = logging.getLogger(__name__)


class SubAgent:
    def __init__(self, client: OllamaClient, memory=None, search_client=None):
        self.client = client
        self.memory = memory
        self.tool_agent = ToolAgent(client, memory=memory, search_client=search_client)

    async def execute(
        self,
        task: TaskNode,
        dependency_outputs: dict[str, str],
        workspace_task_dir: Path | None = None,
    ) -> TaskNode:
        """
        Execute a single task. Routes to ToolAgent for tool-type tasks,
        otherwise calls the LLM directly with the specialist prompt.
        Both paths support retries.
        """
        # Route tool tasks to the ToolAgent (has its own retry logic)
        if task.agent_type == AgentType.TOOL:
            return await self.tool_agent.execute(
                task, dependency_outputs, workspace_task_dir
            )

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        logger.info(f"  ▶ [{task.id}] Starting: {task.name} (type={task.agent_type})")

        # Build the user message with task context
        user_message = self._build_user_message(task, dependency_outputs)

        # Get the specialist system prompt
        system_prompt = get_agent_prompt(task.agent_type)

        # Get temperature for this agent type
        temperature = AGENT_TEMPERATURES.get(
            task.agent_type, AGENT_TEMPERATURES["default"]
        )

        # Retry loop for LLM-based tasks
        attempts: list[dict] = []
        last_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            attempt_log = {
                "attempt": attempt,
                "timestamp": datetime.now().isoformat(),
                "error": None,
                "success": False,
            }

            try:
                if attempt > 1:
                    logger.info(f"  ↻ [{task.id}] Retry {attempt}/{MAX_RETRIES}...")

                result = await self.client.chat(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    temperature=temperature,
                )

                # Basic validation - empty responses are a failure
                if not result or not result.strip():
                    last_error = "LLM returned an empty response"
                    attempt_log["error"] = last_error
                    attempts.append(attempt_log)
                    logger.warning(f"  ⚠ [{task.id}] Attempt {attempt}: empty response")
                    continue

                task.result = result
                task.status = TaskStatus.COMPLETED
                attempt_log["success"] = True
                attempts.append(attempt_log)

                if attempt > 1:
                    logger.info(f"  ✓ [{task.id}] Completed on retry {attempt}: {task.name} ({len(result)} chars)")
                else:
                    logger.info(f"  ✓ [{task.id}] Completed: {task.name} ({len(result)} chars)")
                break

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                attempt_log["error"] = last_error
                attempts.append(attempt_log)
                logger.warning(f"  ⚠ [{task.id}] Attempt {attempt} failed: {e}")
                continue

        else:
            # All retries exhausted
            task.status = TaskStatus.FAILED
            task.error = f"All {MAX_RETRIES} attempts failed.\nLast error: {last_error}"
            logger.error(f"  ✗ [{task.id}] Failed after {MAX_RETRIES} attempts: {task.name}")

        # Write attempt log to workspace for debugging
        if workspace_task_dir and (task.status == TaskStatus.FAILED or len(attempts) > 1):
            self._write_attempt_log(workspace_task_dir, task, attempts)

        task.completed_at = datetime.now()
        return task

    def _build_user_message(
        self,
        task: TaskNode,
        dependency_outputs: dict[str, str],
    ) -> str:
        """
        Build the user message that the sub-agent receives.
        Includes the task description and any upstream results.
        """
        prefix = "/no_think\n" if task.agent_type in NO_THINK_AGENTS else ""
        parts = [f"{prefix}## Task: {task.name}\n\n{task.description}"]

        if dependency_outputs:
            parts.append("\n\n## Context from prior tasks:\n")
            for dep_id, output in dependency_outputs.items():
                parts.append(f"### Results from task [{dep_id}]:\n{output}\n")

        return "\n".join(parts)

    def _write_attempt_log(
        self,
        workspace_dir: Path,
        task: TaskNode,
        attempts: list[dict],
    ) -> None:
        """Write attempt log for failed or retried tasks."""
        log = {
            "task_id": task.id,
            "task_name": task.name,
            "agent_type": task.agent_type.value,
            "final_status": task.status.value,
            "final_error": task.error,
            "total_attempts": len(attempts),
            "attempts": attempts,
        }

        log_path = workspace_dir / "attempts.json"
        log_path.write_text(json.dumps(log, indent=2))
        logger.debug(f"  📝 [{task.id}] Attempt log saved to {log_path}")
