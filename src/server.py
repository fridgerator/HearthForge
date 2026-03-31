"""
FastAPI server for the HearthForge orchestrator.

Provides a REST API for:
  - Submitting goals (async, returns a job ID)
  - Checking job status and results
  - Browsing workspace history
  - Managing user preferences and memory
  - User authentication (JWT-based)

Jobs run asynchronously via asyncio tasks. The server maintains
an in-memory job registry that maps job IDs to their status and
results. Workspace files on disk provide persistence if the server
restarts.

Run with:
  python server.py
  # or
  uvicorn server:app --host 0.0.0.0 --port 8742
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auth import AuthManager
from config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    SERVER_HOST,
    SERVER_PORT,
    WORKSPACE_ROOT,
)
from memory import MemoryManager
from models import OrchestratorResult
from ollama_client import OllamaClient
from orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# --- Request/Response Models ---

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    username: str
    display_name: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None

class SubmitGoalRequest(BaseModel):
    goal: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # "pending", "running", "completed", "failed"
    goal: str
    created_at: str
    completed_at: str | None = None
    result: OrchestratorResult | None = None
    error: str | None = None

class JobListItem(BaseModel):
    job_id: str
    status: str
    goal: str
    created_at: str
    completed_at: str | None = None

class SetPreferenceRequest(BaseModel):
    key: str
    value: str

class MemoryResponse(BaseModel):
    user_id: str
    history_count: int
    preferences: list[dict]
    tool_knowledge: list[dict]
    history: list[dict]

class WorkspaceListItem(BaseModel):
    run_id: str
    goal: str | None = None
    has_result: bool = False

# --- Job Tracking ---

class Job:
    """In-memory job tracker for async orchestrator runs."""
    def __init__(self, job_id: str, goal: str, user_id: str):
        self.job_id = job_id
        self.goal = goal
        self.user_id = user_id
        self.status = "pending"
        self.created_at = datetime.now().isoformat()
        self.completed_at: str | None = None
        self.result: OrchestratorResult | None = None
        self.error: str | None = None
        self.task: asyncio.Task | None = None

# Global job registry: job_id -> Job
_jobs: dict[str, Job] = {}


# --- App Lifecycle ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    logger.info(f"HearthForge server starting on {SERVER_HOST}:{SERVER_PORT}")
    logger.info(f"Ollama: {OLLAMA_MODEL} @ {OLLAMA_HOST}")
    logger.info(f"Workspace: {WORKSPACE_ROOT}")

    # Ensure first user exists
    auth = AuthManager()
    if not auth.has_users():
        logger.warning("No users found. Create one with POST /api/auth/setup")

    yield

    # Shutdown: cancel any running jobs
    for job in _jobs.values():
        if job.task and not job.task.done():
            job.task.cancel()
    logger.info("Server shutting down")


# --- App Setup ---

app = FastAPI(
    title="HearthForge Orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Home lab - open CORS. Lock down in production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


# --- Auth Dependencies ---

def get_auth() -> AuthManager:
    return AuthManager()

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    auth: Annotated[AuthManager, Depends(get_auth)],
) -> dict:
    """
    Validate JWT token and return user info.
    If no users exist yet (first run), allow unauthenticated access
    so the setup endpoint is reachable.
    """
    if not auth.has_users():
        # No users configured yet - return a temporary identity
        return {"sub": "setup", "display_name": "Setup"}

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = auth.verify_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


# --- Auth Endpoints ---

@app.post("/api/auth/setup", response_model=LoginResponse)
async def setup_first_user(
    req: CreateUserRequest,
    auth: Annotated[AuthManager, Depends(get_auth)],
):
    """Create the first user. Only works when no users exist."""
    if auth.has_users():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Users already exist. Use /api/auth/register instead.",
        )

    auth.create_user(req.username, req.password, req.display_name)
    token = auth.create_token(req.username)
    user = auth.get_user(req.username)
    return LoginResponse(
        token=token,
        username=req.username,
        display_name=user["display_name"],
    )


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    auth: Annotated[AuthManager, Depends(get_auth)],
):
    """Authenticate and receive a JWT token."""
    if not auth.verify_password(req.username, req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = auth.create_token(req.username)
    user = auth.get_user(req.username)
    return LoginResponse(
        token=token,
        username=req.username,
        display_name=user["display_name"],
    )


@app.post("/api/auth/register", response_model=LoginResponse)
async def register_user(
    req: CreateUserRequest,
    user: Annotated[dict, Depends(get_current_user)],
    auth: Annotated[AuthManager, Depends(get_auth)],
):
    """Register a new user. Requires an existing authenticated user."""
    if not auth.create_user(req.username, req.password, req.display_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{req.username}' already exists",
        )

    token = auth.create_token(req.username)
    new_user = auth.get_user(req.username)
    return LoginResponse(
        token=token,
        username=req.username,
        display_name=new_user["display_name"],
    )


@app.get("/api/auth/me")
async def get_me(user: Annotated[dict, Depends(get_current_user)]):
    """Get current user info."""
    return {
        "username": user["sub"],
        "display_name": user.get("display_name", user["sub"]),
    }


# --- Job Endpoints ---

@app.post("/api/jobs", response_model=JobStatusResponse)
async def submit_goal(
    req: SubmitGoalRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Submit a goal for async execution. Returns immediately with a job ID.
    Poll /api/jobs/{job_id} for status and results.
    """
    job_id = str(uuid.uuid4())[:12]
    user_id = user["sub"]

    job = Job(job_id=job_id, goal=req.goal, user_id=user_id)
    _jobs[job_id] = job

    # Launch the orchestrator as a background task
    job.task = asyncio.create_task(_run_job(job))

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        goal=job.goal,
        created_at=job.created_at,
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Get the status and result of a job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Users can only see their own jobs
    if job.user_id != user["sub"]:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        goal=job.goal,
        created_at=job.created_at,
        completed_at=job.completed_at,
        result=job.result,
        error=job.error,
    )


@app.get("/api/jobs", response_model=list[JobListItem])
async def list_jobs(
    user: Annotated[dict, Depends(get_current_user)],
):
    """List all jobs for the current user (most recent first)."""
    user_id = user["sub"]
    user_jobs = [
        JobListItem(
            job_id=j.job_id,
            status=j.status,
            goal=j.goal,
            created_at=j.created_at,
            completed_at=j.completed_at,
        )
        for j in _jobs.values()
        if j.user_id == user_id
    ]
    return sorted(user_jobs, key=lambda j: j.created_at, reverse=True)


async def _run_job(job: Job) -> None:
    """Background task that runs the orchestrator for a job."""
    job.status = "running"
    client = OllamaClient()

    try:
        orchestrator = Orchestrator(client, user_id=job.user_id)
        result = await orchestrator.run(job.goal)
        job.result = result
        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        logger.error(f"Job {job.job_id} failed: {e}")
    finally:
        job.completed_at = datetime.now().isoformat()
        await client.close()


# --- Memory/Preferences Endpoints ---

@app.get("/api/memory", response_model=MemoryResponse)
async def get_memory(
    user: Annotated[dict, Depends(get_current_user)],
):
    """Get the current user's full memory state."""
    user_id = user["sub"]
    memory = MemoryManager(user_id=user_id)
    mem = memory.load()

    return MemoryResponse(
        user_id=user_id,
        history_count=len(mem.history),
        preferences=[p.model_dump() for p in mem.preferences],
        tool_knowledge=[t.model_dump() for t in mem.tool_knowledge],
        history=[h.model_dump() for h in mem.history],
    )


@app.post("/api/memory/preferences")
async def set_preference(
    req: SetPreferenceRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Set or update a user preference."""
    user_id = user["sub"]
    memory = MemoryManager(user_id=user_id)
    memory.load()
    memory.set_preference(req.key, req.value, source="explicit")
    return {"status": "ok", "key": req.key, "value": req.value}


@app.delete("/api/memory/preferences/{key}")
async def delete_preference(
    key: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Delete a user preference."""
    user_id = user["sub"]
    memory = MemoryManager(user_id=user_id)
    memory.load()
    if memory.remove_preference(key):
        return {"status": "ok", "removed": key}
    raise HTTPException(status_code=404, detail=f"Preference '{key}' not found")


@app.delete("/api/memory/tool-knowledge/{source}")
async def delete_tool_knowledge(
    source: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Delete all tool knowledge entries for a given source."""
    user_id = user["sub"]
    memory = MemoryManager(user_id=user_id)
    mem = memory.load()
    before = len(mem.tool_knowledge)
    mem.tool_knowledge = [t for t in mem.tool_knowledge if t.source != source]
    if len(mem.tool_knowledge) < before:
        memory.save()
        return {"status": "ok", "removed_source": source, "removed_count": before - len(mem.tool_knowledge)}
    raise HTTPException(status_code=404, detail=f"No tool knowledge for source '{source}'")


# --- Workspace Browsing ---

@app.get("/api/workspaces", response_model=list[WorkspaceListItem])
async def list_workspaces(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = 20,
):
    """List recent workspace runs (most recent first)."""
    if not WORKSPACE_ROOT.exists():
        return []

    workspaces = []
    dirs = sorted(WORKSPACE_ROOT.iterdir(), reverse=True)

    for d in dirs[:limit]:
        if not d.is_dir():
            continue

        goal = None
        goal_file = d / "goal.txt"
        if goal_file.exists():
            goal = goal_file.read_text().strip()[:200]

        has_result = (d / "result.md").exists()

        workspaces.append(WorkspaceListItem(
            run_id=d.name,
            goal=goal,
            has_result=has_result,
        ))

    return workspaces


@app.get("/api/workspaces/{run_id}")
async def get_workspace(
    run_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """Get details of a specific workspace run."""
    workspace_dir = WORKSPACE_ROOT / run_id
    if not workspace_dir.exists():
        raise HTTPException(status_code=404, detail="Workspace not found")

    result = {"run_id": run_id}

    # Read goal
    goal_file = workspace_dir / "goal.txt"
    if goal_file.exists():
        result["goal"] = goal_file.read_text().strip()

    # Read final result
    result_file = workspace_dir / "result.md"
    if result_file.exists():
        result["result"] = result_file.read_text()

    # Read DAG
    dag_file = workspace_dir / "dag.json"
    if dag_file.exists():
        try:
            result["dag"] = json.loads(dag_file.read_text())
        except json.JSONDecodeError:
            pass

    # Read task statuses
    tasks_dir = workspace_dir / "tasks"
    if tasks_dir.exists():
        tasks = {}
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            task_info = {}

            status_file = task_dir / "status.json"
            if status_file.exists():
                try:
                    task_info["status"] = json.loads(status_file.read_text())
                except json.JSONDecodeError:
                    pass

            output_file = task_dir / "output.txt"
            if output_file.exists():
                task_info["output"] = output_file.read_text()

            attempts_file = task_dir / "attempts.json"
            if attempts_file.exists():
                try:
                    task_info["attempts"] = json.loads(attempts_file.read_text())
                except json.JSONDecodeError:
                    pass

            tasks[task_id] = task_info
        result["tasks"] = tasks

    return result


# --- Health ---

@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "model": OLLAMA_MODEL,
        "ollama_host": OLLAMA_HOST,
        "active_jobs": sum(1 for j in _jobs.values() if j.status == "running"),
        "total_jobs": len(_jobs),
    }


# --- Static Files & SPA ---

STATIC_DIR = Path(__file__).parent.parent / "static"

@app.get("/")
async def serve_index():
    """Serve the web UI."""
    return FileResponse(STATIC_DIR / "index.html")

# Mount static files for any additional assets (CSS, JS, images)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Server Entry Point ---

if __name__ == "__main__":
    import os
    import uvicorn

    debug_logging = os.getenv("HEARTHFORGE_DEBUG_HTTP", "").lower() in ("1", "true")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    uvicorn.run(
        "server:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info",
        access_log=debug_logging,
    )
