import ReactMarkdown, { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'

interface MarkdownReportProps {
  content: string
  components?: Components
}

export default function MarkdownReport({ content, components }: MarkdownReportProps) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1.5 [&_th]:text-left [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_tr]:odd:bg-muted/30 prose-p:my-3 prose-p:leading-7 prose-li:my-1.5 prose-ul:my-3 prose-ol:my-3 prose-headings:scroll-mt-24 prose-h2:mt-8 prose-h3:mt-6 prose-hr:my-6 [&_blockquote]:border-l-4 [&_blockquote]:pl-3">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
