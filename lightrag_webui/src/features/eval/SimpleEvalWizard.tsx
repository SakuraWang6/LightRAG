import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  listDatasets,
  listEvalJobs,
  type DatasetSummary
} from '@/api/eval'
import { hasRunningJobs } from '@/features/eval/utils'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'

type SimpleEvalParams = Record<string, unknown>

interface SimpleEvalWizardProps {
  initial?: {
    name?: string
    dataset: string
    params?: SimpleEvalParams
  } | null
  onBack: () => void
  onStarted: () => void
}

function positiveInteger(value: string, fallback: number): number {
  const parsed = Math.floor(Number(value))
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function nonNegativeInteger(value: string): number {
  const parsed = Math.floor(Number(value))
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

function stringParam(params: SimpleEvalParams | undefined, key: string, fallback: string): string {
  const value = params?.[key]
  return typeof value === 'string' && value.trim() ? value : fallback
}

function numberParam(params: SimpleEvalParams | undefined, key: string, fallback: number): string {
  const value = params?.[key]
  return typeof value === 'number' || typeof value === 'string' ? String(value) : String(fallback)
}

export default function SimpleEvalWizard({ initial, onBack, onStarted }: SimpleEvalWizardProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [name, setName] = useState(() => initial?.name ?? '')
  const [dataset, setDataset] = useState(() => initial?.dataset ?? '')
  const [model, setModel] = useState(() => stringParam(initial?.params, 'model', 'qwen3:8b'))
  const [mode, setMode] = useState(() => stringParam(initial?.params, 'mode', 'mix'))
  const [topK, setTopK] = useState(() => numberParam(initial?.params, 'top_k', 5))
  const [chunkTopK, setChunkTopK] = useState(() => numberParam(initial?.params, 'chunk_top_k', 5))
  const [maxCases, setMaxCases] = useState(() => numberParam(initial?.params, 'max_cases', 0))
  const [numCtx, setNumCtx] = useState(() => numberParam(initial?.params, 'num_ctx', 16384))
  const [maxTotalTokens, setMaxTotalTokens] = useState(() => numberParam(initial?.params, 'max_total_tokens', 8192))
  const [numPredict, setNumPredict] = useState(() => numberParam(initial?.params, 'num_predict', 128))
  const [temperature, setTemperature] = useState(() => numberParam(initial?.params, 'temperature', 0))
  const [engine, setEngine] = useState(() => stringParam(initial?.params, 'engine', 'native'))
  const [kg, setKg] = useState(() => initial?.params?.kg !== false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
  }, [])

  const start = useCallback(async () => {
    if (!name.trim()) {
      toast.error('请填写测评名称')
      return
    }
    if (!dataset) {
      toast.error(t('eval.wizardIncomplete'))
      return
    }
    const parsedTemperature = Number(temperature)
    if (!Number.isFinite(parsedTemperature) || parsedTemperature < 0 || parsedTemperature > 2) {
      toast.error('温度需介于 0 和 2 之间')
      return
    }
    setSubmitting(true)
    try {
      const jobs = await listEvalJobs()
      if (hasRunningJobs(jobs) && !window.confirm(t('eval.activeJobWarning'))) {
        return
      }
      await createEvalJob({
        kind: 'run',
        name: name.trim(),
        experiment: 'end_to_end_baseline',
        dataset,
        params: {
          model: model.trim() || undefined,
          mode,
          top_k: positiveInteger(topK, 5),
          chunk_top_k: positiveInteger(chunkTopK, 5),
          max_cases: nonNegativeInteger(maxCases),
          num_ctx: positiveInteger(numCtx, 16384),
          max_total_tokens: positiveInteger(maxTotalTokens, 8192),
          num_predict: positiveInteger(numPredict, 128),
          temperature: parsedTemperature,
          engine: engine.trim() || 'native',
          kg
        }
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [chunkTopK, dataset, engine, kg, maxCases, maxTotalTokens, mode, model, name, numCtx, numPredict, onStarted, t, temperature, topK])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">新建测评</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">测评信息</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5 md:col-span-2">
                <span className="text-sm font-medium">测评名称</span>
                <Input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} placeholder="例如：合同文档基线测评" />
                <span className="text-muted-foreground block text-xs">名称仅用于列表与报告展示；内部运行 ID 会单独生成。</span>
              </label>
              <label className="space-y-1.5 md:col-span-2">
                <span className="text-sm font-medium">{t('eval.wizardDataset')}</span>
                <Select value={dataset} onValueChange={setDataset}>
                  <SelectTrigger className="h-9"><SelectValue placeholder={t('eval.wizardPickDataset')} /></SelectTrigger>
                  <SelectContent>
                    {datasets.map((item) => (
                      <SelectItem key={item.dataset_id} value={item.dataset_id}>
                        {item.dataset_id} · {item.pages}p · {item.tier}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {datasets.length === 0 ? <span className="text-muted-foreground block text-xs">请先在测评首页的数据集页面创建或导入数据集。</span> : null}
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">检索与评分范围</CardTitle></CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5"><span className="text-sm font-medium">模型</span><Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="服务器已配置的模型名称" /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">检索模式</span><Select value={mode} onValueChange={setMode}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="mix">Mix</SelectItem><SelectItem value="local">Local</SelectItem><SelectItem value="global">Global</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectContent></Select></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">检索条数（Top-K）</span><Input type="number" min="1" value={topK} onChange={(event) => setTopK(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">Chunk Top-K</span><Input type="number" min="1" value={chunkTopK} onChange={(event) => setChunkTopK(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">最多运行题数</span><Input type="number" min="0" value={maxCases} onChange={(event) => setMaxCases(event.target.value)} /><span className="text-muted-foreground block text-xs">填 0 表示运行数据集中的全部题目。</span></label>
              <label className="flex items-center gap-2 self-center rounded-md border px-3 py-2.5"><Checkbox checked={kg} onCheckedChange={(checked) => setKg(checked === true)} /><span className="text-sm font-medium">启用知识图谱抽取</span></label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">运行参数</CardTitle></CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5"><span className="text-sm font-medium">上下文窗口</span><Input type="number" min="1" value={numCtx} onChange={(event) => setNumCtx(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">最大上下文 Token</span><Input type="number" min="1" value={maxTotalTokens} onChange={(event) => setMaxTotalTokens(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">最大输出 Token</span><Input type="number" min="1" value={numPredict} onChange={(event) => setNumPredict(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">温度</span><Input type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">解析引擎</span><Input value={engine} onChange={(event) => setEngine(event.target.value)} placeholder="native" /></label>
            </CardContent>
          </Card>

          <Button onClick={() => void start()} disabled={submitting}>
            <PlayIcon className="mr-1 size-4" />
            {t('eval.startRun')}
          </Button>
        </div>
      </div>
    </div>
  )
}
