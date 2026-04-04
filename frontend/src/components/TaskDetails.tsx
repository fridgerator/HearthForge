import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { WorkspaceDetail } from '../api'
import MarkdownOutput from './MarkdownOutput'

interface TaskDetailsProps {
  runId: string | null
  completedTasks: number
  totalTasks: number
  elapsedSeconds: number
}

export default function TaskDetails({ runId, completedTasks, totalTasks, elapsedSeconds }: TaskDetailsProps) {
  const [expanded, setExpanded] = useState(false)
  const { token } = useAuth()
  const api = createApiClient(token)

  // Fetch workspace details lazily — only when expanded and we have a runId
  const { data: workspace, isLoading } = useQuery({
    queryKey: ['workspace', runId],
    queryFn: () => api.get<WorkspaceDetail>(`/workspaces/${runId}`),
    enabled: expanded && !!runId,
  })

  return (
    <div className="task-details">
      <button
        className="task-details-toggle"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="task-details-arrow">{expanded ? '▼' : '▶'}</span>
        <span className="task-details-summary">
          Task execution ({completedTasks}/{totalTasks} completed · {elapsedSeconds.toFixed(1)}s)
        </span>
      </button>

      {expanded && (
        <div className="task-details-content">
          {isLoading && <div className="task-details-loading">Loading task details...</div>}
          {!runId && <div className="task-details-loading">No workspace data available</div>}
          {workspace?.tasks && Object.entries(workspace.tasks).map(([taskId, info]) => {
            const status = info.status?.status || 'unknown'
            const name = info.status?.name || taskId
            return (
              <div key={taskId} className="task-detail-item">
                <div className="task-detail-header">
                  <span className="task-detail-name">[{taskId}] {name}</span>
                  <span className={`badge badge-${status}`}>{status}</span>
                </div>
                {info.output && (
                  <details className="task-detail-output">
                    <summary>Show output ({info.output.length} chars)</summary>
                    <div className="task-detail-output-content">
                      <MarkdownOutput maxHeight={200}>{info.output}</MarkdownOutput>
                    </div>
                  </details>
                )}
                {info.status?.error && (
                  <p className="task-detail-error">Error: {info.status.error}</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
