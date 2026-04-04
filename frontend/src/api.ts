const API_BASE = '/api'

// --- Types matching server Pydantic models ---

export interface User {
  username: string
  display_name: string
}

export interface LoginResponse {
  token: string
  username: string
  display_name: string
}

export interface Conversation {
  conversation_id: string
  title: string
  created_at: string
  job_count: number
}

export interface JobListItem {
  job_id: string
  conversation_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  goal: string
  created_at: string
  completed_at: string | null
}

export interface OrchestratorResult {
  final_output: string
  completed_tasks: number
  total_tasks: number
  elapsed_seconds: number
  run_id: string | null
}

export interface JobDetail extends JobListItem {
  result: OrchestratorResult | null
  error: string | null
}

export interface WorkspaceListItem {
  run_id: string
  goal: string | null
  has_result: boolean
}

export interface TaskInfo {
  status?: {
    status: string
    name?: string
    error?: string
  }
  output?: string
  attempts?: unknown[]
}

export interface WorkspaceDetail {
  run_id: string
  goal?: string
  result?: string
  dag?: unknown
  tasks?: Record<string, TaskInfo>
}

export interface ToolKnowledge {
  source: string
  fact: string
}

export interface Preference {
  key: string
  value: string
}

export interface HistoryEntry {
  goal: string
  outcome: 'success' | 'partial' | 'failed'
  timestamp: string
}

export interface MemoryResponse {
  user_id: string
  history_count: number
  preferences: Preference[]
  tool_knowledge: ToolKnowledge[]
  history: HistoryEntry[]
}

// --- API client factory ---

export function createApiClient(token: string | null) {
  async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(opts.headers as Record<string, string>),
    }
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch(API_BASE + path, { ...opts, headers })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`)
    }
    return res.json() as Promise<T>
  }

  return {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body: unknown) =>
      request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
    del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  }
}

// Unauthenticated — check if first-time setup is needed
export async function fetchSetupStatus(): Promise<boolean> {
  const res = await fetch(API_BASE + '/auth/me')
  if (res.ok) {
    const data = await res.json()
    return data.username === 'setup'
  }
  return false
}
