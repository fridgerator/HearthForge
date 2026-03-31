"""
Tool Agent - Generic data fetcher via LLM-generated Python scripts.

Flow:
  1. LLM receives the task description + tool system prompt
  2. LLM writes a Python script that fetches external data
  3. We extract the script from the response
  4. Execute it in a sandboxed subprocess with timeout
  5. If it fails, feed the error back to the LLM and retry (up to MAX_RETRIES)
  6. Capture stdout as the task result (should be JSON)

Retry strategy:
  On failure, the LLM receives its previous script AND the error output.
  This lets it fix the specific problem (wrong URL, bad JSON parsing, etc.)
  rather than starting from scratch. Each attempt is logged to the workspace
  so you can inspect the full history of what was tried.

Security notes:
  - Scripts run in a subprocess with a timeout
  - Network access is not restricted by default (home lab assumption)
  - For production, you'd want: network allowlist, no filesystem access
    outside workspace, resource limits via cgroups/containers, or even
    run inside a Firecracker VM like Perplexity does
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

from config import AGENT_TEMPERATURES, MAX_RETRIES, SEARXNG_SEARCH_FIRST, SEARXNG_URL, TASK_TIMEOUT_SECONDS
from models import TaskNode, TaskStatus
from ollama_client import OllamaClient
from prompts import get_agent_prompt

logger = logging.getLogger(__name__)


class ToolAgent:
    def __init__(self, client: OllamaClient, memory=None, search_client=None):
        self.client = client
        self.memory = memory
        self.search = search_client

    async def execute(
        self,
        task: TaskNode,
        dependency_outputs: dict[str, str],
        workspace_dir: Path | None = None,
    ) -> TaskNode:
        """
        Execute a tool task with retries.
        On each failure, the LLM sees the error and rewrites the script.
        All attempts are logged to the workspace for debugging.
        """
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now()

        logger.info(f"  🔧 [{task.id}] Tool task: {task.name}")

        # Track all attempts for logging
        attempts: list[dict] = []
        last_error: str | None = None
        last_script: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            attempt_log = {
                "attempt": attempt,
                "timestamp": datetime.now().isoformat(),
                "script": None,
                "stdout": None,
                "stderr": None,
                "returncode": None,
                "error": None,
                "success": False,
            }

            try:
                # Step 1: Ask LLM to write (or fix) the script
                if attempt == 1:
                    logger.info(f"  🔧 [{task.id}] Attempt {attempt}/{MAX_RETRIES}: Generating script...")
                    script_response = await self._generate_script(task, dependency_outputs)
                else:
                    logger.info(f"  🔧 [{task.id}] Attempt {attempt}/{MAX_RETRIES}: Retrying with error context...")
                    script_response = await self._generate_retry_script(
                        task, dependency_outputs, last_script, last_error
                    )

                # Step 2: Extract Python code
                script = self._extract_python(script_response)
                if not script:
                    error_msg = (
                        f"LLM did not return a valid Python script.\n"
                        f"Raw response ({len(script_response)} chars):\n"
                        f"{script_response[:1000]}"
                    )
                    attempt_log["error"] = error_msg
                    last_error = error_msg
                    last_script = None
                    attempts.append(attempt_log)
                    continue

                attempt_log["script"] = script
                last_script = script

                # Save the script for this attempt
                if workspace_dir:
                    (workspace_dir / f"script_attempt_{attempt}.py").write_text(script)

                # Step 3: Execute the script
                logger.info(f"  🔧 [{task.id}] Executing script (timeout={TASK_TIMEOUT_SECONDS}s)...")
                stdout, stderr, returncode = await self._execute_script(script)

                attempt_log["stdout"] = stdout
                attempt_log["stderr"] = stderr
                attempt_log["returncode"] = returncode

                if returncode != 0:
                    error_detail = stderr.strip() or "Script exited with non-zero status"

                    # Check if there's usable partial output
                    if stdout.strip() and self._looks_like_valid_data(stdout.strip()):
                        task.result = stdout.strip()
                        task.status = TaskStatus.COMPLETED
                        attempt_log["success"] = True
                        attempt_log["error"] = f"Script exited non-zero but produced usable output. stderr: {error_detail}"
                        logger.warning(f"  ⚠ [{task.id}] Script failed but produced usable output")
                        attempts.append(attempt_log)
                        break

                    # Build detailed error for the retry prompt
                    last_error = self._format_error_for_retry(stderr, stdout, returncode)
                    attempt_log["error"] = last_error
                    logger.warning(f"  ⚠ [{task.id}] Attempt {attempt} failed: {error_detail[:200]}")
                    attempts.append(attempt_log)
                    continue

                # Script exited cleanly - but validate the output content
                # Scripts that handle errors gracefully will exit 0 but print
                # {"error": "..."} — we need to catch that and retry
                output = stdout.strip()
                output_error = self._extract_output_error(output)

                if output_error:
                    last_error = (
                        f"Script ran successfully but returned an error in its output:\n"
                        f"{output_error}\n\n"
                        f"Full output:\n{output[:1000]}"
                    )
                    attempt_log["error"] = last_error
                    logger.warning(f"  ⚠ [{task.id}] Attempt {attempt}: script succeeded but output contains error: {output_error[:200]}")
                    attempts.append(attempt_log)
                    continue

                # Genuine success
                task.result = output
                task.status = TaskStatus.COMPLETED
                attempt_log["success"] = True
                attempts.append(attempt_log)

                result_len = len(task.result)
                logger.info(f"  ✓ [{task.id}] Data fetched on attempt {attempt} ({result_len} chars)")

                # Learn from success: save working endpoints to memory
                if self.memory and script:
                    self._record_working_endpoints(task, script)

                break

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                attempt_log["error"] = last_error
                attempts.append(attempt_log)
                logger.warning(f"  ⚠ [{task.id}] Attempt {attempt} exception: {e}")
                continue

        else:
            # All retries exhausted
            task.status = TaskStatus.FAILED
            task.error = (
                f"All {MAX_RETRIES} attempts failed.\n"
                f"Last error: {last_error}"
            )
            logger.error(f"  ✗ [{task.id}] Tool failed after {MAX_RETRIES} attempts")

        # Write the full attempt log to workspace
        if workspace_dir:
            self._write_attempt_log(workspace_dir, task, attempts)

        task.completed_at = datetime.now()
        return task

    async def _generate_script(
        self,
        task: TaskNode,
        dependency_outputs: dict[str, str],
    ) -> str:
        """
        Ask the LLM to write a Python data-fetching script.

        If SearXNG is available and SEARXNG_SEARCH_FIRST is True,
        searches for current API documentation first and injects the
        results into the prompt. This is the "search-native" approach
        that mirrors Perplexity Computer — the agent leads with search
        rather than guessing from training data.
        """
        system_prompt = get_agent_prompt("tool")

        # Inject tool knowledge from memory so the LLM avoids known bad endpoints
        if self.memory:
            tool_knowledge = self.memory.render_tool_knowledge_for_agent()
            if tool_knowledge:
                system_prompt += f"\n\n{tool_knowledge}"

        temperature = AGENT_TEMPERATURES.get("tool", 0.2)

        parts = [f"## Task: {task.name}\n\n{task.description}"]

        # Always tell the LLM about SearXNG so it can use it for web searches
        if self.search:
            parts.append(
                f"\n\n## Available Services\n"
                f"- SearXNG web search: GET {SEARXNG_URL}/search?q=<query>&format=json&language=en\n"
                f"  Use this when you need to search the web for current information, not just APIs."
            )

        # Search-native: search for current API docs before writing the script
        if SEARXNG_SEARCH_FIRST and self.search:
            search_results = await self._search_for_api_info(task)
            if search_results:
                parts.append(f"\n\n{search_results}")

        if dependency_outputs:
            parts.append("\n\n## Context from prior tasks:\n")
            for dep_id, output in dependency_outputs.items():
                parts.append(f"### Results from task [{dep_id}]:\n{output}\n")

        user_message = "\n".join(parts)

        return await self.client.chat(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
        )

    async def _generate_retry_script(
        self,
        task: TaskNode,
        dependency_outputs: dict[str, str],
        previous_script: str | None,
        error: str | None,
    ) -> str:
        """
        Ask the LLM to FIX a failed script. On retry, ALWAYS search for
        current API docs regardless of SEARXNG_SEARCH_FIRST setting —
        if the first attempt failed, we need fresh information.
        """
        system_prompt = get_agent_prompt("tool")
        temperature = AGENT_TEMPERATURES.get("tool", 0.2)

        parts = [f"## Task: {task.name}\n\n{task.description}"]

        # Always tell the LLM about SearXNG so it can use it for web searches
        if self.search:
            parts.append(
                f"\n\n## Available Services\n"
                f"- SearXNG web search: GET {SEARXNG_URL}/search?q=<query>&format=json&language=en\n"
                f"  Use this when you need to search the web for current information, not just APIs."
            )

        # Always search on retries — the failure likely means stale API knowledge
        if self.search:
            search_results = await self._search_for_api_info(task)
            if search_results:
                parts.append(f"\n\n{search_results}")

        if dependency_outputs:
            parts.append("\n\n## Context from prior tasks:\n")
            for dep_id, output in dependency_outputs.items():
                parts.append(f"### Results from task [{dep_id}]:\n{output}\n")

        parts.append("\n\n## PREVIOUS ATTEMPT FAILED - FIX THE SCRIPT\n")

        if previous_script:
            parts.append(f"### Script that failed:\n```python\n{previous_script}\n```\n")

        if error:
            parts.append(f"### Error output:\n```\n{error}\n```\n")

        parts.append(
            "\nAnalyze the error above and the search results. Write a FIXED version "
            "of the script using the correct, current API endpoints from the search results. "
            "If the original API is down or requires auth, try an alternative source."
        )

        user_message = "\n".join(parts)

        return await self.client.chat(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
        )

    async def _search_for_api_info(self, task: TaskNode) -> str:
        """
        Search SearXNG for current API documentation relevant to a task.
        Returns formatted search results, or empty string if search fails.
        """
        if not self.search:
            return ""

        try:
            results = await self.search.search_for_api_docs(
                task_description=task.description,
                llm_client=self.client,
            )
            if results:
                logger.info(f"  🔍 [{task.id}] Search returned API context")
            return results
        except Exception as e:
            logger.warning(f"  🔍 [{task.id}] Search failed: {e}")
            return ""

    def _extract_python(self, response: str) -> str | None:
        """
        Extract Python code from the LLM response.
        Handles ```python ... ``` fences and bare code.
        """
        # Try to find a python code fence first
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, response, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try generic code fence
        pattern = r"```\s*\n(.*?)```"
        match = re.search(pattern, response, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if "import " in code or "print(" in code or "def " in code:
                return code

        # Last resort: if the whole response looks like Python
        stripped = response.strip()
        if stripped.startswith("import ") or stripped.startswith("#") or stripped.startswith("from "):
            return stripped

        return None

    def _looks_like_valid_data(self, output: str) -> bool:
        """Check if output looks like usable data (valid JSON or structured text)."""
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and "error" in parsed and len(parsed) == 1:
                return False
            return True
        except json.JSONDecodeError:
            return len(output) > 50

    def _extract_output_error(self, output: str) -> str | None:
        """
        Check if script output contains an error payload despite the script
        exiting cleanly. Returns the error message if found, None if output
        looks like real data.

        Catches patterns like:
          {"error": "404 Client Error: Not Found for url: ..."}
          {"error": "API key required for ..."}
          {"status": "error", "message": "..."}
          Empty or near-empty output
        """
        if not output:
            return "Script produced no output"

        # Try to parse as JSON and look for error indicators
        try:
            parsed = json.loads(output)

            if isinstance(parsed, dict):
                # {"error": "..."} — the most common pattern from our tool prompt
                if "error" in parsed:
                    error_val = parsed["error"]
                    # If error is the only key, it's definitely a failure
                    if len(parsed) == 1:
                        return str(error_val)
                    # If there are other keys with real data alongside an error,
                    # it might be partial success — only fail if the other values
                    # are empty/null
                    other_values = {k: v for k, v in parsed.items() if k != "error"}
                    if all(not v for v in other_values.values()):
                        return str(error_val)

                # {"status": "error", "message": "..."}
                status = parsed.get("status", "")
                if isinstance(status, str) and status.lower() == "error":
                    return parsed.get("message", parsed.get("error", "Unknown error"))

            # Check for HTTP error codes in the output text
            # Handles cases where the JSON itself is valid but contains error info
            output_lower = output.lower()
            http_error_patterns = [
                "401 unauthorized",
                "403 forbidden",
                "404 not found",
                "429 too many requests",
                "500 internal server error",
                "502 bad gateway",
                "503 service unavailable",
            ]
            for pattern in http_error_patterns:
                if pattern in output_lower:
                    return f"HTTP error detected in output: {pattern}"

        except json.JSONDecodeError:
            # Not JSON — check if it's an error message in plain text
            output_lower = output.lower()
            if output_lower.startswith("error:") or output_lower.startswith("traceback"):
                return output[:500]

        return None

    def _format_error_for_retry(
        self,
        stderr: str,
        stdout: str,
        returncode: int,
    ) -> str:
        """Format error details in a way that's useful for the LLM to diagnose."""
        parts = [f"Exit code: {returncode}"]

        if stderr.strip():
            # Trim to last 1500 chars - the most relevant part of a traceback
            # is at the end
            trimmed_stderr = stderr.strip()[-1500:]
            parts.append(f"stderr:\n{trimmed_stderr}")

        if stdout.strip():
            trimmed_stdout = stdout.strip()[-500:]
            parts.append(f"stdout (partial):\n{trimmed_stdout}")

        return "\n\n".join(parts)

    def _write_attempt_log(
        self,
        workspace_dir: Path,
        task: TaskNode,
        attempts: list[dict],
    ) -> None:
        """Write a detailed log of all attempts to the workspace."""
        log = {
            "task_id": task.id,
            "task_name": task.name,
            "final_status": task.status.value,
            "final_error": task.error,
            "total_attempts": len(attempts),
            "attempts": [],
        }

        for attempt in attempts:
            # Don't dump full scripts into the JSON log - they're saved as
            # separate files. Just note if one was generated.
            entry = {
                "attempt": attempt["attempt"],
                "timestamp": attempt["timestamp"],
                "has_script": attempt["script"] is not None,
                "script_length": len(attempt["script"]) if attempt["script"] else 0,
                "returncode": attempt["returncode"],
                "stdout_length": len(attempt["stdout"]) if attempt["stdout"] else 0,
                "stderr_preview": (attempt["stderr"] or "")[:500],
                "error": attempt["error"],
                "success": attempt["success"],
            }
            log["attempts"].append(entry)

        log_path = workspace_dir / "attempts.json"
        log_path.write_text(json.dumps(log, indent=2))
        logger.debug(f"  🔧 [{task.id}] Attempt log saved to {log_path}")

    def _record_working_endpoints(self, task: TaskNode, script: str) -> None:
        """
        Extract API endpoints from a successful script and save them to memory.
        This builds a dynamic knowledge base of confirmed working endpoints over time,
        so future runs don't have to re-discover them through search or trial-and-error.
        """
        from urllib.parse import urlparse

        # Parse SEARXNG_URL once so we can skip it
        searxng_host = urlparse(SEARXNG_URL).netloc.lower()

        urls = re.findall(r'https?://[^\s\'"\\)},]+', script)
        seen = set()
        for url in urls:
            # Strip any trailing punctuation that got captured
            url = url.rstrip(".,;:")
            try:
                parsed = urlparse(url)
                if not parsed.netloc or parsed.path in ("", "/"):
                    continue
                # Skip SearXNG itself — it's infrastructure, not a learned endpoint
                if parsed.netloc.lower() == searxng_host:
                    continue
                # Build a stable base key: scheme + netloc + path (no query string)
                base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if base in seen:
                    continue
                seen.add(base)
                source = self._source_from_domain(parsed.netloc)
                fact = f"Working endpoint confirmed: {base}"
                self.memory.add_tool_knowledge(source=source, fact=fact, confidence="confirmed")
                logger.debug(f"  📚 [{task.id}] Recorded working endpoint: {base}")
            except Exception:
                continue

    def _source_from_domain(self, domain: str) -> str:
        """Map a hostname to a short source identifier for memory storage."""
        domain_lower = domain.lower()
        known = [
            ("coingecko", "coingecko"),
            ("finance.yahoo", "yahoo_finance"),
            ("yahoo", "yahoo_finance"),
            ("open-meteo", "open_meteo"),
            ("openmeteo", "open_meteo"),
            ("wikipedia", "wikipedia"),
            ("coinbase", "coinbase"),
            ("binance", "binance"),
            ("alpha-vantage", "alpha_vantage"),
            ("alphavantage", "alpha_vantage"),
            ("finnhub", "finnhub"),
            ("polygon.io", "polygon"),
            ("newsapi", "newsapi"),
            ("openweathermap", "openweathermap"),
            ("github", "github"),
            ("reddit", "reddit"),
        ]
        for keyword, source_id in known:
            if keyword in domain_lower:
                return source_id
        # Fallback: use the second-level domain (e.g. "api.example.com" → "example")
        parts = domain_lower.rstrip(".").split(".")
        return parts[-2] if len(parts) >= 2 else domain_lower

    async def _execute_script(self, script: str) -> tuple[str, str, int]:
        """
        Execute a Python script in a subprocess with timeout.
        Returns (stdout, stderr, returncode).
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            prefix="hearthforge_tool_",
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            result = await asyncio.to_thread(
                self._run_subprocess,
                script_path,
            )
            return result
        finally:
            Path(script_path).unlink(missing_ok=True)

    def _run_subprocess(self, script_path: str) -> tuple[str, str, int]:
        """Run the script in a subprocess. Blocking call (wrapped in to_thread)."""
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=TASK_TIMEOUT_SECONDS,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"Script timed out after {TASK_TIMEOUT_SECONDS}s", 1
        except Exception as e:
            return "", f"Failed to execute script: {e}", 1
