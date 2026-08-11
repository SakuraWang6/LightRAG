import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { getEvalRunLog, type EvalRunEvent } from '@/api/eval'
import EmptyCard from '@/components/ui/EmptyCard'

interface RunLogProps {
  runId: string
  events?: EvalRunEvent[]
  active?: boolean
}

const LIVE_LOG_POLL_INTERVAL_MS = 1500
const FOLLOW_TAIL_THRESHOLD_PX = 24

function sameLines(previous: string[] | null, next: string[]): boolean {
  return previous?.length === next.length && previous.every((line, index) => line === next[index])
}

export default function RunLog({ runId, events = [], active = false }: RunLogProps) {
  const { t } = useTranslation()
  const [lines, setLines] = useState<string[] | null>(null)
  const [followTail, setFollowTail] = useState(true)
  const logViewportRef = useRef<HTMLPreElement | null>(null)

  const load = useCallback(async (silent = false) => {
    try {
      const result = await getEvalRunLog(runId)
      setLines((previous) => (sameLines(previous, result.lines) ? previous : result.lines))
    } catch (error) {
      if (!silent) {
        toast.error(error instanceof Error ? error.message : String(error))
      }
    }
  }, [runId])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLines(null)
      setFollowTail(true)
      void load()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [load])

  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(true), LIVE_LOG_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [active, load])

  useEffect(() => {
    const viewport = logViewportRef.current
    if (followTail && viewport) {
      viewport.scrollTop = viewport.scrollHeight
    }
  }, [followTail, lines])

  const handleLogScroll = () => {
    const viewport = logViewportRef.current
    if (!viewport) return
    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
    setFollowTail(distanceFromBottom <= FOLLOW_TAIL_THRESHOLD_PX)
  }

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
      {lines.length > 0 ? (
        <section className="overflow-hidden rounded-md border bg-muted/20">
          <p className="border-b px-3 py-2 text-sm font-medium">运行日志</p>
          <pre
            ref={logViewportRef}
            onScroll={handleLogScroll}
            className="bg-muted/40 max-h-[480px] overflow-auto p-3 text-xs leading-relaxed"
          >
            {lines.join('\n')}
          </pre>
        </section>
      ) : null}
    </div>
  )
}
