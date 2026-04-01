import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { JobDetail } from '../api'
import MarkdownOutput from '../components/MarkdownOutput'

function Badge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{status}</span>
}

export default function NewTaskPage() {
  const [goal, setGoal] = useState('')
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const { token } = useAuth()
  const api = createApiClient(token)

  const submitMutation = useMutation({
    mutationFn: (g: string) =>
      api.post<{ job_id: string; status: string }>('/jobs', { goal: g }),
    onSuccess: data => {
      setActiveJobId(data.job_id)
      setGoal('')
    },
  })

  const jobQuery = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => api.get<JobDetail>(`/jobs/${activeJobId}`),
    enabled: !!activeJobId,
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'pending' || status === 'running' ? 2000 : false
    },
  })

  function handleSubmit() {
    const trimmed = goal.trim()
    if (!trimmed) return
    setActiveJobId(null)
    submitMutation.mutate(trimmed)
  }

  const job = jobQuery.data

  return (
    <>
      <h2>New Task</h2>
      <div className="goal-form">
        <input
          type="text"
          value={goal}
          onChange={e => setGoal(e.target.value)}
          placeholder="Describe what you want to accomplish..."
          onKeyDown={e => e.key === 'Enter' && handleSubmit()}
        />
        <button
          className="btn"
          onClick={handleSubmit}
          disabled={submitMutation.isPending}
        >
          {submitMutation.isPending ? 'Submitting...' : 'Run'}
        </button>
      </div>

      {submitMutation.isError && (
        <div className="card" style={{ borderColor: 'var(--error)' }}>
          Error: {submitMutation.error.message}
        </div>
      )}

      {activeJobId && (
        <>
          {(!job || job.status === 'pending' || job.status === 'running') && (
            <div className="card">
              <span className="polling-dot" /> Running... (Job: {activeJobId})
            </div>
          )}

          {job?.status === 'completed' && (
            <div className="card">
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 12,
                }}
              >
                <Badge status="completed" />
                <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {job.result?.completed_tasks}/{job.result?.total_tasks} tasks ·{' '}
                  {job.result?.elapsed_seconds.toFixed(1)}s
                </span>
              </div>
              <MarkdownOutput>{job.result?.final_output ?? ''}</MarkdownOutput>
            </div>
          )}

          {job?.status === 'failed' && (
            <div className="card" style={{ borderColor: 'var(--error)' }}>
              <Badge status="failed" />
              <pre style={{ marginTop: 8, color: 'var(--error)', fontSize: 13 }}>
                {job.error || 'Unknown error'}
              </pre>
            </div>
          )}
        </>
      )}
    </>
  )
}
