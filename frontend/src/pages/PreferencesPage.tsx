import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { MemoryResponse } from '../api'

export default function PreferencesPage() {
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const { token } = useAuth()
  const api = createApiClient(token)
  const qc = useQueryClient()

  const { data: mem, isLoading } = useQuery({
    queryKey: ['memory'],
    queryFn: () => api.get<MemoryResponse>('/memory'),
  })

  const addPref = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      api.post('/memory/preferences', { key, value }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['memory'] })
      setKey('')
      setValue('')
    },
  })

  const deletePref = useMutation({
    mutationFn: (k: string) => api.del(`/memory/preferences/${encodeURIComponent(k)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['memory'] }),
  })

  function handleAdd() {
    if (!key.trim() || !value.trim()) return
    addPref.mutate({ key: key.trim(), value: value.trim() })
  }

  return (
    <>
      <h2>Preferences</h2>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          type="text"
          value={key}
          onChange={e => setKey(e.target.value)}
          placeholder="Key (e.g. output_style)"
          style={{ width: 200 }}
        />
        <input
          type="text"
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder="Value (e.g. concise, no fluff)"
          style={{ flex: 1 }}
          onKeyDown={e => e.key === 'Enter' && handleAdd()}
        />
        <button className="btn btn-sm" onClick={handleAdd} disabled={addPref.isPending}>
          {addPref.isPending ? 'Adding...' : 'Add'}
        </button>
      </div>

      {isLoading ? (
        <div className="empty-state"><p>Loading...</p></div>
      ) : mem?.preferences.length ? (
        <div className="card">
          {mem.preferences.map(p => (
            <div key={p.key} className="pref-row">
              <span className="pref-key">{p.key}</span>
              <span className="pref-value">{p.value}</span>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => deletePref.mutate(p.key)}
                disabled={deletePref.isPending}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-state"><p>No preferences set. Add one above.</p></div>
      )}
    </>
  )
}
