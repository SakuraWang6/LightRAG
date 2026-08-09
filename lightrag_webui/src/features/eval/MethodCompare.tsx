import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import type { TableData } from '@/api/eval'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import EvalDataTable from '@/features/eval/EvalDataTable'
import { formatMetricValue } from '@/features/eval/utils'

const CHART_METRICS = [
  'answer_accuracy',
  'groundedness',
  'ungrounded_rate',
  'evidence_available',
  'average_recall',
  'candidate_recall',
  'selected_recall',
  'role_coverage',
  'selection_precision'
]

interface MethodCompareProps {
  table: TableData
}

export default function MethodCompare({ table }: MethodCompareProps) {
  const { t } = useTranslation()

  const rows = useMemo(() => table.rows, [table.rows])
  const nameKey = rows.some((row) => row.method) ? 'method' : rows.some((row) => row.arm) ? 'arm' : 'label'
  const chartable = CHART_METRICS.filter((key) =>
    rows.some((row) => typeof row[key] === 'number')
  )

  return (
    <div className="space-y-4">
      {chartable.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('eval.methodCompare')}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-2">
            {chartable.map((metric) => {
              const values = rows.map((row) => Number(row[metric]) || 0)
              const max = Math.max(...values, 1e-9)
              const metricLabel =
                (table.columns.find((column) => column.key === metric)?.label) ?? metric
              return (
                <div key={metric}>
                  <p className="text-muted-foreground mb-2 text-xs">{metricLabel}</p>
                  <div className="space-y-1.5">
                    {rows.map((row, index) => {
                      const value = Number(row[metric])
                      const name = String(row[nameKey] ?? `#${index + 1}`)
                      return (
                        <div key={index} className="flex items-center gap-2">
                          <span className="w-32 shrink-0 truncate text-xs" title={name}>
                            {name}
                          </span>
                          <div className="bg-muted h-4 flex-1 overflow-hidden rounded">
                            <div
                              className="bg-primary h-full rounded transition-all"
                              style={{ width: `${Number.isFinite(value) ? (value / max) * 100 : 0}%` }}
                            />
                          </div>
                          <span className="w-12 shrink-0 text-right text-xs tabular-nums">
                            {formatMetricValue(Number.isFinite(value) ? value : null)}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{t('eval.methodTable')}</CardTitle>
        </CardHeader>
        <CardContent>
          <EvalDataTable data={table} maxHeight="max-h-[50vh]" />
        </CardContent>
      </Card>
    </div>
  )
}
