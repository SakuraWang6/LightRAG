import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  getEvalDataset,
  listDatasets,
  listEvalJobs,
  listEvalModels,
  type DatasetSummary
} from '@/api/eval'
import { hasRunningJobs, questionTypeLabel } from '@/features/eval/utils'
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

function integerParam(value: string, label: string, minimum: number): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < minimum) {
    throw new Error(`${label}必须是${minimum}以上的整数`)
  }
  return parsed
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
  const [datasetInfo, setDatasetInfo] = useState<{ question_count: number; question_types: string[] } | null>(null)
  const [questionTypes, setQuestionTypes] = useState<string[]>(() => {
    const value = initial?.params?.question_types
    return Array.isArray(value) ? value.map(String) : []
  })
  const [extractionMaxAsync, setExtractionMaxAsync] = useState(() => numberParam(initial?.params, 'extraction_max_async', 2))
  const [queryMaxAsync, setQueryMaxAsync] = useState(() => numberParam(initial?.params, 'query_max_async', 2))
  const [numCtx, setNumCtx] = useState(() => numberParam(initial?.params, 'num_ctx', 16384))
  const [maxTotalTokens, setMaxTotalTokens] = useState(() => numberParam(initial?.params, 'max_total_tokens', 8192))
  const [numPredict, setNumPredict] = useState(() => numberParam(initial?.params, 'num_predict', 4096))
  const [temperature, setTemperature] = useState(() => numberParam(initial?.params, 'temperature', 0))
  const [engine, setEngine] = useState(() => stringParam(initial?.params, 'engine', 'native'))
  const [kg, setKg] = useState(() => initial?.params?.kg !== false)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [engineOptions, setEngineOptions] = useState<string[]>([])
  const [optionsLoading, setOptionsLoading] = useState(true)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const effectiveMode = kg ? mode : 'naive'
  const displayDatasetName = (item: DatasetSummary) => {
    if (item.display_name.trim()) return item.display_name
    return item.dataset_id
  }

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!dataset) return
    let cancelled = false
    void getEvalDataset(dataset)
      .then((data) => {
        if (cancelled) return
        setDatasetInfo({
          question_count: typeof data.question_count === 'number' ? data.question_count : 0,
          question_types: Array.isArray(data.question_types) ? data.question_types.map(String) : []
        })
        setQuestionTypes([])
      })
      .catch(() => {
        if (!cancelled) setDatasetInfo(null)
      })
    return () => {
      cancelled = true
    }
  }, [dataset])

  useEffect(() => {
    void listEvalModels()
      .then((data) => {
        const models = data.selectable_models?.length ? data.selectable_models : data.models
        const engines = data.parser_engines ?? []
        const defaultModel = data.default_model ?? models[0]
        const defaultEngine = data.default_parser_engine ?? engines[0]
        setModelOptions(models)
        setEngineOptions(engines)
        setModel((current) => (models.includes(current) ? current : defaultModel ?? ''))
        setEngine((current) => (engines.includes(current) ? current : defaultEngine ?? ''))
        if (data.configuration_error) {
          setOptionsError(data.configuration_error)
        } else if (!Array.isArray(data.parser_engines)) {
          setOptionsError('当前 LightRAG API 尚未更新，请重启 LightRAG API 后重试。')
        } else if (models.length === 0 || engines.length === 0) {
          setOptionsError('服务器没有可用于测评的模型或解析引擎，请先完成服务配置。')
        }
      })
      .catch((error) => {
        setOptionsError(error instanceof Error ? error.message : String(error))
      })
      .finally(() => setOptionsLoading(false))
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
    if (!model || !engine || optionsLoading || optionsError) {
      toast.error(optionsError ?? '正在加载服务器运行选项，请稍后重试。')
      return
    }
    let numericParams: {
      top_k: number
      chunk_top_k: number
      max_cases: number
      num_ctx: number
      max_total_tokens: number
      num_predict: number
    }
    if (datasetInfo && datasetInfo.question_count > 0 && Number(maxCases) > datasetInfo.question_count) {
      toast.error(`题数上限不能超过数据集题数（${datasetInfo.question_count}）`)
      return
    }
    const parsedExtractionAsync = integerParam(extractionMaxAsync, '抽取并发', 1)
    const parsedQueryAsync = integerParam(queryMaxAsync, '回答并发', 1)
    try {
      numericParams = {
        top_k: integerParam(topK, t('eval.paramTopK'), 1),
        chunk_top_k: integerParam(chunkTopK, t('eval.paramChunkTopK'), 1),
        max_cases: integerParam(maxCases, t('eval.paramMaxCases'), 0),
        num_ctx: integerParam(numCtx, t('eval.paramNumCtx'), 1),
        max_total_tokens: integerParam(maxTotalTokens, t('eval.maxTotalTokens'), 1),
        num_predict: integerParam(numPredict, t('eval.paramNumPredict'), 1)
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
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
        dataset,
        params: {
          model: model.trim() || undefined,
          mode: effectiveMode,
          ...numericParams,
          temperature: parsedTemperature,
          engine: engine.trim() || 'native',
          kg,
          question_types: questionTypes.length > 0 ? questionTypes : undefined,
          extra: [
            `extraction_max_async=${parsedExtractionAsync}`,
            `query_max_async=${parsedQueryAsync}`
          ]
        }
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [chunkTopK, dataset, datasetInfo, effectiveMode, engine, extractionMaxAsync, kg, maxCases, maxTotalTokens, model, name, numCtx, numPredict, onStarted, optionsError, optionsLoading, queryMaxAsync, questionTypes, t, temperature, topK])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">{t('eval.newRun')}</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.runInfo')}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5 md:col-span-2">
                <span className="text-sm font-medium">{t('eval.runName')}</span>
                <Input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} placeholder={t('eval.runNamePlaceholder')} />
                <span className="text-muted-foreground block text-xs">{t('eval.runNameHint')}</span>
              </label>
              <label className="space-y-1.5 md:col-span-2">
                <span className="text-sm font-medium">{t('eval.wizardDataset')}</span>
                <Select value={dataset} onValueChange={(value) => {
                  setDataset(value)
                  setDatasetInfo(null)
                  setQuestionTypes([])
                }}>
                  <SelectTrigger className="h-9"><SelectValue placeholder={t('eval.wizardPickDataset')} /></SelectTrigger>
                  <SelectContent>
                    {datasets.map((item) => (
                      <SelectItem key={item.dataset_id} value={item.dataset_id}>
                        {displayDatasetName(item)} · {item.pages} {t('eval.pages')}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {datasets.length === 0 ? <span className="text-muted-foreground block text-xs">{t('eval.wizardNoDatasets')}</span> : null}
                {datasetInfo && datasetInfo.question_count > 0 ? (
                  <span className="text-muted-foreground block text-xs">
                    数据集共 {datasetInfo.question_count} 题{datasetInfo.question_types.length > 0 ? ` · 题型：${datasetInfo.question_types.map(questionTypeLabel).join('、')}` : ''}
                  </span>
                ) : null}
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">{t('eval.retrievalSettings')}</CardTitle></CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramModel')}</span><Select value={model} onValueChange={setModel} disabled={optionsLoading || modelOptions.length === 0 || optionsError !== null}><SelectTrigger><SelectValue placeholder={t('eval.paramModelPick')} /></SelectTrigger><SelectContent>{modelOptions.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent></Select><span className="text-muted-foreground block text-xs">{t('eval.modelHint')}</span></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramMode')}</span><Select value={effectiveMode} onValueChange={setMode} disabled={!kg}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="naive">Naive</SelectItem><SelectItem value="mix">Mix</SelectItem><SelectItem value="local">Local</SelectItem><SelectItem value="global">Global</SelectItem><SelectItem value="hybrid">Hybrid</SelectItem></SelectContent></Select>{!kg ? <span className="text-muted-foreground block text-xs">{t('eval.kgDisabledHint')}</span> : null}</label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramTopK')}</span><Input type="number" min="1" value={topK} onChange={(event) => setTopK(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramChunkTopK')}</span><Input type="number" min="1" value={chunkTopK} onChange={(event) => setChunkTopK(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramMaxCases')}</span><Input type="number" min="0" value={maxCases} onChange={(event) => setMaxCases(event.target.value)} /><span className="text-muted-foreground block text-xs">{t('eval.maxCasesHint')}{datasetInfo && datasetInfo.question_count > 0 ? `（上限 ${datasetInfo.question_count} 题）` : ''}</span></label>
              <label className="space-y-1.5">
                <span className="text-sm font-medium">抽取并发</span>
                <Select value={extractionMaxAsync} onValueChange={setExtractionMaxAsync}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {[1, 2, 3, 4].map((value) => <SelectItem key={value} value={String(value)}>{value}</SelectItem>)}
                  </SelectContent>
                </Select>
                <span className="text-muted-foreground block text-xs">KG 抽取并行数，本地内存小请用 1</span>
              </label>
              <label className="space-y-1.5">
                <span className="text-sm font-medium">回答并发</span>
                <Select value={queryMaxAsync} onValueChange={setQueryMaxAsync}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {[1, 2, 3, 4].map((value) => <SelectItem key={value} value={String(value)}>{value}</SelectItem>)}
                  </SelectContent>
                </Select>
                <span className="text-muted-foreground block text-xs">逐题回答并行数，内存小请用 1</span>
              </label>
              <label className="flex items-center gap-2 self-center rounded-md border px-3 py-2.5"><Checkbox checked={kg} onCheckedChange={(checked) => setKg(checked === true)} /><span className="text-sm font-medium">{t('eval.paramKg')}</span></label>
            </CardContent>
          </Card>

          {datasetInfo && datasetInfo.question_types.length > 0 ? (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">题目类型</CardTitle></CardHeader>
              <CardContent>
                <p className="text-muted-foreground mb-2 text-xs">选择要运行的题型；留空表示全部</p>
                <div className="flex flex-wrap gap-3">
                  {datasetInfo.question_types.map((type) => (
                    <label key={type} className="flex items-center gap-1.5 text-sm">
                      <Checkbox
                        checked={questionTypes.includes(type)}
                        onCheckedChange={(checked) => {
                          setQuestionTypes((current) =>
                            checked === true
                              ? Array.from(new Set([...current, type]))
                              : current.filter((item) => item !== type)
                          )
                        }}
                      />
                      <span>{questionTypeLabel(type)}</span>
                    </label>
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">{t('eval.wizardParams')}</CardTitle></CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramNumCtx')}</span><Input type="number" min="1" value={numCtx} onChange={(event) => setNumCtx(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.maxTotalTokens')}</span><Input type="number" min="1" value={maxTotalTokens} onChange={(event) => setMaxTotalTokens(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramNumPredict')}</span><Input type="number" min="1" value={numPredict} onChange={(event) => setNumPredict(event.target.value)} /><span className="text-muted-foreground block text-xs">{t('eval.numPredictHint')}</span></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramTemperature')}</span><Input type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} /></label>
              <label className="space-y-1.5"><span className="text-sm font-medium">{t('eval.paramEngine')}</span><Select value={engine} onValueChange={setEngine} disabled={optionsLoading || engineOptions.length === 0}><SelectTrigger><SelectValue placeholder={t('eval.enginePick')} /></SelectTrigger><SelectContent>{engineOptions.map((option) => <SelectItem key={option} value={option}>{option}</SelectItem>)}</SelectContent></Select><span className="text-muted-foreground block text-xs">{t('eval.engineHint')}</span></label>
            </CardContent>
          </Card>

          {optionsError ? <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{optionsError}</p> : null}
          <Button onClick={() => void start()} disabled={submitting || optionsLoading || Boolean(optionsError)}>
            <PlayIcon className="mr-1 size-4" />
            {t('eval.startRun')}
          </Button>
        </div>
      </div>
    </div>
  )
}
