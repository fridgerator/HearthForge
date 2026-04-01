import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { fetchSetupStatus } from '../api'

export default function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [isSetup, setIsSetup] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    fetchSetupStatus().then(setIsSetup)
  }, [])

  async function handleSubmit() {
    if (!username || !password) {
      setError('Username and password required')
      return
    }
    setError('')
    setLoading(true)
    try {
      const endpoint = isSetup ? '/api/auth/setup' : '/api/auth/login'
      const body = isSetup
        ? { username, password, display_name: displayName || null }
        : { username, password }

      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`)
      }

      const data = await res.json()
      login(data.token, { username: data.username, display_name: data.display_name })
      navigate('/tasks')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-container">
      <div className="login-box">
        <h1>HearthForge</h1>
        <p className="subtitle">Agent Orchestrator</p>
        {isSetup && (
          <div className="setup-notice">
            First time setup — create your admin account.
          </div>
        )}
        <div className="field">
          <label>Username</label>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete="current-password"
            onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          />
        </div>
        {isSetup && (
          <div className="field">
            <label>Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder="Optional"
            />
          </div>
        )}
        <button className="btn" onClick={handleSubmit} disabled={loading}>
          {loading ? 'Please wait...' : isSetup ? 'Create Account' : 'Sign In'}
        </button>
        {error && <div className="login-error">{error}</div>}
      </div>
    </div>
  )
}
