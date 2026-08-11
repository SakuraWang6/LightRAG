import { CheckIcon, XIcon } from 'lucide-react'

import type { MetricItem } from '@/api/eval'
import { formatMetricValue } from '@/features/eval/utils'

interface MetricCardsProps {
  metrics: MetricItem[]
}

export default function MetricCards({ metrics }: MetricCardsProps) {
  const visible = metrics.filter((metric) => metric.value !== null)
  if (visible.length === 0) {
    return <p className="text-muted-foreground text-sm">—</p>
  }
  return (
    <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
      {visible.map((metric) => (
        <div key={metric.key} className="flex min-w-0 items-center justify-between gap-4 rounded-md border bg-card px-3 py-2.5">
          <dt className="text-muted-foreground truncate text-sm" title={metric.label}>
            {metric.label}
          </dt>
          <dd
            className={`flex shrink-0 items-center gap-1 text-base font-semibold ${
              metric.value === false
                ? 'text-red-600 dark:text-red-400'
                : metric.value === true
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : ''
            }`}
            title={typeof metric.value === 'string' ? metric.value : undefined}
          >
            {metric.value === true ? (
              <CheckIcon className="size-4" />
            ) : metric.value === false ? (
              <XIcon className="size-4" />
            ) : (
              formatMetricValue(metric.value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
