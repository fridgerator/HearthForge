import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { WorkspaceListItem, WorkspaceDetail } from '../api'
import MarkdownOutput from '../components/MarkdownOutput'

function WorkspacesList() {
  const { token } = useAuth()
  const api = createApiClient(token)
  const navigate = useNavigate()

  const { data: workspaces, isLoading, error } = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.get<WorkspaceListItem[]>('/workspaces'),
  })

  if (isLoading) return <div className="empty-state"><p>Loading...</p></div>
  if (error) return <div className="card">Error: {(error as Error).message}</div>
  if (!workspaces?.length) {
    return <div className="empty-state"><p>No workspace runs found.</p></div>
  }

  return (
    <>
      {workspaces.map(w => (
        <div
          key={w.run_id}
          className="card"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate(`/workspaces/${w.run_id}`)}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-muted)' }}>
              {w.run_id}
            </span>
            {w.has_result ? (
              <span className="badge badge-completed">has result</span>
            ) : (
              <span className="badge badge-pending">no result</span>
            )}
          </div>
          {w.goal && <p style={{ marginTop: 8, fontSize: 14 }}>{w.goal}</p>}
        </div>
      ))}
    </>
  )
}

function WorkspaceDetailView({ runId }: { runId: string }) {
  const { token } = useAuth()
  const api = createApiClient(token)
  const navigate = useNavigate()

  const { data: ws, isLoading, error } = useQuery({
    queryKey: ['workspace', runId],
    queryFn: () => api.get<WorkspaceDetail>(`/workspaces/${runId}`),
  })

  if (isLoading) return <div className="empty-state"><p>Loading...</p></div>
  if (error) return <div className="card">Error: {(error as Error).message}</div>
  if (!ws) return null

  return (
    <>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => navigate('/workspaces')}
        style={{ marginBottom: 16 }}
      >
        ← Back
      </button>
      <div className="card">
        <p style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--text-muted)', marginBottom: 8 }}>
          {runId}
        </p>
        {ws.goal && <p style={{ fontWeight: 600, marginBottom: 12 }}>{ws.goal}</p>}
        {ws.result ? (
          <MarkdownOutput>{ws.result}</MarkdownOutput>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No result file.</p>
        )}
      </div>

      {ws.tasks && Object.keys(ws.tasks).length > 0 && (
        <>
          <h3 style={{ margin: '16px 0 8px', fontSize: 16 }}>Tasks</h3>
          {Object.entries(ws.tasks).map(([taskId, info]) => {
            const status = info.status?.status || 'unknown'
            const name = info.status?.name || taskId
            return (
              <div key={taskId} className="card">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 8,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    [{taskId}] {name}
                  </span>
                  <span className={`badge badge-${status}`}>{status}</span>
                </div>
                {info.output && (
                  <details>
                    <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-muted)' }}>
                      Show output ({info.output.length} chars)
                    </summary>
                    <div style={{ marginTop: 8 }}>
                      <MarkdownOutput maxHeight={200}>{info.output}</MarkdownOutput>
                    </div>
                  </details>
                )}
                {info.status?.error && (
                  <p style={{ color: 'var(--error)', fontSize: 12, marginTop: 4 }}>
                    Error: {info.status.error}
                  </p>
                )}
              </div>
            )
          })}
        </>
      )}
    </>
  )
}

export default function WorkspacesPage() {
  const { runId } = useParams()

  return (
    <>
      <h2>Workspace History</h2>
      {runId ? <WorkspaceDetailView runId={runId} /> : <WorkspacesList />}
    </>
  )
}
