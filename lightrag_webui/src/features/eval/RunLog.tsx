import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'

import { getEvalRunLog, type EvalRunEvent } from '@/api/eval'
import Button from '@/components/ui/Button'
import EmptyCard from '@/components/ui/EmptyCard'

interface RunLogProps {
  runId: string
  events?: EvalRunEvent[]
}

export default function RunLog({ runId, events = [] }: RunLogProps) {
  const { t } = useTranslation()
  const [lines, setLines] = useState<string[] | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getEvalRunLog(runId)
      setLines(result.lines)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(timer)
  }, [load])

  if (lines === null) {
    return <p className="text-muted-foreground text-sm">{t('eval.loading')}</p>
  }
  if (lines.length === 0 && events.length === 0) {
    return <EmptyCard title={t('eval.runLog')} description={t('eval.runLogEmpty')} />
  }
  return (
    <div className="space-y-2">
      {events.length > 0 ? (
        <div className="space-y-2 rounded-md border p-3">
          <p className="text-muted-foreground text-xs font-medium">运行阶段</p>
          <ol className="space-y-3">
            {events.map((event, index) => (
              <li key={`${event.timestamp}-${index}`} className="flex gap-3 text-sm">
                <span className={`mt-1.5 size-2 shrink-0 rounded-full ${event.severity === 'error' ? 'bg-destructive' : 'bg-primary'}`} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="font-medium">{event.phase || '运行'}</span>
                    <time className="text-muted-foreground text-xs">{event.timestamp}</time>
                  </div>
                  <p className="text-muted-foreground break-words text-sm">{event.message}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
        <RefreshCwIcon className={`mr-1 size-4 ${loading ? 'animate-spin' : ''}`} />
        刷新日志
      </Button>
      {lines.length > 0 ? (
        <details className="rounded-md border bg-muted/20">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium">原始运行日志</summary>
          <pre className="bg-muted/40 max-h-[480px] overflow-auto border-t p-3 text-xs leading-relaxed">{lines.join('\n')}</pre>
        </details>
      ) : null}
    </div>
  )
}
