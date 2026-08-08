import { useTranslation } from 'react-i18next'

import type { EvalRunProgress } from '@/api/eval'

interface ProgressBarProps {
  progress: EvalRunProgress
}

export default function ProgressBar({ progress }: ProgressBarProps) {
  const { t } = useTranslation()
  const done = progress.done ?? 0
  const total = progress.total ?? 0
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const running = progress.status === 'running' || progress.status === 'queued'
  if (!running) return null
  return (
    <div className="mt-2">
      <div className="text-muted-foreground mb-1 flex items-center justify-between text-xs">
        <span>
          {t('eval.running')} {done}/{total || '?'}
          {progress.phase ? ` · ${progress.phase}` : ''}
        </span>
        <span>{percent}%</span>
      </div>
      <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
        <div
          className="bg-emerald-500 h-full rounded-full transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
