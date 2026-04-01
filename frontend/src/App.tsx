import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import NewTaskPage from './pages/NewTaskPage'
import JobsPage from './pages/JobsPage'
import WorkspacesPage from './pages/WorkspacesPage'
import MemoryPage from './pages/MemoryPage'
import PreferencesPage from './pages/PreferencesPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})

function AppRoutes() {
  const { token, isLoading } = useAuth()

  if (isLoading) {
    return <div className="loading">Loading...</div>
  }

  if (!token) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/tasks" replace />} />
        <Route path="/tasks" element={<NewTaskPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:jobId" element={<JobsPage />} />
        <Route path="/workspaces" element={<WorkspacesPage />} />
        <Route path="/workspaces/:runId" element={<WorkspacesPage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/preferences" element={<PreferencesPage />} />
        <Route path="*" element={<Navigate to="/tasks" replace />} />
      </Route>
      <Route path="/login" element={<Navigate to="/tasks" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
