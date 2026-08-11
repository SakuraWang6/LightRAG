import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { SparklesIcon, RefreshCwIcon } from 'lucide-react'

import { analyzeEvalRun } from '@/api/eval'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'

interface AiAnalysisProps {
  runId: string
}

export default function AiAnalysis({ runId }: AiAnalysisProps) {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const generate = useCallback(
    async (force: boolean) => {
      setLoading(true)
      setError(null)
      try {
        const result = await analyzeEvalRun(runId, force)
        setText(result.text)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    },
    [runId]
  )

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <SparklesIcon className="size-4" />
          AI 解读（可选）
          {text ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs"
              onClick={() => void generate(true)}
              disabled={loading}
            >
              <RefreshCwIcon className={`mr-1 size-3 ${loading ? 'animate-spin' : ''}`} />
              {t('eval.regenerate')}
            </Button>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <p className="text-muted-foreground text-sm">{t('eval.analyzing')}</p>
        ) : text ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
        ) : (
          <div className="flex flex-col items-start gap-2">
            <p className="text-muted-foreground text-sm">此操作会调用当前服务器配置的 LLM，对已生成的测评结果做补充解读。</p>
            <Button size="sm" onClick={() => void generate(false)}>
              <SparklesIcon className="mr-1 size-4" />
              {t('eval.generateAnalysis')}
            </Button>
          </div>
        )}
        {error ? <p className="text-destructive mt-2 text-sm">{error}</p> : null}
      </CardContent>
    </Card>
  )
}
