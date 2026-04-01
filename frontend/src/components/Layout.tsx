import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'

export default function Layout() {
  const { currentUser, logout } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  function handleLogout() {
    queryClient.clear()
    logout()
    navigate('/login')
  }

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
          <NavLink to="/tasks">New Task</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
          <NavLink to="/workspaces">Workspace History</NavLink>
          <NavLink to="/memory">Memory</NavLink>
          <NavLink to="/preferences">Preferences</NavLink>
        </div>
        <div className="sidebar-footer">
          <button onClick={handleLogout}>Sign Out</button>
        </div>
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
