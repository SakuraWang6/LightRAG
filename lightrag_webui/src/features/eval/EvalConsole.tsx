import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CheckSquareIcon,
  Columns3Icon,
  FolderOpenIcon,
  ListChecksIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon
} from 'lucide-react'
import { toast } from 'sonner'

import {
  getEvalRun,
  listEvalRuns,
  refreshEvalIndex,
  validateRunComparison,
  type EvalRun,
  type EvalRunDetail
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
import DatasetsView from '@/features/eval/DatasetsView'
import SimpleEvalWizard from '@/features/eval/SimpleEvalWizard'
import JobsView from '@/features/eval/JobsView'
import {
  buildReproduceDraft,
  compareCompatible,
  formatDate,
} from '@/features/eval/utils'

type SimpleEvalDraft = {
  name?: string
  dataset: string
  params?: Record<string, unknown>
}

export default function EvalConsole() {
  const { t } = useTranslation()
  const [runs, setRuns] = useState<EvalRun[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [datasetFilter, setDatasetFilter] = useState('all')
  const [search, setSearch] = useState('')

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<EvalRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const detailCache = useRef(new Map<string, EvalRunDetail>())

  const [compareIds, setCompareIds] = useState<Set<string>>(new Set())
  const [comparing, setComparing] = useState(false)
  const [compareRuns, setCompareRuns] = useState<EvalRunDetail[]>([])
  const [compareLoading, setCompareLoading] = useState(false)
  const [view, setView] = useState<'runs' | 'new' | 'datasets' | 'jobs'>('runs')
  const [simpleDraft, setSimpleDraft] = useState<SimpleEvalDraft | null>(null)

  const handleReproduce = useCallback((run: EvalRun) => {
    const draft = buildReproduceDraft(run)
    setSimpleDraft({
      name: `${run.label}（复跑）`,
      dataset: draft.dataset,
      params: draft.params
    })
    setView('new')
  }, [])

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
    const timer = window.setTimeout(() => void loadRuns(), 0)
    return () => window.clearTimeout(timer)
  }, [loadRuns])

  const loadDetail = useCallback(
    async (id: string, force = false) => {
      if (!force) {
        const cached = detailCache.current.get(id)
        if (cached) {
          setDetail(cached)
          return
        }
      }
      setDetailLoading(true)
      try {
        const data = await getEvalRun(id)
        detailCache.current.set(id, data)
        setDetail(data)
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      } finally {
        setDetailLoading(false)
      }
    },
    []
  )

  useEffect(() => {
    if (selectedId) {
      const timer = window.setTimeout(() => void loadDetail(selectedId), 0)
      return () => window.clearTimeout(timer)
    }
  }, [selectedId, loadDetail])

  const handleRefresh = useCallback(async (silent = false) => {
    setRefreshing(true)
    try {
      const result = await refreshEvalIndex()
      if (!silent) toast.success(`已刷新 ${result.run_count} 个测评`)
      detailCache.current.clear()
      await loadRuns()
      if (selectedId) await loadDetail(selectedId, true)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setRefreshing(false)
    }
  }, [loadDetail, loadRuns, selectedId])

  const filteredRuns = useMemo(() => {
    if (!runs) return []
    const needle = search.trim().toLowerCase()
    return runs.filter((run) => {
      if (datasetFilter !== 'all' && run.dataset !== datasetFilter) return false
      if (needle) {
        const haystack = `${run.label} ${run.dataset_display_name ?? ''} ${run.dataset ?? ''} ${run.artifact_titles.join(' ')}`.toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      return true
    })
  }, [runs, datasetFilter, search])

  const datasetOptions = useMemo(() => {
    const values = new Map<string, string>()
    for (const run of runs ?? []) {
      if (run.dataset) values.set(run.dataset, run.dataset_display_name ?? run.dataset)
    }
    return Array.from(values, ([id, label]) => ({ id, label })).sort((left, right) =>
      left.label.localeCompare(right.label)
    )
  }, [runs])

  const hasActiveRuns = useMemo(
    () =>
      (runs ?? []).some((run) =>
        ['running', 'queued'].includes(run.progress?.status ?? '')
      ),
    [runs]
  )

  useEffect(() => {
    // Always poll: while a run is active refresh every 5s, otherwise poll at a
    // low rate so a queued task that starts after the previous one finishes
    // (or fails) appears without a manual refresh.
    const intervalMs = hasActiveRuns ? 5000 : 15000
    const timer = window.setInterval(() => {
      void loadRuns()
      if (selectedId) void loadDetail(selectedId, true)
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [hasActiveRuns, loadRuns, loadDetail, selectedId])

  const toggleCompare = useCallback((id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
        return next
      }
      if (next.size > 0) {
        const firstId = next.values().next().value as string
        const first = runs?.find((run) => run.id === firstId)
        const candidate = runs?.find((run) => run.id === id)
        if (first && candidate && !compareCompatible([first, candidate])) {
          toast.error(t('eval.compareMismatch'))
          return prev
        }
      }
      next.add(id)
      return next
    })
  }, [runs, t])

  const startCompare = useCallback(async () => {
    const ids = Array.from(compareIds)
    if (ids.length === 0) return
    setCompareLoading(true)
    try {
      const contract = await validateRunComparison(ids)
      if (!contract.ranking_permitted) {
        toast.error(`不可并列排名：${contract.incompatible_fields.join('、')}`)
        return
      }
      setComparing(true)
      const loaded = await Promise.all(
        ids.map(async (id) => {
          const cached = detailCache.current.get(id)
          if (cached) return cached
          const data = await getEvalRun(id)
          detailCache.current.set(id, data)
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
  }, [compareIds])

  const stopCompare = useCallback(() => {
    setComparing(false)
    setCompareRuns([])
  }, [])

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

  if (view === 'new') {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1 overflow-hidden">
          <SimpleEvalWizard
            initial={simpleDraft}
            onBack={() => setView('runs')}
            onStarted={() => {
              setView('runs')
              void handleRefresh(true)
              window.setTimeout(() => void handleRefresh(true), 1200)
            }}
          />
        </div>
      </div>
    )
  }

  if (view === 'datasets') {
    return <DatasetsView onBack={() => setView('runs')} />
  }

  if (view === 'jobs') {
    return <JobsView onBack={() => setView('runs')} />
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <h1 className="mr-2 text-base font-semibold">{t('eval.title')}</h1>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" onClick={() => { setSimpleDraft(null); setView('new') }}>
            <PlusIcon className="mr-1 size-4" />
            {t('eval.newRun')}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setView('datasets')}>
            <FolderOpenIcon className="mr-1 size-4" />
            数据集
          </Button>
          <Button size="sm" variant="outline" onClick={() => setView('jobs')}>
            <ListChecksIcon className="mr-1 size-4" />
            {t('eval.jobs')}
          </Button>
          {compareIds.size > 0 && (
            <Button
              size="sm"
              onClick={startCompare}
              disabled={compareLoading || compareIds.size < 2}
            >
              <Columns3Icon className="mr-1 size-4" />
              {t('eval.compare')} ({compareIds.size})
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => void handleRefresh()} disabled={refreshing}>
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
            <div>
              <Select value={datasetFilter} onValueChange={setDatasetFilter}>
                <SelectTrigger className="h-8 w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('eval.datasetAll')}</SelectItem>
                  {datasetOptions.map((dataset) => (
                    <SelectItem key={dataset.id} value={dataset.id}>
                      {dataset.label}
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
                          {run.progress?.status === 'running' ? (
                            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                              <span className="bg-emerald-500 size-1.5 animate-pulse rounded-full" />
                              {t('eval.running')}
                              <span className="tabular-nums">{run.progress.done ?? 0}/{run.progress.total ?? '?'}</span>
                            </span>
                          ) : run.progress?.status === 'queued' ? (
                            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                              <span className="bg-amber-500 size-1.5 rounded-full" />
                              {t('eval.queued')}
                            </span>
                          ) : run.status === 'complete' || run.status === 'succeeded' ? (
                            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                              <span className="bg-emerald-500 size-1.5 rounded-full" />
                              {t('eval.statusComplete')}
                            </span>
                          ) : run.status === 'failed' ? (
                            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-red-600 dark:text-red-400">
                              <span className="bg-red-500 size-1.5 rounded-full" />
                              {t('eval.statusFailed')}
                            </span>
                          ) : run.status === 'cancelled' ? (
                            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                              <span className="bg-muted-foreground size-1.5 rounded-full" />
                              {t('eval.statusCancelled')}
                            </span>
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
                          {run.dataset ? <span className="truncate">{run.dataset_display_name ?? run.dataset}</span> : null}
                          <span>{formatDate(run.updated_at)}</span>
                        </div>
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
            <EvalRunDetailView
              run={detail}
              onReproduce={handleReproduce}
              onDeleted={() => {
                setSelectedId(null)
                void loadRuns()
              }}
            />
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
