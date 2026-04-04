import { useState, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { JobDetail } from '../api'
import ChatInput from '../components/ChatInput'
import ChatMessage from '../components/ChatMessage'
import type { ChatMessageData } from '../components/ChatMessage'

export default function ChatPage() {
  const { jobId: routeJobId } = useParams()
  const { token } = useAuth()
  const api = createApiClient(token)

  const [messages, setMessages] = useState<ChatMessageData[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Load a specific job from route params (clicking sidebar history)
  const routeJobQuery = useQuery({
    queryKey: ['job', routeJobId],
    queryFn: () => api.get<JobDetail>(`/jobs/${routeJobId}`),
    enabled: !!routeJobId,
  })

  // When route job loads, build messages from it
  useEffect(() => {
    if (routeJobQuery.data && routeJobId) {
      const job = routeJobQuery.data
      setMessages([
        {
          id: `user-${job.job_id}`,
          role: 'user',
          content: job.goal,
          jobId: job.job_id,
        },
        {
          id: `assistant-${job.job_id}`,
          role: 'assistant',
          content: job.result?.final_output ?? '',
          jobId: job.job_id,
          status: job.status,
          result: job.result ?? undefined,
          error: job.error ?? undefined,
        },
      ])
      // Don't poll for completed/failed jobs
      if (job.status === 'pending' || job.status === 'running') {
        setActiveJobId(job.job_id)
      } else {
        setActiveJobId(null)
      }
    }
  }, [routeJobQuery.data, routeJobId])

  // Poll active job
  const activeJobQuery = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => api.get<JobDetail>(`/jobs/${activeJobId}`),
    enabled: !!activeJobId && activeJobId !== routeJobId,
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'pending' || status === 'running' ? 2000 : false
    },
  })

  // Update assistant message as polling progresses
  useEffect(() => {
    if (!activeJobQuery.data || !activeJobId) return
    const job = activeJobQuery.data

    setMessages(prev =>
      prev.map(msg =>
        msg.jobId === activeJobId && msg.role === 'assistant'
          ? {
              ...msg,
              status: job.status,
              result: job.result ?? undefined,
              error: job.error ?? undefined,
              content: job.result?.final_output ?? '',
            }
          : msg
      )
    )

    // Stop polling when done
    if (job.status === 'completed' || job.status === 'failed') {
      setActiveJobId(null)
    }
  }, [activeJobQuery.data, activeJobId])

  // Submit a new goal
  const submitMutation = useMutation({
    mutationFn: (goal: string) =>
      api.post<{ job_id: string; status: string }>('/jobs', { goal }),
  })

  function handleSubmit(goal: string) {
    const tempId = crypto.randomUUID()

    // Add user message immediately
    const userMsg: ChatMessageData = {
      id: `user-${tempId}`,
      role: 'user',
      content: goal,
    }

    // Add placeholder assistant message
    const assistantMsg: ChatMessageData = {
      id: `assistant-${tempId}`,
      role: 'assistant',
      content: '',
      status: 'pending',
    }

    setMessages(prev => [...prev, userMsg, assistantMsg])

    submitMutation.mutate(goal, {
      onSuccess: data => {
        // Link the placeholder messages to the real job
        setMessages(prev =>
          prev.map(msg => {
            if (msg.id === `user-${tempId}`) {
              return { ...msg, jobId: data.job_id }
            }
            if (msg.id === `assistant-${tempId}`) {
              return { ...msg, jobId: data.job_id }
            }
            return msg
          })
        )
        setActiveJobId(data.job_id)
      },
      onError: err => {
        setMessages(prev =>
          prev.map(msg =>
            msg.id === `assistant-${tempId}`
              ? { ...msg, status: 'failed', error: (err as Error).message }
              : msg
          )
        )
      },
    })
  }

  const isRunning = !!activeJobId || submitMutation.isPending

  return (
    <div className="chat-page">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <h2>HearthForge</h2>
            <p>Describe what you want to accomplish and agents will work on it.</p>
          </div>
        )}
        {messages.map(msg => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>
      <ChatInput onSubmit={handleSubmit} disabled={isRunning} />
    </div>
  )
}
