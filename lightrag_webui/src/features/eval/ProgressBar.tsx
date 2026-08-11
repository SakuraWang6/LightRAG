import { useTranslation } from 'react-i18next'

import type { EvalRunProgress } from '@/api/eval'

interface ProgressBarProps {
  progress: EvalRunProgress
}

const PHASE_LABELS: Record<string, string> = {
  runtime: '准备独立运行环境',
  ingestion: '文档上传、入库与索引',
  retrieval: '检索评测',
  answer: '回答评测',
  report: '汇总结果'
}

export default function ProgressBar({ progress }: ProgressBarProps) {
  const { t } = useTranslation()
  const done = progress.done ?? 0
  const total = progress.total ?? 0
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const running = progress.status === 'running' || progress.status === 'queued'
  const phase = progress.phase ? (PHASE_LABELS[progress.phase] ?? progress.phase) : null
  const title = phase ?? t('eval.running')
  const detail = progress.message?.trim()
  if (!running) return null
  return (
    <div className="mt-2">
      <div className="text-muted-foreground mb-1 flex items-center justify-between text-xs">
        <span className="min-w-0 truncate">
          <span className="text-foreground font-medium">{title}</span>
          {detail ? <span> · {detail}</span> : null}
        </span>
        <span className="ml-3 shrink-0">{total > 0 ? `已完成 ${done}/${total}` : '处理中'}</span>
      </div>
      <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
        <div
          className="bg-emerald-500 h-full rounded-full transition-all"
          style={{ width: `${percent}%` }}
          aria-label={`${title}：${total > 0 ? `已完成 ${done}/${total}` : '处理中'}`}
        />
      </div>
    </div>
  )
}
