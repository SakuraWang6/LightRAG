import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCwIcon, Columns3Icon, SearchIcon, CheckSquareIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  getEvalRun,
  listEvalRuns,
  refreshEvalIndex,
  type EvalRun,
  type EvalRunDetail,
  type EvalRunKind
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import EmptyCard from '@/components/ui/EmptyCard'
import Input from '@/components/ui/Input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import EvalRunDetailView from '@/features/eval/EvalRunDetail'
import EvalCompare from '@/features/eval/EvalCompare'
import ConditionChips from '@/features/eval/ConditionChips'
import { formatDate, runKindClass, statusBadgeClass, statusLabel } from '@/features/eval/utils'

const KIND_OPTIONS: { value: EvalRunKind | 'all'; labelKey: string }[] = [
  { value: 'all', labelKey: 'eval.kindAll' },
  { value: 'offline', labelKey: 'eval.kindOffline' },
  { value: 'online', labelKey: 'eval.kindOnline' },
  { value: 'experiment', labelKey: 'eval.kindExperiment' },
  { value: 'report', labelKey: 'eval.kindReport' }
]

export default function EvalConsole() {
  const { t } = useTranslation()
  const [runs, setRuns] = useState<EvalRun[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [kindFilter, setKindFilter] = useState<EvalRunKind | 'all'>('all')
  const [datasetFilter, setDatasetFilter] = useState('all')
  const [search, setSearch] = useState('')

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<EvalRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const detailCache = useMemo(() => new Map<string, EvalRunDetail>(), [])

  const [compareIds, setCompareIds] = useState<Set<string>>(new Set())
  const [comparing, setComparing] = useState(false)
  const [compareRuns, setCompareRuns] = useState<EvalRunDetail[]>([])
  const [compareLoading, setCompareLoading] = useState(false)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listEvalRuns()
      setRuns(data.runs)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  const loadDetail = useCallback(
    async (id: string, force = false) => {
      if (!force) {
        const cached = detailCache.get(id)
        if (cached) {
          setDetail(cached)
          return
        }
      }
      setDetailLoading(true)
      try {
        const data = await getEvalRun(id)
        detailCache.set(id, data)
        setDetail(data)
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      } finally {
        setDetailLoading(false)
      }
    },
    [detailCache]
  )

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId)
    }
  }, [selectedId, loadDetail])

  const handleRefresh = useCallback(async () => {
    setRefreshing(true)
    try {
      const result = await refreshEvalIndex()
      toast.success(`${result.run_count} runs indexed`)
      await loadRuns()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setRefreshing(false)
    }
  }, [loadRuns])

  const filteredRuns = useMemo(() => {
    if (!runs) return []
    const needle = search.trim().toLowerCase()
    return runs.filter((run) => {
      if (kindFilter !== 'all' && run.kind !== kindFilter) return false
      if (datasetFilter !== 'all' && run.dataset !== datasetFilter) return false
      if (needle) {
        const haystack = `${run.label} ${run.dataset ?? ''} ${run.artifact_titles.join(' ')}`.toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      return true
    })
  }, [runs, kindFilter, datasetFilter, search])

  const datasetOptions = useMemo(() => {
    const values = new Set<string>()
    for (const run of runs ?? []) {
      if (run.dataset) values.add(run.dataset)
    }
    return Array.from(values).sort()
  }, [runs])

  const hasActiveRuns = useMemo(
    () =>
      (runs ?? []).some((run) =>
        ['running', 'queued'].includes(run.progress?.status ?? '')
      ),
    [runs]
  )

  useEffect(() => {
    if (!hasActiveRuns) return
    const timer = window.setInterval(() => {
      void loadRuns()
      if (selectedId) void loadDetail(selectedId, true)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [hasActiveRuns, loadRuns, loadDetail, selectedId])

  const toggleCompare = useCallback((id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const startCompare = useCallback(async () => {
    const ids = Array.from(compareIds)
    if (ids.length === 0) return
    setCompareLoading(true)
    setComparing(true)
    try {
      const loaded = await Promise.all(
        ids.map(async (id) => {
          const cached = detailCache.get(id)
          if (cached) return cached
          const data = await getEvalRun(id)
          detailCache.set(id, data)
          return data
        })
      )
      setCompareRuns(loaded)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
      setComparing(false)
    } finally {
      setCompareLoading(false)
    }
  }, [compareIds, detailCache])

  const stopCompare = useCallback(() => {
    setComparing(false)
    setCompareRuns([])
  }, [])

  const runCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const run of runs ?? []) {
      counts[run.kind] = (counts[run.kind] ?? 0) + 1
    }
    return counts
  }, [runs])

  if (comparing) {
    return (
      <div className="h-full">
        {compareLoading ? (
          <div className="flex h-full items-center justify-center text-sm">{t('eval.loading')}</div>
        ) : (
          <EvalCompare runs={compareRuns} onBack={stopCompare} />
        )}
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <h1 className="mr-2 text-base font-semibold">{t('eval.title')}</h1>
        {KIND_OPTIONS.slice(1).map((option) => (
          <Badge key={option.value} variant="outline" className="text-muted-foreground text-xs">
            {t(option.labelKey)} {runCounts[option.value] ?? 0}
          </Badge>
        ))}
        <div className="ml-auto flex items-center gap-2">
          {compareIds.size > 0 && (
            <Button size="sm" onClick={startCompare} disabled={compareLoading}>
              <Columns3Icon className="mr-1 size-4" />
              {t('eval.compare')} ({compareIds.size})
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={handleRefresh} disabled={refreshing}>
            <RefreshCwIcon className={`mr-1 size-4 ${refreshing ? 'animate-spin' : ''}`} />
            {t('eval.refresh')}
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="flex w-[340px] shrink-0 flex-col border-r">
          <div className="space-y-2 border-b p-3">
            <div className="relative">
              <SearchIcon className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
              <Input
                className="pl-8"
                placeholder={t('eval.searchPlaceholder')}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <Select value={kindFilter} onValueChange={(value) => setKindFilter(value as EvalRunKind | 'all')}>
                <SelectTrigger className="h-8 flex-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KIND_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {t(option.labelKey)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={datasetFilter} onValueChange={setDatasetFilter}>
                <SelectTrigger className="h-8 flex-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('eval.datasetAll')}</SelectItem>
                  {datasetOptions.map((dataset) => (
                    <SelectItem key={dataset} value={dataset}>
                      {dataset}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {loading && !runs ? (
              <div className="p-4 text-sm">{t('eval.loading')}</div>
            ) : filteredRuns.length === 0 ? (
              <div className="p-4 text-sm text-muted-foreground">{t('eval.noRuns')}</div>
            ) : (
              filteredRuns.map((run) => {
                const selected = selectedId === run.id
                const compareChecked = compareIds.has(run.id)
                return (
                  <div
                    key={run.id}
                    className={`cursor-pointer border-b px-3 py-2.5 transition-colors ${
                      selected ? 'bg-primary/10' : 'hover:bg-accent/60'
                    }`}
                    onClick={() => {
                      setSelectedId(run.id)
                      setComparing(false)
                    }}
                  >
                    <div className="flex items-start gap-2">
                      <button
                        type="button"
                        aria-label={t('eval.selectForCompare')}
                        className="mt-0.5 shrink-0"
                        onClick={(event) => {
                          event.stopPropagation()
                          toggleCompare(run.id)
                        }}
                      >
                        <CheckSquareIcon
                          className={`size-4 ${compareChecked ? 'text-primary' : 'text-muted-foreground/50'}`}
                        />
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate text-sm font-medium" title={run.id}>
                            {run.label}
                          </span>
                          <Badge variant="outline" className={`text-[10px] ${runKindClass(run.kind)}`}>
                            {run.kind}
                          </Badge>
                          {run.legacy ? (
                            <Badge variant="outline" className="text-[10px] text-amber-600 dark:text-amber-400">
                              {t('eval.legacyRun')}
                            </Badge>
                          ) : null}
                          {(run.restarts ?? 0) > 0 ? (
                            <Badge variant="outline" className="text-[10px] text-amber-600 dark:text-amber-400">
                              {t('eval.restarts', { count: run.restarts ?? 0 })}
                              {run.last_restart_resume != null
                                ? run.last_restart_resume
                                  ? t('eval.restartResume')
                                  : t('eval.restartFresh')
                                : ''}
                            </Badge>
                          ) : null}
                        </div>
                        <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-1 text-[11px]">
                          {run.dataset ? <span className="truncate">{run.dataset}</span> : null}
                          {run.status ? (
                            <Badge variant="outline" className={`text-[10px] ${statusBadgeClass(run.status)}`}>
                              {statusLabel(run)}
                            </Badge>
                          ) : null}
                          <span>{formatDate(run.updated_at)}</span>
                        </div>
                        {['running', 'queued'].includes(run.progress?.status ?? '') ? (
                          <div className="text-emerald-600 dark:text-emerald-400 mt-1 text-[11px] font-medium">
                            ● {t('eval.running')} {run.progress.done ?? 0}/{run.progress.total ?? '?'}
                          </div>
                        ) : null}
                        <div className="mt-1.5">
                          <ConditionChips conditions={run.conditions} limit={3} />
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-hidden">
          {detailLoading && !detail ? (
            <div className="flex h-full items-center justify-center text-sm">{t('eval.loading')}</div>
          ) : detail ? (
            <EvalRunDetailView run={detail} />
          ) : (
            <EmptyCard
              title={t('eval.noSelection')}
              description={t('eval.noSelectionHint')}
            />
          )}
        </main>
      </div>
    </div>
  )
}
