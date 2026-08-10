import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, BanIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  cancelEvalJob,
  getEvalJob,
  listEvalJobs,
  type EvalJob
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/Table'
import { formatDate } from '@/features/eval/utils'

interface JobsViewProps {
  onBack: () => void
}

function statusClass(status: string): string {
  if (status === 'running' || status === 'pending') {
    return 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
  }
  if (status === 'canceled' || status === 'stale') {
    return 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300'
  }
  if (status === 'failed') {
    return 'border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300'
  }
  return 'border-zinc-300 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300'
}

export default function JobsView({ onBack }: JobsViewProps) {
  const { t } = useTranslation()
  const [jobs, setJobs] = useState<EvalJob[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [log, setLog] = useState<string[]>([])
  const [logLoading, setLogLoading] = useState(false)

  const load = useCallback(async () => {
    try {
      setJobs(await listEvalJobs())
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  const hasActive = useMemo(
    () => jobs.some((job) => job.status === 'running' || job.status === 'pending'),
    [jobs]
  )
  const pendingCount = useMemo(
    () => jobs.filter((job) => job.status === 'pending').length,
    [jobs]
  )

  useEffect(() => {
    if (!hasActive) return
    const timer = window.setInterval(() => {
      void load()
      if (expanded) {
        void (async () => {
          const detail = await getEvalJob(expanded)
          setLog(detail.log ?? [])
        })()
      }
    }, 5000)
    return () => window.clearInterval(timer)
  }, [hasActive, load, expanded])

  const expand = async (job: EvalJob) => {
    if (expanded === job.id) {
      setExpanded(null)
      return
    }
    setExpanded(job.id)
    setLogLoading(true)
    try {
      const detail = await getEvalJob(job.id)
      setLog(detail.log ?? [])
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLogLoading(false)
    }
  }

  const cancel = async (job: EvalJob) => {
    if (!window.confirm(t('eval.cancelJobConfirm', { id: job.id }))) return
    try {
      await cancelEvalJob(job.id)
      toast.success(t('eval.jobCanceled'))
      void load()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">{t('eval.jobs')}</h2>
        {pendingCount > 0 ? (
          <span className="text-muted-foreground text-xs">
            {t('eval.jobsQueued', { count: pendingCount })}
          </span>
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="overflow-auto rounded-md border">
          <Table className="min-w-full text-left text-sm">
            <TableHeader className="sticky top-0 bg-background">
              <TableRow>
                <TableHead className="px-3 py-2">id</TableHead>
                <TableHead className="px-3 py-2">{t('eval.kind')}</TableHead>
                <TableHead className="px-3 py-2">{t('eval.jobTarget')}</TableHead>
                <TableHead className="px-3 py-2">{t('eval.status')}</TableHead>
                <TableHead className="px-3 py-2">{t('eval.queuePosition')}</TableHead>
                <TableHead className="px-3 py-2">{t('eval.startedAt')}</TableHead>
                <TableHead className="px-3 py-2" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {jobs.map((job) => (
                <Fragment key={job.id}>
                  <TableRow className="cursor-pointer" onClick={() => void expand(job)}>
                    <TableCell className="px-3 py-2 font-medium">{job.id}</TableCell>
                    <TableCell className="px-3 py-2">{job.kind}</TableCell>
                    <TableCell className="max-w-[220px] truncate px-3 py-2">
                      {job.experiment ?? job.dataset_id ?? job.dataset ?? '—'}
                    </TableCell>
                    <TableCell className="px-3 py-2">
                      <Badge variant="outline" className={`text-[10px] ${statusClass(job.status)}`}>
                        {job.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="px-3 py-2">
                      {job.status === 'pending' && job.queue_position != null
                        ? `#${job.queue_position}`
                        : '—'}
                    </TableCell>
                    <TableCell className="px-3 py-2">{formatDate(job.started_at)}</TableCell>
                    <TableCell className="px-3 py-2 text-right">
                      {job.status === 'running' || job.status === 'pending' ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={(event) => {
                            event.stopPropagation()
                            void cancel(job)
                          }}
                        >
                          <BanIcon className="size-4" />
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                  {expanded === job.id ? (
                    <TableRow className="bg-muted/40">
                      <TableCell colSpan={7} className="px-3 py-2">
                        {logLoading ? (
                          <p className="text-muted-foreground text-xs">{t('eval.loading')}</p>
                        ) : (
                          <pre className="bg-background max-h-72 overflow-auto rounded-md p-3 text-xs leading-relaxed">
                            {log.length > 0 ? log.join('\n') : t('eval.runLogEmpty')}
                          </pre>
                        )}
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              ))}
              {jobs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground h-24 text-center">
                    {t('eval.noJobs')}
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  )
}
