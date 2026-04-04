import type { OrchestratorResult } from '../api'
import MarkdownOutput from './MarkdownOutput'
import TaskDetails from './TaskDetails'

export interface ChatMessageData {
  id: string
  role: 'user' | 'assistant'
  content: string
  jobId?: string
  status?: 'pending' | 'running' | 'completed' | 'failed'
  result?: OrchestratorResult
  error?: string
}

interface ChatMessageProps {
  message: ChatMessageData
}

export default function ChatMessage({ message }: ChatMessageProps) {
  if (message.role === 'user') {
    return (
      <div className="chat-message chat-message-user">
        <div className="chat-bubble chat-bubble-user">
          {message.content}
        </div>
      </div>
    )
  }

  // Assistant message
  const isLoading = message.status === 'pending' || message.status === 'running'
  const isFailed = message.status === 'failed'

  return (
    <div className="chat-message chat-message-assistant">
      <div className="chat-bubble chat-bubble-assistant">
        {isLoading && (
          <div className="chat-loading">
            <span className="polling-dot" />
            {message.status === 'pending' ? 'Starting...' : 'Running...'}
          </div>
        )}

        {isFailed && (
          <div className="chat-error">
            {message.error || 'An error occurred'}
          </div>
        )}

        {message.status === 'completed' && message.result && (
          <>
            <TaskDetails
              runId={message.result.run_id}
              completedTasks={message.result.completed_tasks}
              totalTasks={message.result.total_tasks}
              elapsedSeconds={message.result.elapsed_seconds}
            />
            <MarkdownOutput>{message.result.final_output}</MarkdownOutput>
          </>
        )}
      </div>
    </div>
  )
}
