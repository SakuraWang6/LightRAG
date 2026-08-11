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
  type EvalRunDetail,
  type MetricItem
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import EmptyCard from '@/components/ui/EmptyCard'
import AiAnalysis from '@/features/eval/AiAnalysis'
import CasesView from '@/features/eval/CasesView'
import ConditionChips from '@/features/eval/ConditionChips'
import MetricCards from '@/features/eval/MetricCards'
import ProgressBar from '@/features/eval/ProgressBar'
import ReportDocument from '@/features/eval/ReportDocument'
import RunLog from '@/features/eval/RunLog'
import { formatDate, statusBadgeClass, statusLabel } from '@/features/eval/utils'

const HEADLINE_ORDER = [
  'passed',
  'answer_accuracy',
  'groundedness',
  'ungrounded_rate',
  'average_recall',
  'mrr',
  'evidence_available',
  'retrieval_recall'
]

function headlineMetrics(run: EvalRunDetail, questionScore?: MetricItem): MetricItem[] {
  const entries = Object.values(run.headline ?? {})
  if (entries.length === 0) return []
  const ordered = HEADLINE_ORDER
    .map((key) => entries.find((m) => m.key === key))
    .filter((m): m is MetricItem => Boolean(m))
  const rest = entries.filter((m) => !HEADLINE_ORDER.includes(m.key))
  const metrics = [...ordered, ...rest]
  const withoutSeparateCounts = questionScore
    ? metrics.filter((metric) => !['correct_cases', 'cases'].includes(metric.key))
    : metrics
  return questionScore ? [questionScore, ...withoutSeparateCounts].slice(0, 6) : withoutSeparateCounts.slice(0, 6)
}

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
  const etaSeconds = useMemo(() => {
    const done = run.progress?.done ?? 0
    const total = run.progress?.total ?? 0
    if (!run.started_at || elapsedSeconds == null || done <= 0 || total <= 1) return null
    const rate = done / elapsedSeconds
    if (rate <= 0) return null
    return (total - done) / rate
  }, [run.started_at, elapsedSeconds, run.progress?.done, run.progress?.total])
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
        {isRunning && etaSeconds != null ? (
          <>
            {' · '}
            {t('eval.eta')}: {formatDuration(etaSeconds)}
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
        ) : (
          <MetricCards metrics={artifact.metrics} />
        )}
      </CardContent>
    </Card>
  )
}

function StandardRunView({ run }: { run: EvalRunDetail }) {
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
  const reportArtifacts = useMemo(
    () => run.artifacts.filter((artifact) => artifact.report_md),
    [run.artifacts]
  )
  const [selectedReportPath, setSelectedReportPath] = useState<string | null>(null)
  const selectedReport = reportArtifacts.find((a) => a.rel_path === selectedReportPath) ?? reportArtifacts[0]
  const questionScore = useMemo<MetricItem | undefined>(() => {
    const rows = caseArtifact?.table.rows ?? []
    const scored = rows.filter((row) => typeof row.exact_match === 'boolean')
    if (scored.length === 0) return undefined
    const correct = scored.filter((row) => row.exact_match === true).length
    return {
      key: 'question_score',
      label: '正确题数 / 总题数',
      value: `${correct} / ${scored.length}`,
      type: 'text'
    }
  }, [caseArtifact])

  return (
    <div className="p-4">
      <div className="mb-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">结果摘要</CardTitle></CardHeader>
          <CardContent><MetricCards metrics={headlineMetrics(run, questionScore)} /></CardContent>
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
          <AiAnalysis runId={run.id} />
          {reportArtifacts.length === 0 ? (
            <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
          ) : (
            <div className="space-y-3">
              <div className="w-72">
                <Select value={selectedReport?.rel_path} onValueChange={setSelectedReportPath}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('eval.selectReport')} />
                  </SelectTrigger>
                  <SelectContent>
                    {reportArtifacts.map((artifact) => (
                      <SelectItem key={artifact.rel_path} value={artifact.rel_path}>
                        {artifact.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {selectedReport ? <ReportDocument artifact={selectedReport} /> : null}
            </div>
          )}
        </TabsContent>

        <TabsContent value="log" forceMount={false} className="mt-3">
          <RunLog runId={run.id} events={run.events} />
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
      const result = await cancelEvalJob(activeJob.id)
      setActiveJob(null)
      toast.success(
        result.status === 'cancelled'
          ? activeJob.supervise
            ? t('eval.canceledNoRestart')
            : t('eval.jobCanceled')
          : t('eval.jobCanceled')
      )
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
          <AiAnalysis runId={run.id} />
          <div className="mt-3">
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
        <StandardRunView run={run} />
      </div>
    </div>
  )
}
