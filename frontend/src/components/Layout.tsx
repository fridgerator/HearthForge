import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { Conversation } from '../api'

export default function Layout() {
  const { currentUser, token, logout } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const api = createApiClient(token)

  const { data: conversations } = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api.get<Conversation[]>('/conversations'),
  })

  function handleLogout() {
    queryClient.clear()
    logout()
    navigate('/login')
  }

  function handleNewChat() {
    navigate('/')
  }

  const recentConversations = (conversations ?? []).slice(0, 30)

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
          {recentConversations.length > 0 && (
            <div className="sidebar-history">
              <div className="sidebar-section-label">Recent</div>
              {recentConversations.map(conv => (
                <NavLink
                  key={conv.conversation_id}
                  to={`/chat/${conv.conversation_id}`}
                  className="sidebar-history-item"
                  title={conv.title}
                >
                  {conv.title.length > 40 ? conv.title.slice(0, 40) + '...' : conv.title}
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
