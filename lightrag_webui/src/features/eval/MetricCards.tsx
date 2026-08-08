import { CheckIcon, XIcon } from 'lucide-react'

import type { MetricItem } from '@/api/eval'
import { Card } from '@/components/ui/Card'
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
    <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
      {visible.map((metric) => (
        <Card key={metric.key} className="p-3">
          <p className="text-muted-foreground truncate text-xs" title={metric.label}>
            {metric.label}
          </p>
          <p
            className={`mt-1 flex items-center gap-1 text-base font-semibold ${
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
          </p>
        </Card>
      ))}
    </div>
  )
}
