"""
Configuration for the HearthForge orchestrator.
All settings can be overridden via environment variables.
"""

import os
from pathlib import Path

# Ollama connection
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")

# Workspace for filesystem-based IPC
WORKSPACE_ROOT = Path(os.getenv("HEARTHFORGE_WORKSPACE", "/tmp/hearthforge"))

# Orchestrator settings
MAX_CONCURRENT_AGENTS = int(os.getenv("HEARTHFORGE_MAX_CONCURRENT", "3"))
TASK_TIMEOUT_SECONDS = int(os.getenv("HEARTHFORGE_TASK_TIMEOUT", "600"))

# Retry settings
MAX_RETRIES = int(os.getenv("HEARTHFORGE_MAX_RETRIES", "3"))
# Applies to both regular agent LLM failures and tool script failures.
# For tool tasks, the LLM sees the previous error and rewrites the script.

# Memory settings
# Per-user memory files stored here. Each user gets {user_id}.json.
MEMORY_DIR = Path(os.getenv("HEARTHFORGE_MEMORY_DIR", str(Path.home() / ".hearthforge" / "memory")))
# Number of run history entries to keep per user (oldest drops off)
MEMORY_MAX_HISTORY = int(os.getenv("HEARTHFORGE_MAX_HISTORY", "50"))
# Max characters of memory context to inject into prompts
MEMORY_PROMPT_BUDGET = int(os.getenv("HEARTHFORGE_MEMORY_BUDGET", "4000"))

# Server settings
SERVER_HOST = os.getenv("HEARTHFORGE_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("HEARTHFORGE_PORT", "8742"))

# Auth settings
# User credentials stored here. Created on first run or via CLI.
AUTH_DB_PATH = Path(os.getenv("HEARTHFORGE_AUTH_DB", str(Path.home() / ".hearthforge" / "users.json")))
# JWT secret for session tokens. CHANGE THIS in production.
JWT_SECRET = os.getenv("HEARTHFORGE_JWT_SECRET", "hearthforge-dev-secret-change-me")
JWT_EXPIRY_HOURS = int(os.getenv("HEARTHFORGE_JWT_EXPIRY_HOURS", "72"))

# SearXNG settings (self-hosted search engine for tool agent)
# The tool agent searches for current API docs before writing scripts.
# See docker-compose.searxng.yml to run SearXNG locally.
SEARXNG_URL = os.getenv("HEARTHFORGE_SEARXNG_URL", "http://localhost:8080")
# If True, tool agent always searches before writing scripts (Perplexity-style).
# If False, tool agent only searches on retries after a failure.
SEARXNG_SEARCH_FIRST = os.getenv("HEARTHFORGE_SEARCH_FIRST", "true").lower() == "true"

# Page content fetching settings (used by the search client after getting results)
# After searching, the client fetches and extracts text from the top N result pages.
# This gives the tool agent actual API documentation instead of just snippets.
# Set PAGE_FETCH_MAX_RESULTS=0 to disable page fetching (snippets only).
PAGE_FETCH_MAX_RESULTS = int(os.getenv("HEARTHFORGE_PAGE_FETCH_MAX_RESULTS", "2"))
PAGE_FETCH_TIMEOUT = float(os.getenv("HEARTHFORGE_PAGE_FETCH_TIMEOUT", "5.0"))
PAGE_FETCH_MAX_CHARS = int(os.getenv("HEARTHFORGE_PAGE_FETCH_MAX_CHARS", "8000"))

# Ollama generation parameters
# num_predict must be large enough for Qwen3's thinking tokens + actual response.
# Qwen3 spends tokens on <think>...</think> before producing content — 4096 is
# too small for complex tasks and results in empty responses.
OLLAMA_OPTIONS = {
    "temperature": 0.3,       # Lower for more deterministic task planning
    "num_predict": 16384,     # Max tokens per response (thinking models need headroom)
    "stop": ["<|im_end|>", "<|endoftext|>"],  # Prevent Qwen3 runaway generation
}

# Agent-specific temperature overrides (creative tasks get higher temp)
AGENT_TEMPERATURES = {
    "research": 0.3,
    "analyze": 0.3,
    "write": 0.7,
    "code": 0.2,
    "tool": 0.2,
    "summarize": 0.4,
    "default": 0.3,
}
