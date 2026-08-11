import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, FileTextIcon, ListIcon, BarChart3Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  cancelEvalJob,
  deleteEvalRun,
  listEvalJobs,
  type EvalArtifact,
  type EvalJob,
  type EvalRunDetail
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import EmptyCard from '@/components/ui/EmptyCard'
import CasesView from '@/features/eval/CasesView'
import ConditionChips from '@/features/eval/ConditionChips'
import EvaluationSummary from '@/features/eval/EvaluationSummary'
import MetricCards from '@/features/eval/MetricCards'
import ProgressBar from '@/features/eval/ProgressBar'
import ReportDocument from '@/features/eval/ReportDocument'
import RunLog from '@/features/eval/RunLog'
import { formatDate, statusBadgeClass, statusLabel } from '@/features/eval/utils'

const ANSWER_METRIC_KEYS = new Set([
  'correct_cases',
  'answer_accuracy',
  'abstention_accuracy',
  'numeric_unit_accuracy',
  'formula_accuracy',
  'table_cell_accuracy',
  'final_context_observable_rate',
  'final_context_evidence_coverage',
  'final_context_evidence_available',
  'cases'
])

const RETRIEVAL_METRIC_KEYS = new Set([
  'retrieval_cases',
  'average_recall',
  'mrr',
  'context_precision',
  'object_hit_rate',
  'full_recall_cases'
])

// These were response-reference observations in older runs and do not explain
// an answer outcome. The case sheet now presents final-context evidence instead.
const HIDDEN_DIAGNOSTIC_METRIC_KEYS = new Set([
  'evidence_available',
  'groundedness',
  'ungrounded_rate',
  'citation_presence',
  'citation_correctness'
])

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

function RunHeader({
  run,
  activeJob,
  onCancel,
  onReproduce,
  onDelete
}: {
  run: EvalRunDetail
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
        <h2 className="text-lg font-semibold">{run.label}</h2>
        {run.legacy ? (
          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300">
            {t('eval.legacyRun')}
          </Badge>
        ) : null}
        {(run.restarts ?? 0) > 0 ? (
          <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300">
            {t('eval.restarts', { count: run.restarts ?? 0 })}
            {run.last_restart_resume != null
              ? run.last_restart_resume
                ? t('eval.restartResume')
                : t('eval.restartFresh')
              : ''}
          </Badge>
        ) : null}
        {run.status ? <Badge variant="outline" className={statusBadgeClass(run.status)}>{t(statusLabel(run))}</Badge> : null}
        {(run.failed_checks ?? []).map((check) => (
          <Badge key={check} className="border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300">
            {t('eval.failed')}: {check}
          </Badge>
        ))}
        {activeJob ? (
          <Button size="sm" variant="outline" onClick={onCancel} className="ml-auto">
            {t('eval.cancelRun')}
          </Button>
        ) : null}
        {onDelete ? (
          <Button size="sm" variant="outline" className="text-destructive" onClick={onDelete}>
            {t('eval.deleteRun')}
          </Button>
        ) : null}
        {onReproduce ? (
          <Button size="sm" variant="outline" onClick={onReproduce}>
            {t('eval.reproduce')}
          </Button>
        ) : null}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">
        {t('eval.updatedAt')}: {formatDate(run.updated_at)}
        {isRunning && elapsedSeconds != null ? (
          <>
            {' · '}
            {t('eval.elapsed')}: {formatDuration(elapsedSeconds)}
          </>
        ) : null}
        {run.duration_seconds != null ? (
          <>
            {' · '}
            {t('eval.duration')}: {run.duration_seconds}s
          </>
        ) : null}
        {' · '}
        {run.artifacts.length} {t('eval.artifactLabel')}
      </p>
      {run.description ? <p className="text-muted-foreground mt-1 text-sm">{run.description}</p> : null}
      {run.failure ? (
        <div className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm">
          <span className="font-medium">{t('eval.failed')} · {run.failure.phase}: </span>
          {run.failure.summary}
          <span className="text-muted-foreground"> · {run.failure.recommendation}</span>
        </div>
      ) : null}
      <div className="mt-2">
        <ConditionChips conditions={run.conditions} limit={10} />
      </div>
      {run.variables && run.variables.length > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground text-xs">{t('eval.variables')}:</span>
          {run.variables.map((variable) => (
            <Badge key={variable.axis} variant="outline" className="font-normal">
              {variable.label ?? variable.axis}:{' '}
              {variable.arms.map((arm) => arm.label ?? String(arm.arm)).join(' / ')}
            </Badge>
          ))}
        </div>
      ) : null}
      <ProgressBar progress={run.progress} />
    </div>
  )
}

function ArtifactMetricsCard({ artifact }: { artifact: EvalArtifact }) {
  const visibleMetrics = artifact.metrics.filter(
    (metric) => !HIDDEN_DIAGNOSTIC_METRIC_KEYS.has(metric.key)
  )
  const answerMetrics = visibleMetrics.filter((metric) => ANSWER_METRIC_KEYS.has(metric.key))
  const retrievalMetrics = visibleMetrics.filter((metric) => RETRIEVAL_METRIC_KEYS.has(metric.key))
  const remainingMetrics = visibleMetrics.filter(
    (metric) => !ANSWER_METRIC_KEYS.has(metric.key) && !RETRIEVAL_METRIC_KEYS.has(metric.key)
  )
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          {artifact.title}
          <Badge variant="outline" className="text-xs">{artifact.kind}</Badge>
        </CardTitle>
        <CardDescription className="text-xs">
          {artifact.updated_at ? formatDate(artifact.updated_at) : artifact.rel_path}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {artifact.error ? (
          <p className="text-destructive text-sm">{artifact.error}</p>
        ) : artifact.kind === 'summary' && (answerMetrics.length > 0 || retrievalMetrics.length > 0) ? (
          <div className="grid gap-4 xl:grid-cols-2">
            <section>
              <h3 className="mb-2 text-sm font-semibold">回答测评指标</h3>
              <MetricCards metrics={answerMetrics} />
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">检索测评指标</h3>
              <MetricCards metrics={retrievalMetrics} />
            </section>
            {remainingMetrics.length > 0 ? (
              <section className="xl:col-span-2">
                <h3 className="mb-2 text-sm font-semibold">其他运行指标</h3>
                <MetricCards metrics={remainingMetrics} />
              </section>
            ) : null}
          </div>
        ) : (
          <MetricCards metrics={visibleMetrics} />
        )}
      </CardContent>
    </Card>
  )
}

function StandardRunView({ run, active }: { run: EvalRunDetail; active: boolean }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('metrics')
  const caseArtifact = useMemo(
    () => run.artifacts.find((artifact) => artifact.kind === 'cases'),
    [run.artifacts]
  )
  const metricArtifacts = useMemo(
    () => run.artifacts.filter((artifact) => artifact.metrics.length > 0 || artifact.error),
    [run.artifacts]
  )
  const reportArtifact = useMemo(
    () => run.artifacts.find((artifact) => artifact.report_md),
    [run.artifacts]
  )

  return (
    <div className="p-4">
      <div className="mb-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">结果摘要</CardTitle></CardHeader>
          <CardContent>
            <EvaluationSummary metrics={run.headline} rows={caseArtifact?.table.rows ?? []} />
          </CardContent>
        </Card>
      </div>

      {run.artifacts.some((a) => a.error) ? (
        <div className="bg-destructive/10 text-destructive mb-4 flex items-center gap-2 rounded-md border border-destructive/30 px-3 py-2 text-sm">
          <AlertTriangle className="size-4" />
          {t('eval.artifactErrors')}
        </div>
      ) : null}

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="metrics">
            <BarChart3Icon className="mr-1 size-4" />
            {t('eval.metrics')}
          </TabsTrigger>
          <TabsTrigger value="cases">
            <ListIcon className="mr-1 size-4" />
            逐题详情
          </TabsTrigger>
          <TabsTrigger value="reports">
            <FileTextIcon className="mr-1 size-4" />
            {t('eval.reports')}
          </TabsTrigger>
          <TabsTrigger value="log">
            <FileTextIcon className="mr-1 size-4" />
            {t('eval.runLog')}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="metrics" forceMount={false} className="mt-3 space-y-3">
          {metricArtifacts.map((artifact) => (
            <ArtifactMetricsCard key={artifact.rel_path} artifact={artifact} />
          ))}
        </TabsContent>

        <TabsContent value="cases" forceMount={false} className="mt-3">
          {!caseArtifact ? (
            <EmptyCard title={t('eval.noCases')} description={t('eval.noCasesHint')} />
          ) : (
            <CasesView rows={caseArtifact.table.rows} />
          )}
        </TabsContent>

        <TabsContent value="reports" forceMount={false} className="mt-3 space-y-3">
          {!reportArtifact ? (
            <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
          ) : (
            <ReportDocument artifact={reportArtifact} />
          )}
        </TabsContent>

        <TabsContent value="log" forceMount={false} className="mt-3">
          <RunLog runId={run.id} events={run.events} active={active} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export default function EvalRunDetail({
  run,
  onReproduce,
  onDeleted
}: EvalRunDetailProps) {
  const { t } = useTranslation()
  const [activeJob, setActiveJob] = useState<EvalJob | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const jobs = await listEvalJobs()
        const match = jobs.find(
          (job) =>
            job.kind === 'run' &&
            ['claiming', 'running', 'cancelling'].includes(job.status) &&
            job.output_dir === (run.run_dir ?? '')
        )
        setActiveJob(match ?? null)
      } catch {
        setActiveJob(null)
      }
    })()

  }, [run.run_dir])

  const cancel = async () => {
    if (!activeJob) return
    if (!window.confirm(t('eval.cancelRunConfirm'))) return
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

  const headerProps = {
    run,
    activeJob,
    onCancel: cancel,
    onReproduce: onReproduce ? () => onReproduce(run) : undefined,
    onDelete: remove
  }

  if (run.kind === 'report') {
    const report = run.artifacts.find((artifact) => artifact.report_md) ?? run.artifacts[0]
    return (
      <div className="flex h-full flex-col overflow-hidden">
        <RunHeader {...headerProps} />
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <div>
            {report ? <ReportDocument artifact={report} /> : <EmptyCard title="—" description="—" />}
          </div>
        </div>
      </div>
    )
  }
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <RunHeader {...headerProps} />
      <div className="min-h-0 flex-1 overflow-auto">
        <StandardRunView
          run={run}
          active={
            Boolean(activeJob) ||
            ['running', 'queued', 'pending'].includes(run.progress?.status ?? '') ||
            ['running', 'queued', 'pending'].includes(run.status ?? '')
          }
        />
      </div>
    </div>
  )
}
