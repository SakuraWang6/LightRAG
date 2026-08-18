import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, FileTextIcon, ListIcon, SearchCheckIcon, MessageSquareTextIcon, WaypointsIcon, BarChart3Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  cancelEvalJob,
  deleteEvalRun,
  listEvalJobs,
  type EvalArtifact,
  type EvalJob,
  type EvalRunDetail,
  type MetricItem
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import EmptyCard from '@/components/ui/EmptyCard'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import CasesView from '@/features/eval/CasesView'
import ConditionChips from '@/features/eval/ConditionChips'
import EvaluationSummary from '@/features/eval/EvaluationSummary'
import MetricCards from '@/features/eval/MetricCards'
import ProgressBar from '@/features/eval/ProgressBar'
import ReportDocument from '@/features/eval/ReportDocument'
import RunLog from '@/features/eval/RunLog'
import {
  getRunCapabilities,
  metricDomain,
  runKindLabel,
  runTitle,
  type MetricDomain,
  type RunCapabilities
} from '@/features/eval/runCapabilities'
import { evalStatusLabel, formatDate, statusBadgeClass } from '@/features/eval/utils'

interface EvalRunDetailProps {
  run: EvalRunDetail
  onReproduce?: (run: EvalRunDetail) => void
  onDeleted?: () => void
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m ${Math.floor(seconds % 60)}s`
}

function RunKindBadge({ capabilities }: { capabilities: RunCapabilities }) {
  return (
    <>
      <Badge variant={capabilities.hasAnswer ? 'default' : 'secondary'} className="text-[10px]">
        {runKindLabel(capabilities)}
      </Badge>
      {capabilities.hasDetailedDiagnostics ? <Badge variant="outline" className="text-[10px]">DETAILED</Badge> : null}
    </>
  )
}

function RunHeader({
  run,
  capabilities,
  activeJob,
  onCancel,
  onReproduce,
  onDelete
}: {
  run: EvalRunDetail
  capabilities: RunCapabilities
  activeJob?: EvalJob | null
  onCancel?: () => void
  onReproduce?: () => void
  onDelete?: () => void
}) {
  const { t } = useTranslation()
  const isRunning = useMemo(
    () =>
      ['running', 'queued', 'pending'].includes(run.progress?.status ?? '') ||
      ['running', 'queued', 'pending'].includes(run.status ?? '') ||
      Boolean(activeJob),
    [run.progress?.status, run.status, activeJob]
  )
  const isStarted = useMemo(
    () => run.progress?.status === 'running' || run.status === 'running',
    [run.progress?.status, run.status]
  )
  const [now, setNow] = useState(0)
  useEffect(() => {
    if (!isRunning) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [isRunning])
  const elapsedSeconds = useMemo(() => {
    if (!run.started_at) return null
    const started = new Date(run.started_at).getTime()
    if (Number.isNaN(started)) return null
    return Math.max(0, (now - started) / 1000)
  }, [run.started_at, now])

  return (
    <div className="border-b px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{runTitle(capabilities)}</h2>
        <RunKindBadge capabilities={capabilities} />
        <span className="text-muted-foreground max-w-[360px] truncate text-sm" title={run.label}>{run.label}</span>
        {(run.restarts ?? 0) > 0 ? (
          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300">
            {t('eval.restarts', { count: run.restarts ?? 0 })}
            {run.last_restart_resume != null ? run.last_restart_resume ? t('eval.restartResume') : t('eval.restartFresh') : ''}
          </Badge>
        ) : null}
        {run.status ? <Badge variant="outline" className={statusBadgeClass(run.status)}>{t(evalStatusLabel(run.status))}</Badge> : null}
        {activeJob ? <Button size="sm" variant="outline" onClick={onCancel} className="ml-auto">{t('eval.cancelRun')}</Button> : null}
        {onDelete ? <Button size="sm" variant="outline" className="text-destructive" onClick={onDelete}>{t('eval.deleteRun')}</Button> : null}
        {onReproduce ? <Button size="sm" variant="outline" onClick={onReproduce}>{t('eval.reproduce')}</Button> : null}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">
        {t('eval.updatedAt')}: {formatDate(run.updated_at)}
        {isStarted && elapsedSeconds != null ? <> · {t('eval.elapsed')}: {formatDuration(elapsedSeconds)}</> : null}
        {run.duration_seconds != null ? <> · {t('eval.duration')}: {run.duration_seconds}s</> : null}
        {' · '}{run.artifacts.length} {t('eval.artifactLabel')}
      </p>
      {run.failure ? (
        <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm">
          <span className="font-medium">{t('eval.failed')} · {run.failure.phase}: </span>
          {run.failure.summary}<span className="text-muted-foreground"> · {run.failure.recommendation}</span>
        </div>
      ) : null}
      <div className="mt-2"><ConditionChips conditions={run.conditions} limit={10} /></div>
      <ProgressBar progress={run.progress} capabilities={capabilities} />
    </div>
  )
}

function visibleMetrics(artifact: EvalArtifact, domain: MetricDomain): MetricItem[] {
  return artifact.metrics.filter((metric) => metricDomain(metric.key) === domain)
}

function DomainMetrics({ artifacts, domain, title }: { artifacts: EvalArtifact[]; domain: MetricDomain; title: string }) {
  const entries = artifacts
    .map((artifact) => ({ artifact, metrics: visibleMetrics(artifact, domain) }))
    .filter((entry) => entry.metrics.length > 0 || Boolean(entry.artifact.error))
  if (entries.length === 0) return <EmptyCard title={`暂无${title}`} description="该运行尚未产生可展示的结果。" />
  return (
    <div className="space-y-3">
      {entries.map(({ artifact, metrics }) => (
        <Card key={artifact.rel_path}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              {artifact.title}<Badge variant="outline" className="text-[10px]">{artifact.kind}</Badge>
            </CardTitle>
            <CardDescription className="text-xs">{artifact.updated_at ? formatDate(artifact.updated_at) : artifact.rel_path}</CardDescription>
          </CardHeader>
          <CardContent>{artifact.error ? <p className="text-destructive text-sm">{artifact.error}</p> : <MetricCards metrics={metrics} />}</CardContent>
        </Card>
      ))}
    </div>
  )
}

function rowRetrieval(row: Record<string, unknown>): Record<string, unknown> {
  const detail = row.detail && typeof row.detail === 'object' && !Array.isArray(row.detail)
    ? row.detail as Record<string, unknown>
    : {}
  const nested = row.retrieval ?? detail.retrieval
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) return nested as Record<string, unknown>
  const retrieval: Record<string, unknown> = {}
  for (const key of ['recall_at_k', 'first_evidence_rank']) {
    const value = detail[key] ?? row[key]
    if (value !== undefined) retrieval[key] = value
  }
  return retrieval
}

function FailureAnalysis({ rows, capabilities }: { rows: Record<string, unknown>[]; capabilities: RunCapabilities }) {
  const retrievalRows = rows.map(rowRetrieval).filter((retrieval) => typeof retrieval.recall_at_k === 'number')
  const missed = retrievalRows.filter((retrieval) => Number(retrieval.recall_at_k) < 1).length
  const late = retrievalRows.filter((retrieval) => typeof retrieval.first_evidence_rank === 'number' && Number(retrieval.first_evidence_rank) > 1).length
  const answerRows = rows.filter((row) => row.exact_match === false && row.answer_verdict !== 'uncertain')
  if (retrievalRows.length === 0 && answerRows.length === 0) return <EmptyCard title="暂无失败分析" description="需要案例级结果后才能生成分析。" />
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{capabilities.hasAnswer ? '失败分析' : '召回失败分析'}</CardTitle>
        <CardDescription className="text-xs">{capabilities.hasAnswer ? '按检索、排序和回答结果定位优先排查环节。' : '按未召回与 Gold Rank 聚合检索问题。'}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          <div className="rounded-md border bg-card px-3 py-2.5"><dt className="text-muted-foreground text-sm">未完整召回</dt><dd className="mt-1 text-base font-semibold">{missed} 题</dd></div>
          {capabilities.hasGoldRank ? <div className="rounded-md border bg-card px-3 py-2.5"><dt className="text-muted-foreground text-sm">Gold Rank 靠后</dt><dd className="mt-1 text-base font-semibold">{late} 题</dd></div> : null}
          {capabilities.hasAnswer ? <div className="rounded-md border bg-card px-3 py-2.5"><dt className="text-muted-foreground text-sm">回答未通过</dt><dd className="mt-1 text-base font-semibold">{answerRows.length} 题</dd></div> : null}
        </dl>
      </CardContent>
    </Card>
  )
}

function RetrievalBreakdown({ rows }: { rows: Record<string, unknown>[] }) {
  const groups = new Map<string, { cases: number; complete: number; recall: number; recallCount: number }>()
  for (const row of rows) {
    const retrieval = rowRetrieval(row)
    if (typeof retrieval.recall_at_k !== 'number') continue
    const type = typeof row.question_type === 'string' && row.question_type.trim() ? row.question_type : '未标注题型'
    const group = groups.get(type) ?? { cases: 0, complete: 0, recall: 0, recallCount: 0 }
    const recall = Number(retrieval.recall_at_k)
    group.cases += 1
    group.complete += recall === 1 ? 1 : 0
    group.recall += recall
    group.recallCount += 1
    groups.set(type, group)
  }
  if (groups.size === 0) return null
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">分类表现</CardTitle>
        <CardDescription className="text-xs">按题型汇总召回覆盖情况。</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-auto rounded-md border">
          <table className="w-full min-w-[440px] text-left text-sm">
            <thead className="bg-muted/40 text-muted-foreground text-xs">
              <tr><th className="px-3 py-2 font-medium">题型</th><th className="px-3 py-2 font-medium">案例</th><th className="px-3 py-2 font-medium">完整召回</th><th className="px-3 py-2 font-medium">平均 Recall@K</th></tr>
            </thead>
            <tbody>
              {Array.from(groups.entries()).sort(([left], [right]) => left.localeCompare(right)).map(([type, group]) => (
                <tr key={type} className="border-t"><td className="px-3 py-2">{type}</td><td className="px-3 py-2 tabular-nums">{group.cases}</td><td className="px-3 py-2 tabular-nums">{group.complete} / {group.cases}</td><td className="px-3 py-2 tabular-nums">{(group.recall / group.recallCount).toFixed(4).replace(/\.?0+$/, '')}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}

function StandardRunView({ run, active, capabilities }: { run: EvalRunDetail; active: boolean; capabilities: RunCapabilities }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('overview')
  const caseArtifact = useMemo(() => run.artifacts.find((artifact) => artifact.kind === 'cases'), [run.artifacts])
  const metricArtifacts = useMemo(() => run.artifacts.filter((artifact) => artifact.metrics.length > 0 || artifact.error), [run.artifacts])
  const reportArtifact = useMemo(() => run.artifacts.find((artifact) => artifact.report_md), [run.artifacts])
  const caseRows = caseArtifact?.table.rows ?? []
  const hasRetrievalTab = capabilities.hasRetrievalMetrics
  const hasAnswerTab = capabilities.hasAnswerMetrics
  const hasCasesTab = capabilities.hasRetrievalDetails || capabilities.hasAnswerDetails
  const allowedTabs = new Set([
    'overview',
    'log',
    ...(hasRetrievalTab ? ['retrieval'] : []),
    ...(hasAnswerTab ? ['answer'] : []),
    ...(capabilities.hasFailureAnalysis ? ['analysis'] : []),
    ...(hasCasesTab ? ['cases'] : []),
    ...(reportArtifact ? ['reports'] : [])
  ])
  const visibleTab = allowedTabs.has(tab) ? tab : 'overview'

  return (
    <div className="p-4">
      {run.artifacts.some((artifact) => artifact.error) ? (
        <div className="bg-destructive/10 text-destructive mb-4 flex items-center gap-2 rounded-md border border-destructive/30 px-3 py-2 text-sm">
          <AlertTriangle className="size-4" />{t('eval.artifactErrors')}
        </div>
      ) : null}
      <Tabs value={visibleTab} onValueChange={setTab} className="w-full">
        <TabsList className="h-auto flex-wrap justify-start">
          <TabsTrigger value="overview"><BarChart3Icon className="mr-1 size-4" />总览</TabsTrigger>
          {hasRetrievalTab ? <TabsTrigger value="retrieval"><SearchCheckIcon className="mr-1 size-4" />检索</TabsTrigger> : null}
          {hasAnswerTab ? <TabsTrigger value="answer"><MessageSquareTextIcon className="mr-1 size-4" />回答</TabsTrigger> : null}
          {capabilities.hasFailureAnalysis ? <TabsTrigger value="analysis"><WaypointsIcon className="mr-1 size-4" />失败分析</TabsTrigger> : null}
          {hasCasesTab ? <TabsTrigger value="cases"><ListIcon className="mr-1 size-4" />案例</TabsTrigger> : null}
          {reportArtifact ? <TabsTrigger value="reports"><FileTextIcon className="mr-1 size-4" />{t('eval.reports')}</TabsTrigger> : null}
          <TabsTrigger value="log"><FileTextIcon className="mr-1 size-4" />{t('eval.runLog')}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" forceMount={false} className="mt-3 space-y-3">
          <EvaluationSummary metrics={run.headline} rows={caseRows} capabilities={capabilities} />
          {!capabilities.hasRetrievalMetrics && !capabilities.hasAnswerMetrics ? <EmptyCard title="等待结果" description="测评开始产生指标后，会在这里按能力展示结果。" /> : null}
        </TabsContent>
        {hasRetrievalTab ? <TabsContent value="retrieval" forceMount={false} className="mt-3 space-y-3"><DomainMetrics artifacts={metricArtifacts} domain="retrieval" title="检索指标" /><RetrievalBreakdown rows={caseRows} /></TabsContent> : null}
        {hasAnswerTab ? <TabsContent value="answer" forceMount={false} className="mt-3"><DomainMetrics artifacts={metricArtifacts} domain="answer" title="回答指标" /></TabsContent> : null}
        {capabilities.hasFailureAnalysis ? <TabsContent value="analysis" forceMount={false} className="mt-3"><FailureAnalysis rows={caseRows} capabilities={capabilities} /></TabsContent> : null}
        {hasCasesTab ? <TabsContent value="cases" forceMount={false} className="mt-3"><CasesView rows={caseRows} runId={run.id} capabilities={capabilities} /></TabsContent> : null}
        {reportArtifact ? <TabsContent value="reports" forceMount={false} className="mt-3"><ReportDocument artifact={reportArtifact} /></TabsContent> : null}
        <TabsContent value="log" forceMount={false} className="mt-3"><RunLog runId={run.id} events={run.events} active={active} /></TabsContent>
      </Tabs>
    </div>
  )
}

export default function EvalRunDetail({ run, onReproduce, onDeleted }: EvalRunDetailProps) {
  const { t } = useTranslation()
  const [activeJob, setActiveJob] = useState<EvalJob | null>(null)
  const capabilities = useMemo(() => getRunCapabilities(run), [run])

  useEffect(() => {
    void (async () => {
      try {
        const jobs = await listEvalJobs()
        const match = jobs.find((job) => job.kind === 'run' && ['claiming', 'running', 'cancelling'].includes(job.status) && job.output_dir === (run.run_dir ?? ''))
        setActiveJob(match ?? null)
      } catch {
        setActiveJob(null)
      }
    })()
  }, [run.run_dir])

  const cancel = async () => {
    if (!activeJob || !window.confirm(t('eval.cancelRunConfirm'))) return
    try {
      await cancelEvalJob(activeJob.id)
      setActiveJob(null)
      toast.success(t('eval.jobCanceled'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }
  const remove = async () => {
    if (!window.confirm(t('eval.deleteRunConfirm'))) return
    try {
      await deleteEvalRun(run.id)
      toast.success(t('eval.runDeleted'))
      onDeleted?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }
  const active = Boolean(activeJob) || ['running', 'queued', 'pending'].includes(run.progress?.status ?? '') || ['running', 'queued', 'pending'].includes(run.status ?? '')

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <RunHeader run={run} capabilities={capabilities} activeJob={activeJob} onCancel={cancel} onReproduce={onReproduce ? () => onReproduce(run) : undefined} onDelete={remove} />
      <div className="min-h-0 flex-1 overflow-auto"><StandardRunView run={run} active={active} capabilities={capabilities} /></div>
    </div>
  )
}
