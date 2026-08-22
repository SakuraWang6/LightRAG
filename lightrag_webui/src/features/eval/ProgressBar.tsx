import { CheckIcon, CircleIcon, LoaderCircleIcon } from 'lucide-react'

import type { EvalRunProgress } from '@/api/eval'
import type { RunCapabilities } from '@/features/eval/runCapabilities'
import { getRunProgressPresentation } from '@/features/eval/runProgress'

interface ProgressBarProps {
  progress: EvalRunProgress
  capabilities: RunCapabilities
}

/**
 * The execution route is a phase timeline, not a whole-run percentage. The
 * runner reuses `done/total` for files, retrieval questions and answer
 * questions, so only the active phase is allowed to describe that quantity.
 */
export default function ProgressBar({ progress, capabilities }: ProgressBarProps) {
  const presentation = getRunProgressPresentation(progress, capabilities)
  if (!presentation.isRunning) return null
  const title = presentation.isQueued ? '等待调度' : presentation.stage?.label ?? '正在运行'

  return (
    <section aria-live="polite" className="mt-3 overflow-hidden rounded-md border bg-muted/[0.14]">
      <div className="flex items-start gap-2.5 border-l-2 border-primary px-3 py-3">
        <LoaderCircleIcon className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-sm font-medium">当前阶段 · {title}</p>
            {presentation.workSummary ? (
              <span className="text-muted-foreground text-xs tabular-nums">{presentation.workSummary}</span>
            ) : null}
          </div>
          {presentation.detail ? (
            <p className="text-muted-foreground mt-1 text-xs leading-5">{presentation.detail}</p>
          ) : null}
          {presentation.meter ? (
            <div className="mt-2.5" aria-label={`${presentation.meter.label}：${presentation.meter.valueLabel}`}>
              <div className="text-muted-foreground mb-1.5 flex items-center justify-between gap-3 text-[11px]">
                <span className="font-medium">{presentation.meter.label}</span>
                <span className="tabular-nums">{presentation.meter.valueLabel}</span>
              </div>
              <div className="bg-muted relative h-1.5 overflow-hidden rounded-full">
                {presentation.meter.mode === 'determinate' ? (
                  <div
                    className="bg-primary h-full rounded-full transition-[width] duration-500 ease-out"
                    style={{ width: `${presentation.meter.percent ?? 0}%` }}
                  />
                ) : (
                  <div className="from-primary/35 via-primary to-primary/35 absolute inset-y-0 left-0 w-2/5 animate-pulse rounded-full bg-gradient-to-r" />
                )}
              </div>
              {presentation.meter.hint ? (
                <p className="text-muted-foreground/80 mt-1.5 text-[11px]">{presentation.meter.hint}</p>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="border-t px-3 py-2.5">
        <ol className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[11px]">
          {presentation.stages.map((stage, index) => {
            const active = stage.state === 'active'
            const complete = stage.state === 'complete'
            return (
              <li key={stage.id} className="flex items-center gap-1.5">
                {complete ? (
                  <CheckIcon className="size-3 text-emerald-600" />
                ) : active ? (
                  <LoaderCircleIcon className="size-3 animate-spin text-primary" />
                ) : (
                  <CircleIcon className="size-2.5 text-muted-foreground/50" />
                )}
                <span className={active ? 'font-medium text-foreground' : complete ? 'text-emerald-700 dark:text-emerald-400' : 'text-muted-foreground'}>
                  {stage.label}
                </span>
                {index < presentation.stages.length - 1 ? <span className="text-muted-foreground/45">→</span> : null}
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}
