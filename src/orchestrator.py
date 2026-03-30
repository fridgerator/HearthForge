"""
The Orchestrator ("Brain").

This is the central coordinator that:
  1. Takes a natural language goal from the user
  2. Loads user memory (history, preferences, tool knowledge)
  3. Uses the LLM to decompose it into a task DAG (with memory context)
  4. Executes the DAG via the DAGExecutor
  5. Synthesizes final output from all task results
  6. Records the run to memory and learns from tool failures

This mirrors Perplexity Computer's pattern but with a single model.
The "intelligence" of orchestration comes from the LLM itself deciding
how to break down the work, not from hardcoded routing rules.
"""

import logging
import time
from datetime import datetime

from agent import SubAgent
from config import MEMORY_PROMPT_BUDGET
from dag import DAGExecutor
from memory import MemoryManager
from models import AgentType, OrchestratorResult, TaskDAG, TaskNode, TaskStatus
from ollama_client import OllamaClient
from prompts import DECOMPOSE_SYSTEM_PROMPT, SYNTHESIZE_SYSTEM_PROMPT
from search import SearchClient
from workspace import Workspace

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, client: OllamaClient, user_id: str = "default"):
        self.client = client
        self.user_id = user_id
        self.memory = MemoryManager(user_id=user_id)
        self.memory.load()
        self.search = SearchClient()
        self.agent = SubAgent(client, memory=self.memory, search_client=self.search)

    async def run(self, goal: str) -> OrchestratorResult:
        """
        Main entry point. Takes a goal, returns a result.
        """
        start_time = time.time()

        # Step 1: Decompose the goal into a task DAG (with memory context)
        logger.info("=" * 60)
        logger.info(f"Goal: {goal}")
        logger.info(f"User: {self.user_id}")
        logger.info("=" * 60)
        logger.info("\n📋 Step 1: Decomposing goal into task DAG...")

        dag = await self._decompose_goal(goal)

        logger.info(f"   Created {len(dag.tasks)} tasks:")
        for task in dag.tasks:
            deps = f" (depends on: {', '.join(task.depends_on)})" if task.depends_on else ""
            logger.info(f"   • [{task.id}] {task.name} ({task.agent_type}){deps}")

        # Step 2: Set up workspace
        workspace = Workspace()
        workspace.initialize(goal, dag)
        logger.info(f"\n📁 Workspace: {workspace.root}")

        # Step 3: Execute the DAG
        logger.info("\n🚀 Step 2: Executing task DAG...")
        executor = DAGExecutor(dag, self.agent, workspace)
        dag = await executor.execute()

        # Step 4: Synthesize final output (with memory context)
        logger.info("\n📝 Step 3: Synthesizing final output...")
        final_output = await self._synthesize(goal, dag)
        workspace.write_final_result(final_output)

        elapsed = time.time() - start_time
        completed = sum(1 for t in dag.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in dag.tasks if t.status == TaskStatus.FAILED)

        logger.info(f"\n✅ Done in {elapsed:.1f}s ({completed}/{len(dag.tasks)} tasks completed)")
        logger.info(f"   Result: {workspace.result_path}")

        result = OrchestratorResult(
            goal=goal,
            dag=dag,
            final_output=final_output,
            total_tasks=len(dag.tasks),
            completed_tasks=completed,
            failed_tasks=failed,
            elapsed_seconds=elapsed,
        )

        # Step 5: Record to memory and learn from tool outcomes
        self._record_to_memory(result, workspace.run_id)
        self._learn_from_tool_results(dag)

        return result

    async def _decompose_goal(self, goal: str) -> TaskDAG:
        """
        Use the LLM to break a goal into a task DAG.
        Memory context is injected so the planner knows about past runs,
        user preferences, and known API issues.
        """
        # Build the decomposition prompt with memory context
        system_prompt = DECOMPOSE_SYSTEM_PROMPT
        memory_context = self.memory.render_for_prompt(max_chars=MEMORY_PROMPT_BUDGET)
        if memory_context:
            system_prompt += (
                "\n\n## MEMORY CONTEXT (from this user's previous interactions):\n"
                "Use this to inform your task planning. Reference past work when relevant.\n"
                "Apply user preferences to how you structure tasks.\n"
                "Avoid known API issues listed in tool knowledge.\n\n"
                f"{memory_context}"
            )

        try:
            data = await self.client.chat_json(
                system_prompt=system_prompt,
                user_message=f"Decompose this goal into tasks:\n\n{goal}",
                temperature=0.3,
            )

            tasks = []
            for t in data.get("tasks", []):
                tasks.append(TaskNode(
                    id=t["id"],
                    name=t["name"],
                    description=t["description"],
                    agent_type=AgentType(t["agent_type"]),
                    depends_on=t.get("depends_on", []),
                ))

            if not tasks:
                raise ValueError("LLM returned empty task list")

            # Validate: check for missing dependency references
            task_ids = {t.id for t in tasks}
            for task in tasks:
                bad_deps = [d for d in task.depends_on if d not in task_ids]
                if bad_deps:
                    logger.warning(
                        f"Task [{task.id}] references unknown dependencies: {bad_deps}. Removing them."
                    )
                    task.depends_on = [d for d in task.depends_on if d in task_ids]

            return TaskDAG(goal=goal, tasks=tasks)

        except (ValueError, KeyError) as e:
            logger.error(f"Failed to decompose goal: {e}")
            logger.info("Falling back to simple two-task pipeline")
            return TaskDAG(
                goal=goal,
                tasks=[
                    TaskNode(
                        id="t1",
                        name="Research",
                        description=f"Research and address this goal thoroughly: {goal}",
                        agent_type=AgentType.RESEARCH,
                    ),
                    TaskNode(
                        id="t2",
                        name="Summarize",
                        description="Summarize the research findings into a clear, actionable response.",
                        agent_type=AgentType.SUMMARIZE,
                        depends_on=["t1"],
                    ),
                ],
            )

    async def _synthesize(self, goal: str, dag: TaskDAG) -> str:
        """
        Take all completed task results and synthesize a final response.
        Includes user preferences from memory so output matches their style.
        """
        results = dag.get_completed_results()

        if not results:
            return "No tasks completed successfully. Check the workspace for error details."

        # Build context from all task results
        context_parts = []
        for task in dag.tasks:
            if task.status == TaskStatus.COMPLETED and task.result:
                context_parts.append(
                    f"## Task: {task.name} (type: {task.agent_type})\n{task.result}"
                )

        context = "\n\n---\n\n".join(context_parts)

        # Inject preferences into synthesis prompt
        synthesis_prompt = SYNTHESIZE_SYSTEM_PROMPT
        prefs = self.memory.state.preferences
        if prefs:
            pref_lines = "\n".join(f"- {p.key}: {p.value}" for p in prefs)
            synthesis_prompt += (
                f"\n\nUser preferences (apply these to your output):\n{pref_lines}"
            )

        user_message = (
            f"Original goal: {goal}\n\n"
            f"Below are the results from {len(results)} completed sub-tasks. "
            f"Synthesize them into a single coherent response that addresses the original goal.\n\n"
            f"{context}"
        )

        return await self.client.chat(
            system_prompt=synthesis_prompt,
            user_message=user_message,
        )

    def _record_to_memory(self, result: OrchestratorResult, workspace_id: str) -> None:
        """Record a completed run to the user's history."""
        self.memory.add_run(
            goal=result.goal,
            final_output=result.final_output,
            total_tasks=result.total_tasks,
            completed_tasks=result.completed_tasks,
            failed_tasks=result.failed_tasks,
            elapsed_seconds=result.elapsed_seconds,
            workspace_id=workspace_id,
        )
        logger.debug(f"Recorded run to memory for user '{self.user_id}'")

    def _learn_from_tool_results(self, dag: TaskDAG) -> None:
        """
        Auto-learn tool knowledge from the run.
        If a tool task's final output contains hints about API issues
        (e.g., endpoint deprecation, required auth), record them so
        future runs can avoid the same problems.

        This is a best-effort heuristic — it looks for common patterns
        in error messages and successful workarounds.
        """
        for task in dag.tasks:
            if task.agent_type != AgentType.TOOL:
                continue

            # Learn from failures
            if task.status == TaskStatus.FAILED and task.error:
                error_lower = task.error.lower()
                source = self._guess_source_from_task(task)

                if "404" in error_lower or "not found" in error_lower:
                    self.memory.add_tool_knowledge(
                        source=source,
                        fact=f"Endpoint returned 404. Task was: {task.description[:100]}. "
                             f"Error: {task.error[:200]}",
                        confidence="confirmed",
                    )
                elif "401" in error_lower or "403" in error_lower or "api key" in error_lower:
                    self.memory.add_tool_knowledge(
                        source=source,
                        fact=f"Requires authentication/API key. Task: {task.description[:100]}",
                        confidence="confirmed",
                    )
                elif "429" in error_lower or "rate limit" in error_lower:
                    self.memory.add_tool_knowledge(
                        source=source,
                        fact=f"Rate limited. May need to add delays or use a different endpoint.",
                        confidence="confirmed",
                    )

    def _guess_source_from_task(self, task: TaskNode) -> str:
        """Try to identify the data source from a task description."""
        desc_lower = task.description.lower()
        known_sources = [
            ("coingecko", "coingecko"),
            ("yahoo finance", "yahoo_finance"),
            ("yahoo", "yahoo_finance"),
            ("alpha vantage", "alpha_vantage"),
            ("open-meteo", "open_meteo"),
            ("openmeteo", "open_meteo"),
            ("coinbase", "coinbase"),
            ("binance", "binance"),
            ("wikipedia", "wikipedia"),
            ("github", "github"),
        ]
        for keyword, source_id in known_sources:
            if keyword in desc_lower:
                return source_id
        return "unknown"
