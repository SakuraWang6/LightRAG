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
  if (lines.length === 0) {
    return <EmptyCard title={t('eval.runLog')} description={t('eval.runLogEmpty')} />
  }
  return (
    <div className="space-y-2">
      {events.length > 0 ? (
        <div className="overflow-auto rounded-md border">
          <table className="min-w-full text-left text-xs">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-3 py-2 font-medium">Time</th>
                <th className="px-3 py-2 font-medium">Phase</th>
                <th className="px-3 py-2 font-medium">Level</th>
                <th className="px-3 py-2 font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event, index) => (
                <tr key={`${event.timestamp}-${index}`} className="border-t">
                  <td className="text-muted-foreground whitespace-nowrap px-3 py-1.5">{event.timestamp}</td>
                  <td className="px-3 py-1.5">{event.phase}</td>
                  <td className="px-3 py-1.5">{event.severity}</td>
                  <td className="px-3 py-1.5">{event.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
        <RefreshCwIcon className={`mr-1 size-4 ${loading ? 'animate-spin' : ''}`} />
        {t('eval.refresh')}
      </Button>
      <pre className="bg-muted/40 max-h-[480px] overflow-auto rounded-md p-3 text-xs leading-relaxed">
        {lines.join('\n')}
      </pre>
    </div>
  )
}
