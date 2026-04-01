import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  children: string
  maxHeight?: number | string
}

export default function MarkdownOutput({ children, maxHeight }: Props) {
  return (
    <div className="markdown-output" style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}
