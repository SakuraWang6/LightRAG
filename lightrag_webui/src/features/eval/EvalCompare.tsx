import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, DownloadIcon } from 'lucide-react'

import type { EvalRunDetail, MetricItem } from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/Table'
import {
  buildCompareCsv,
  formatDelta,
  formatMetricCell,
  metricStats,
  metricRank
} from '@/features/eval/utils'

interface EvalCompareProps {
  runs: EvalRunDetail[]
  onBack: () => void
}

function collectMetrics(run: EvalRunDetail): Map<string, MetricItem> {
  const map = new Map<string, MetricItem>()
  for (const [key, metric] of Object.entries(run.headline || {})) {
    map.set(key, metric)
  }
  for (const artifact of run.artifacts) {
    for (const metric of artifact.metrics) {
      if (!map.has(metric.key)) {
        map.set(metric.key, metric)
      }
    }
  }
  return map
}

export default function EvalCompare({ runs, onBack }: EvalCompareProps) {
  const { t } = useTranslation()
  const [baselineId, setBaselineId] = useState<string | null>(null)

  const baselineIndex = useMemo(() => {
    if (baselineId) {
      const index = runs.findIndex((run) => run.id === baselineId)
      if (index >= 0) return index
    }
    let latest = 0
    runs.forEach((run, index) => {
      if ((run.updated_at ?? '') > (runs[latest].updated_at ?? '')) latest = index
    })
    return runs.length ? latest : null
  }, [baselineId, runs])

  const conditionKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const run of runs) {
      for (const condition of run.conditions) {
        keys.add(condition.key)
      }
    }
    return Array.from(keys)
  }, [runs])

  const conditionLabel = (key: string) => runs.find((run) =>
    run.conditions.some((condition) => condition.key === key)
  )?.conditions.find((condition) => condition.key === key)?.label ?? key

  const rows = useMemo(() => {
    const perRun = runs.map((run) => ({ run, metrics: collectMetrics(run) }))
    const keys = new Set<string>()
    for (const entry of perRun) {
      for (const key of entry.metrics.keys()) {
        keys.add(key)
      }
    }
    return Array.from(keys)
      .sort((a, b) => metricRank(a) - metricRank(b) || a.localeCompare(b))
      .map((key) => {
        const label = perRun.find((entry) => entry.metrics.has(key))?.metrics.get(key)?.label ?? key
        return { key, label, values: perRun.map((entry) => entry.metrics.get(key)?.value ?? null) }
      })
  }, [runs])

  const exportCsv = () => {
    const csv = buildCompareCsv(runs, rows, baselineIndex)
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'eval-compare.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">{t('eval.compare')}</h2>
        <span className="text-muted-foreground text-xs">{runs.length} runs</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-muted-foreground text-xs">{t('eval.baselineRun')}</span>
          <Select
            value={baselineIndex != null ? runs[baselineIndex]?.id : undefined}
            onValueChange={(value) => setBaselineId(value)}
          >
            <SelectTrigger className="h-8 w-56">
              <SelectValue placeholder="—" />
            </SelectTrigger>
            <SelectContent>
              {runs.map((run) => (
                <SelectItem key={run.id} value={run.id}>
                  <span className="max-w-[180px] truncate">{run.label}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={exportCsv}>
            <DownloadIcon className="mr-1 size-4" />
            {t('eval.exportCsv')}
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t('eval.compareMetrics')}</CardTitle>
          </CardHeader>
          <CardContent>
            {conditionKeys.length > 0 ? (
              <div className="mb-4">
                <p className="text-muted-foreground mb-2 text-xs font-medium">{t('eval.conditions')}</p>
                <div className="overflow-auto rounded-md border">
                  <Table className="min-w-full text-left text-sm">
                    <TableHeader className="sticky top-0 bg-background">
                      <TableRow>
                        <TableHead className="px-3 py-2">{t('eval.condition')}</TableHead>
                        {runs.map((run) => (
                          <TableHead key={run.id} className="px-3 py-2">
                            <span className="max-w-[160px] truncate font-semibold" title={run.label}>
                              {run.label}
                            </span>
                          </TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {conditionKeys.map((key) => (
                        <TableRow key={key}>
                          <TableCell className="text-muted-foreground whitespace-nowrap px-3 py-2">
                            {conditionLabel(key)}
                          </TableCell>
                          {runs.map((run) => {
                            const value = run.conditions.find((condition) => condition.key === key)?.value
                            return (
                              <TableCell key={run.id} className="whitespace-nowrap px-3 py-2">
                                {value ?? '—'}
                              </TableCell>
                            )
                          })}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            ) : null}
            <div className="overflow-auto rounded-md border">
              <Table className="min-w-full text-left text-sm">
                <TableHeader className="sticky top-0 bg-background">
                  <TableRow>
                    <TableHead className="px-3 py-2">{t('eval.metric')}</TableHead>
                    {runs.map((run) => (
                      <TableHead key={run.id} className="px-3 py-2">
                        <span className="flex flex-col gap-1">
                          <span className="max-w-[160px] truncate font-semibold" title={run.label}>
                            {run.label}
                          </span>
                          {runs.indexOf(run) === baselineIndex ? (
                            <Badge variant="outline" className="w-fit text-[10px]">
                              {t('eval.baselineSuffix')}
                            </Badge>
                          ) : null}
                        </span>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell className="text-muted-foreground whitespace-nowrap px-3 py-2">
                        <span>{row.label}</span>
                        {(() => {
                          const stats = metricStats(row.values)
                          return stats.n > 0 ? (
                            <span className="text-muted-foreground/70 block text-[10px]">
                              n={stats.n}
                              {stats.sigma != null
                                ? ` σ=${Number(stats.sigma).toFixed(4).replace(/\.?0+$/, '')}`
                                : ''}
                            </span>
                          ) : null
                        })()}
                      </TableCell>
                      {row.values.map((value, index) => (
                        <TableCell key={index} className="whitespace-nowrap px-3 py-2">
                          {formatMetricCell(value)}
                          {baselineIndex != null && index !== baselineIndex ? (
                            <span className="text-muted-foreground ml-1 text-[10px]">
                              {formatDelta(value, row.values[baselineIndex])}
                            </span>
                          ) : null}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                  {rows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={runs.length + 1} className="text-muted-foreground h-24 text-center">
                        {t('eval.noMetrics')}
                      </TableCell>
                    </TableRow>
                  ) : null}
                </TableBody>
              </Table>
            </div>
            <p className="text-muted-foreground mt-2 text-xs">
              {t('eval.compareNote', { count: runs.length })}
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
