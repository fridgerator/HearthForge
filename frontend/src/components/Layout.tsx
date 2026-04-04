import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { JobListItem } from '../api'

export default function Layout() {
  const { currentUser, token, logout } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const api = createApiClient(token)

  const { data: jobs } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<JobListItem[]>('/jobs'),
  })

  function handleLogout() {
    queryClient.clear()
    logout()
    navigate('/login')
  }

  function handleNewChat() {
    navigate('/')
  }

  // Show most recent 20 jobs in sidebar
  const recentJobs = (jobs ?? []).slice(0, 20)

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar-header">
          <h1>HearthForge</h1>
          <div className="user-info">
            {currentUser?.display_name || currentUser?.username}
          </div>
        </div>
        <div className="sidebar-nav">
          <button className="sidebar-new-chat" onClick={handleNewChat}>
            + New Chat
          </button>
          {recentJobs.length > 0 && (
            <div className="sidebar-history">
              <div className="sidebar-section-label">Recent</div>
              {recentJobs.map(job => (
                <NavLink
                  key={job.job_id}
                  to={`/chat/${job.job_id}`}
                  className="sidebar-history-item"
                  title={job.goal}
                >
                  {job.goal.length > 40 ? job.goal.slice(0, 40) + '...' : job.goal}
                </NavLink>
              ))}
            </div>
          )}
          <div className="sidebar-divider" />
          <NavLink to="/memory">Memory</NavLink>
          <NavLink to="/preferences">Preferences</NavLink>
        </div>
        <div className="sidebar-footer">
          <button onClick={handleLogout}>Sign Out</button>
        </div>
      </nav>
      <main className="main chat-main">
        <Outlet />
      </main>
    </div>
  )
}
