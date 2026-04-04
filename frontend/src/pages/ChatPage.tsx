import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../contexts/AuthContext'
import { createApiClient } from '../api'
import type { JobDetail, Conversation } from '../api'
import ChatInput from '../components/ChatInput'
import ChatMessage from '../components/ChatMessage'
import type { ChatMessageData } from '../components/ChatMessage'

function jobToMessages(job: JobDetail): [ChatMessageData, ChatMessageData] {
  return [
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
  ]
}

export default function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const { token } = useAuth()
  const api = createApiClient(token)
  const queryClient = useQueryClient()

  const [messages, setMessages] = useState<ChatMessageData[]>([])
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(
    conversationId ?? null
  )
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Sync conversationId from route into state
  useEffect(() => {
    setCurrentConversationId(conversationId ?? null)
  }, [conversationId])

  // Load conversation jobs when navigating to an existing conversation
  const conversationJobsQuery = useQuery({
    queryKey: ['conversation-jobs', conversationId],
    queryFn: () => api.get<JobDetail[]>(`/conversations/${conversationId}/jobs`),
    enabled: !!conversationId,
  })

  useEffect(() => {
    if (!conversationJobsQuery.data) return
    const msgs: ChatMessageData[] = []
    for (const job of conversationJobsQuery.data) {
      msgs.push(...jobToMessages(job))
    }
    setMessages(msgs)

    // If the last job is still running, resume polling it
    const last = conversationJobsQuery.data[conversationJobsQuery.data.length - 1]
    if (last && (last.status === 'pending' || last.status === 'running')) {
      setActiveJobId(last.job_id)
    } else {
      setActiveJobId(null)
    }
  }, [conversationJobsQuery.data])

  // Poll active job
  const activeJobQuery = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => api.get<JobDetail>(`/jobs/${activeJobId}`),
    enabled: !!activeJobId,
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'pending' || status === 'running' ? 2000 : false
    },
  })

  // Update the in-flight assistant message as polling progresses
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

    if (job.status === 'completed' || job.status === 'failed') {
      setActiveJobId(null)
      // Refresh conversation list in sidebar
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    }
  }, [activeJobQuery.data, activeJobId])

  // Create conversation mutation
  const createConversationMutation = useMutation({
    mutationFn: (title: string) =>
      api.post<Conversation>('/conversations', { title }),
  })

  // Submit job mutation
  const submitJobMutation = useMutation({
    mutationFn: ({ goal, conversation_id }: { goal: string; conversation_id: string }) =>
      api.post<JobDetail>('/jobs', { goal, conversation_id }),
  })

  async function handleSubmit(goal: string) {
    const tempId = crypto.randomUUID()

    // Add user + placeholder assistant messages immediately
    setMessages(prev => [
      ...prev,
      { id: `user-${tempId}`, role: 'user', content: goal },
      { id: `assistant-${tempId}`, role: 'assistant', content: '', status: 'pending' as const },
    ])

    try {
      // Create a conversation if this is a new chat
      let convId = currentConversationId
      if (!convId) {
        const conv = await createConversationMutation.mutateAsync(
          goal.length > 50 ? goal.slice(0, 50) + '...' : goal
        )
        convId = conv.conversation_id
        setCurrentConversationId(convId)
        navigate(`/chat/${convId}`, { replace: true })
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      }

      const job = await submitJobMutation.mutateAsync({ goal, conversation_id: convId })

      // Link placeholder messages to the real job
      setMessages(prev =>
        prev.map(msg => {
          if (msg.id === `user-${tempId}`) return { ...msg, jobId: job.job_id }
          if (msg.id === `assistant-${tempId}`) return { ...msg, jobId: job.job_id, status: job.status as 'pending' }
          return msg
        })
      )
      setActiveJobId(job.job_id)
    } catch (err) {
      setMessages(prev =>
        prev.map(msg =>
          msg.id === `assistant-${tempId}`
            ? { ...msg, status: 'failed' as const, error: (err as Error).message }
            : msg
        )
      )
    }
  }

  const isRunning = !!activeJobId || createConversationMutation.isPending || submitJobMutation.isPending

  return (
    <div className="chat-page">
      <div className="chat-messages">
        {messages.length === 0 && !conversationJobsQuery.isLoading && (
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
