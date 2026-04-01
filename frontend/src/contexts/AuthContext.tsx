import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import type { User } from '../api'

interface AuthContextType {
  token: string | null
  currentUser: User | null
  isLoading: boolean
  login: (token: string, user: User) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('hf_token'))
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    if (!token) {
      setIsLoading(false)
      return
    }
    fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      .then(res => {
        if (!res.ok) throw new Error('Invalid token')
        return res.json() as Promise<User>
      })
      .then(user => setCurrentUser(user))
      .catch(() => {
        setToken(null)
        localStorage.removeItem('hf_token')
      })
      .finally(() => setIsLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function login(newToken: string, user: User) {
    localStorage.setItem('hf_token', newToken)
    setToken(newToken)
    setCurrentUser(user)
  }

  function logout() {
    localStorage.removeItem('hf_token')
    setToken(null)
    setCurrentUser(null)
  }

  return (
    <AuthContext.Provider value={{ token, currentUser, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
