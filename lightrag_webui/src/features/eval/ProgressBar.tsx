import { useTranslation } from 'react-i18next'

import type { EvalRunProgress } from '@/api/eval'

interface ProgressBarProps {
  progress: EvalRunProgress
}

const PHASE_LABELS: Record<string, string> = {
  runtime: '准备独立运行环境',
  ingestion: '文档入库与索引',
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
  if (!running) return null
  return (
    <div className="mt-2">
      <div className="text-muted-foreground mb-1 flex items-center justify-between text-xs">
        <span>{progress.message || (phase ? `当前阶段：${phase}` : t('eval.running'))}</span>
        <span>已完成 {done}/{total || '?'}</span>
      </div>
      <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
        <div
          className="bg-emerald-500 h-full rounded-full transition-all"
          style={{ width: `${percent}%` }}
          aria-label={`已完成 ${done}/${total || '?'}`}
        />
      </div>
    </div>
  )
}
