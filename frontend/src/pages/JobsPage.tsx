import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { JobListItem, JobDetail } from '../api'
import MarkdownOutput from '../components/MarkdownOutput'

function Badge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{status}</span>
}

function JobsList() {
  const { token } = useAuth()
  const api = createApiClient(token)
  const navigate = useNavigate()

  const { data: jobs, isLoading, error } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<JobListItem[]>('/jobs'),
  })

  if (isLoading) return <div className="empty-state"><p>Loading...</p></div>
  if (error) return <div className="card">Error: {(error as Error).message}</div>
  if (!jobs?.length) {
    return (
      <div className="empty-state">
        <p>No jobs yet. Submit a task to get started.</p>
      </div>
    )
  }

  return (
    <>
      {jobs.map(j => (
        <div
          key={j.job_id}
          className="card"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate(`/jobs/${j.job_id}`)}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Badge status={j.status} />
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {new Date(j.created_at).toLocaleString()}
            </span>
          </div>
          <p style={{ marginTop: 8, fontSize: 14 }}>{j.goal}</p>
        </div>
      ))}
    </>
  )
}

function JobDetailView({ jobId }: { jobId: string }) {
  const { token } = useAuth()
  const api = createApiClient(token)
  const navigate = useNavigate()

  const { data: job, isLoading, error } = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<JobDetail>(`/jobs/${jobId}`),
  })

  if (isLoading) return <div className="empty-state"><p>Loading...</p></div>
  if (error) return <div className="card">Error: {(error as Error).message}</div>
  if (!job) return null

  return (
    <>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => navigate('/jobs')}
        style={{ marginBottom: 16 }}
      >
        ← Back
      </button>
      <div className="card">
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
          }}
        >
          <Badge status={job.status} />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Job: {job.job_id}</span>
        </div>
        <p style={{ fontWeight: 600, marginBottom: 12 }}>{job.goal}</p>
        {job.result && (
          <>
            <p style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
              {job.result.completed_tasks}/{job.result.total_tasks} tasks ·{' '}
              {job.result.elapsed_seconds.toFixed(1)}s
            </p>
            <MarkdownOutput>{job.result.final_output}</MarkdownOutput>
          </>
        )}
        {job.error && (
          <pre style={{ marginTop: 8, color: 'var(--error)', fontSize: 13 }}>{job.error}</pre>
        )}
      </div>
    </>
  )
}

export default function JobsPage() {
  const { jobId } = useParams()

  return (
    <>
      <h2>Jobs</h2>
      {jobId ? <JobDetailView jobId={jobId} /> : <JobsList />}
    </>
  )
}
