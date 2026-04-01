import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { MemoryResponse } from '../api'

function outcomeToStatus(outcome: string) {
  if (outcome === 'success') return 'completed'
  if (outcome === 'partial') return 'running'
  return 'failed'
}

export default function MemoryPage() {
  const { token } = useAuth()
  const api = createApiClient(token)
  const qc = useQueryClient()

  const { data: mem, isLoading, error } = useQuery({
    queryKey: ['memory'],
    queryFn: () => api.get<MemoryResponse>('/memory'),
  })

  const deleteToolKnowledge = useMutation({
    mutationFn: (source: string) =>
      api.del(`/memory/tool-knowledge/${encodeURIComponent(source)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memory'] }),
  })

  if (isLoading) {
    return (
      <>
        <h2>Memory</h2>
        <div className="empty-state"><p>Loading...</p></div>
      </>
    )
  }

  if (error) {
    return (
      <>
        <h2>Memory</h2>
        <div className="card">Error: {(error as Error).message}</div>
      </>
    )
  }

  return (
    <>
      <h2>Memory</h2>

      <div className="card">
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>
          Tool Knowledge ({mem?.tool_knowledge.length ?? 0})
        </h3>
        {mem?.tool_knowledge.length ? (
          mem.tool_knowledge.map(tk => (
            <div key={tk.source} className="pref-row">
              <span className="pref-key">{tk.source}</span>
              <span className="pref-value">{tk.fact}</span>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => deleteToolKnowledge.mutate(tk.source)}
                disabled={deleteToolKnowledge.isPending}
              >
                ×
              </button>
            </div>
          ))
        ) : (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            None yet. Tool knowledge is learned automatically from API failures.
          </p>
        )}
      </div>

      <div className="card">
        <h3 style={{ fontSize: 14, marginBottom: 8 }}>
          Run History ({mem?.history_count ?? 0} entries)
        </h3>
        {mem?.history.length ? (
          [...mem.history].reverse().slice(0, 15).map((h, i) => (
            <div
              key={i}
              style={{ padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}
            >
              <span className={`badge badge-${outcomeToStatus(h.outcome)}`}>{h.outcome}</span>
              <span style={{ color: 'var(--text-muted)', margin: '0 8px' }}>
                {h.timestamp.slice(0, 16)}
              </span>
              {h.goal}
            </div>
          ))
        ) : (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No history yet.</p>
        )}
      </div>
    </>
  )
}
