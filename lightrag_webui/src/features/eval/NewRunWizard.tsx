import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon, SaveIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  listDatasets,
  listEvalExperiments,
  listEvalJobs,
  listEvalModels,
  listEvalTemplates,
  listComparisonTemplates,
  saveEvalTemplate,
  validateComparisonPlan,
  type ComparisonTemplate,
  type DatasetSummary,
  type EvalExperiment,
  type EvalTemplate
} from '@/api/eval'
import {
  buildCustomArmsPayload,
  diffParams,
  hasRunningJobs
} from '@/features/eval/utils'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import Input from '@/components/ui/Input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'

interface NewRunWizardProps {
  initial?: EvalTemplate | null
  onBack: () => void
  onStarted: () => void
}

const GENERIC_FIELDS: { key: string; type: 'number' | 'text' }[] = [
  { key: 'mode', type: 'text' },
  { key: 'top_k', type: 'number' },
  { key: 'chunk_top_k', type: 'number' },
  { key: 'max_cases', type: 'number' },
  { key: 'num_ctx', type: 'number' },
  { key: 'num_predict', type: 'number' },
  { key: 'temperature', type: 'number' },
  { key: 'engine', type: 'text' }
]

const FIELD_LABEL_KEYS: Record<string, string> = {
  mode: 'eval.paramMode',
  top_k: 'eval.paramTopK',
  chunk_top_k: 'eval.paramChunkTopK',
  max_cases: 'eval.paramMaxCases',
  num_ctx: 'eval.paramNumCtx',
  num_predict: 'eval.paramNumPredict',
  temperature: 'eval.paramTemperature',
  engine: 'eval.paramEngine'
}

const COMPARISON_BASE_EXPERIMENTS: Record<string, string[]> = {
  answer_model: ['frozen_prompt_llm_eval'],
  retrieval_configuration: ['end_to_end_baseline'],
  embedding: ['end_to_end_baseline'],
  full_pipeline: ['end_to_end_baseline']
}

function fieldInputType(schemaType: string): 'number' | 'text' {
  return schemaType === 'int' || schemaType === 'float' ? 'number' : 'text'
}

export default function NewRunWizard({ initial, onBack, onStarted }: NewRunWizardProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [experiments, setExperiments] = useState<EvalExperiment[]>([])
  const [templates, setTemplates] = useState<EvalTemplate[]>([])
  const [comparisonTemplates, setComparisonTemplates] = useState<ComparisonTemplate[]>([])
  const [comparisonType, setComparisonType] = useState('')
  const [dataset, setDataset] = useState<string>('')
  const [experiment, setExperiment] = useState<string>('')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [models, setModels] = useState<string[]>([])
  const [customModel, setCustomModel] = useState(false)
  const [pendingCount, setPendingCount] = useState(0)
  const [supervise, setSupervise] = useState(false)
  const [supervision, setSupervision] = useState('auto')
  const [extraText, setExtraText] = useState('')
  const [armRows, setArmRows] = useState([{ key: '', values: '' }])
  const [templateName, setTemplateName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [originalParams, setOriginalParams] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
    void listEvalExperiments().then(setExperiments).catch(() => undefined)
    void listEvalTemplates().then(setTemplates).catch(() => undefined)
    void listComparisonTemplates().then(setComparisonTemplates).catch(() => undefined)
    void listEvalModels()
      .then((data) => setModels(data.models))
      .catch(() => setModels([]))
    void listEvalJobs()
      .then((jobs) => setPendingCount(jobs.filter((job) => job.status === 'pending').length))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (initial) {
        setDataset(initial.dataset)
        setExperiment(initial.experiment)
        setParams(initial.params ?? {})
        setExtraText(initial.extraText ?? '')
        setOriginalParams({ ...(initial.params ?? {}) })
        setSupervise(initial.supervise)
      } else {
        setOriginalParams(null)
      }
    }, 0)
    return () => window.clearTimeout(timer)
  }, [initial])

  const spec = useMemo(
    () => experiments.find((item) => item.id === experiment),
    [experiments, experiment]
  )
  const webuiExperiments = useMemo(
    () => experiments.filter((item) => item.webui_launchable),
    [experiments]
  )

  const modifiedKeys = useMemo(
    () => (originalParams ? diffParams(originalParams, params) : []),
    [originalParams, params]
  )

  const armAxes = useMemo(
    () => (experiment === 'custom_arms' ? buildCustomArmsPayload(armRows) : null),
    [experiment, armRows]
  )
  const armCount = useMemo(() => {
    if (!armAxes) return 0
    return Object.values(armAxes).reduce((product, values) => product * values.length, 1)
  }, [armAxes])
  const comparisonTemplate = useMemo(
    () => comparisonTemplates.find((item) => item.type === comparisonType),
    [comparisonTemplates, comparisonType]
  )
  const eligibleBaseExperimentIds =
    COMPARISON_BASE_EXPERIMENTS[comparisonType] ?? []

  const modelValue = params.model == null ? '' : String(params.model)
  const modelInList = modelValue !== '' && models.includes(modelValue)
  const useCustomModel = customModel || (modelValue !== '' && !modelInList)

  const setParam = (key: string, value: unknown) => {
    setParams((prev) => ({ ...prev, [key]: value }))
  }

  const saveTemplate = useCallback(async () => {
    if (!templateName.trim() || !experiment || !dataset) return
    try {
      await saveEvalTemplate({
        name: templateName.trim(),
        experiment,
        dataset,
        params,
        extraText,
        supervise
      })
      toast.success(t('eval.templateSaved'))
      setTemplateName('')
      void listEvalTemplates().then(setTemplates)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }, [templateName, experiment, dataset, params, extraText, supervise, t])

  const loadTemplate = (name: string) => {
    const item = templates.find((template) => template.name === name)
    if (!item) return
    setDataset(item.dataset)
    setExperiment(item.experiment)
    setParams(item.params ?? {})
    setExtraText(item.extraText ?? '')
    setSupervise(item.supervise)
  }

  const start = useCallback(async () => {
    if (!experiment || !dataset) {
      toast.error(t('eval.wizardIncomplete'))
      return
    }
    setSubmitting(true)
    try {
      const jobs = await listEvalJobs()
      if (hasRunningJobs(jobs) && !window.confirm(t('eval.activeJobWarning'))) {
        return
      }
      const extra = extraText
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean)
      const payload: Record<string, unknown> = { ...params }
      if (experiment === 'custom_arms') {
        const baseId = String(payload.base_experiment ?? '')
        if (!baseId || !armAxes || !comparisonTemplate) {
          toast.error(t('eval.customArmsIncomplete'))
          return
        }
        const rejected = Object.keys(armAxes).filter(
          (key) => !comparisonTemplate.allowed_variables.includes(key)
        )
        if (rejected.length > 0) {
          toast.error(`“${comparisonTemplate.label}” 不允许比较：${rejected.join(', ')}`)
          return
        }
        await validateComparisonPlan({
          comparison_type: comparisonTemplate.type,
          variables: armAxes,
          inputs: Object.fromEntries(
            comparisonTemplate.required_inputs.map((key) => [key, payload[key]])
          )
        })
        payload.base_experiment = baseId
        payload.axes = JSON.stringify(armAxes)
        payload.max_arms = 8
        payload.comparison_type = comparisonTemplate.type
      }
      if (extra.length > 0) payload.extra = extra
      await createEvalJob({
        kind: 'run',
        experiment,
        dataset,
        params: payload,
        supervise,
        supervision
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [experiment, dataset, params, extraText, supervise, supervision, armAxes, comparisonTemplate, t, onStarted])

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
              <CardTitle className="text-sm">{t('eval.wizardDataset')}</CardTitle>
            </CardHeader>
            <CardContent>
              <Select value={dataset} onValueChange={setDataset}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder={t('eval.wizardPickDataset')} />
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((item) => (
                    <SelectItem key={item.dataset_id} value={item.dataset_id}>
                      {item.dataset_id} · {item.pages}p · {item.tier}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {datasets.length === 0 ? (
                <p className="text-muted-foreground mt-2 text-xs">
                  {t('eval.wizardNoDatasets')}
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.wizardExperiment')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Select value={experiment} onValueChange={setExperiment}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder={t('eval.wizardPickExperiment')} />
                </SelectTrigger>
                <SelectContent>
                  {webuiExperiments.map((item) => (
                    <SelectItem key={item.id} value={item.id} disabled={!item.env_ready}>
                      {item.label} {item.env_ready ? '' : `(${t('eval.envMissing')})`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {spec ? (
                <div className="space-y-2">
                  <p className="text-muted-foreground text-xs">{spec.description}</p>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant="outline" className="text-[10px]">
                      {t('eval.supervision')}: {spec.supervision}
                    </Badge>
                    {spec.supports_resume ? (
                      <Badge variant="outline" className="text-[10px]">
                        {t('eval.resumeCapable')}
                      </Badge>
                    ) : null}
                    {!spec.env_ready ? (
                      <Badge variant="outline" className="text-[10px] text-red-500">
                        {t('eval.envMissing')}: {spec.env_required.join(', ')}
                      </Badge>
                    ) : null}
                  </div>
                  {experiment === 'scale' ? (
                    <p className="text-muted-foreground text-xs">
                      {t('eval.scaleStageHint')}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.wizardParams')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {originalParams ? (
                <div className="space-y-1">
                  <p className="text-muted-foreground text-xs">
                    {t('eval.reproduceFrom', {
                      experiment: initial?.experiment ?? '',
                      dataset: initial?.dataset ?? ''
                    })}
                  </p>
                  {modifiedKeys.length > 0 ? (
                    <p className="text-amber-600 dark:text-amber-400 text-xs">
                      {t('eval.modifiedFields')}: {modifiedKeys.join(', ')}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {pendingCount > 0 ? (
                <p className="text-amber-600 dark:text-amber-400 text-xs">
                  {t('eval.jobsQueued', { count: pendingCount })}
                </p>
              ) : null}
              {experiment === 'custom_arms' ? (
                <div className="space-y-2 rounded-md border p-3">
                  <p className="text-sm font-medium">{t('eval.customArms')}</p>
                  <label className="flex flex-col gap-1 text-xs">
                    <span className="text-muted-foreground">比较类型</span>
                    <Select value={comparisonType} onValueChange={setComparisonType}>
                      <SelectTrigger className="h-9">
                        <SelectValue placeholder="选择比较模板" />
                      </SelectTrigger>
                      <SelectContent>
                        {comparisonTemplates.map((item) => (
                          <SelectItem key={item.type} value={item.type}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {comparisonTemplate ? (
                      <p className="text-muted-foreground text-xs">
                        允许变量：{comparisonTemplate.allowed_variables.join('、')}；索引：{comparisonTemplate.index_requirement}
                      </p>
                    ) : null}
                  </label>
                  <label className="flex flex-col gap-1 text-xs">
                    <span className="text-muted-foreground">{t('eval.baseExperiment')}</span>
                    <Select
                      value={params.base_experiment == null ? '' : String(params.base_experiment)}
                      onValueChange={(value) => setParam('base_experiment', value)}
                    >
                      <SelectTrigger className="h-9">
                        <SelectValue placeholder={t('eval.wizardPickExperiment')} />
                      </SelectTrigger>
                      <SelectContent>
                        {experiments
                          .filter((item) => eligibleBaseExperimentIds.includes(item.id))
                          .map((item) => (
                            <SelectItem key={item.id} value={item.id} disabled={!item.env_ready}>
                              {item.label} {item.env_ready ? '' : `(${t('eval.envMissing')})`}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </label>
                  {armRows.map((row, index) => (
                    <div key={index} className="flex items-center gap-2">
                      {comparisonTemplate ? (
                        <Select
                          value={row.key}
                          onValueChange={(value) =>
                            setArmRows((rows) =>
                              rows.map((r, i) => (i === index ? { ...r, key: value } : r))
                            )
                          }
                        >
                          <SelectTrigger className="h-9 w-40"><SelectValue placeholder={t('eval.armAxis')} /></SelectTrigger>
                          <SelectContent>
                            {comparisonTemplate.allowed_variables.map((key) => <SelectItem key={key} value={key}>{key}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Input className="w-40" placeholder={t('eval.armAxis')} value={row.key} onChange={(event) => setArmRows((rows) => rows.map((r, i) => i === index ? { ...r, key: event.target.value } : r))} />
                      )}
                      <Input
                        className="flex-1"
                        placeholder={t('eval.armValues')}
                        value={row.values}
                        onChange={(event) =>
                          setArmRows((rows) =>
                            rows.map((r, i) =>
                              i === index ? { ...r, values: event.target.value } : r
                            )
                          )
                        }
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setArmRows((rows) => rows.filter((_, i) => i !== index))
                        }
                      >
                        {t('eval.armRemove')}
                      </Button>
                    </div>
                  ))}
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setArmRows((rows) => [...rows, { key: '', values: '' }])}
                    >
                      {t('eval.armAdd')}
                    </Button>
                    <span className="text-muted-foreground text-xs">
                      {t('eval.armPreview', { count: armCount })}
                    </span>
                    {armCount > 8 ? (
                      <span className="text-amber-600 dark:text-amber-400 text-xs">
                        {t('eval.armLimit')}
                      </span>
                    ) : null}
                  </div>
                  {comparisonTemplate?.required_inputs.map((key) => (
                    <label key={key} className="flex flex-col gap-1 text-xs">
                      <span className="text-muted-foreground">{key}</span>
                      <Input value={params[key] == null ? '' : String(params[key])} onChange={(event) => setParam(key, event.target.value)} />
                    </label>
                  ))}
                </div>
              ) : null}
              {experiment !== 'custom_arms' ? <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">回答模型</span>
                {!useCustomModel ? (
                  <Select
                    value={params.model == null ? '' : String(params.model)}
                    onValueChange={(value) => {
                      if (value === '__custom__') {
                        setCustomModel(true)
                        setParam('model', null)
                      } else {
                        setParam('model', value)
                      }
                    }}
                  >
                    <SelectTrigger className="h-9">
                      <SelectValue placeholder={t('eval.paramModelPick')} />
                    </SelectTrigger>
                    <SelectContent>
                      {models.map((name) => (
                        <SelectItem key={name} value={name}>
                          {name}
                        </SelectItem>
                      ))}
                      <SelectItem value="__custom__">{t('eval.paramModelCustom')}</SelectItem>
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    value={params.model == null ? '' : String(params.model)}
                    onChange={(event) =>
                      setParam('model', event.target.value === '' ? null : event.target.value)
                    }
                    placeholder="qwen3:8b"
                  />
                )}
                {models.length === 0 ? (
                  <p className="text-muted-foreground text-xs">{t('eval.modelsUnavailable')}</p>
                ) : null}
              </label> : null}
              <div className="grid grid-cols-2 gap-3">
                {GENERIC_FIELDS.map((field) => (
                  <label key={field.key} className="flex flex-col gap-1 text-xs">
                    <span className="text-muted-foreground">
                      {t(FIELD_LABEL_KEYS[field.key] ?? field.key)}
                    </span>
                    <Input
                      type={field.type}
                      value={params[field.key] == null ? '' : String(params[field.key])}
                      placeholder={
                        spec?.default_baseline?.[field.key] != null
                          ? String(spec.default_baseline[field.key])
                          : ''
                      }
                      onChange={(event) => {
                        const raw = event.target.value
                        setParam(field.key, raw === '' ? null : field.type === 'number' ? Number(raw) : raw)
                      }}
                    />
                  </label>
                ))}
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={params.kg !== false}
                  onChange={(event) => setParam('kg', event.target.checked)}
                />
                {t('eval.paramKg')}
              </label>
              {spec && experiment !== 'custom_arms'
                ? Object.entries(spec.extra_schema).map(([key, schemaType]) => (
                  <label key={key} className="flex flex-col gap-1 text-xs">
                    <span className="text-muted-foreground">{key}</span>
                    <Input
                      type={fieldInputType(schemaType)}
                      value={params[key] == null ? '' : String(params[key])}
                      onChange={(event) => {
                        const raw = event.target.value
                        setParam(key, raw === '' ? null : fieldInputType(schemaType) === 'number' ? Number(raw) : raw)
                      }}
                    />
                  </label>
                ))
                : null}
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.wizardExtra')}</span>
                <Input
                  value={extraText}
                  onChange={(event) => setExtraText(event.target.value)}
                  placeholder="KEY=VALUE,KEY2=VALUE2"
                />
              </label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.wizardRunOptions')}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={supervise}
                  onChange={(event) => setSupervise(event.target.checked)}
                />
                {t('eval.supervise')}
              </label>
              {supervise ? (
                <Select value={supervision} onValueChange={setSupervision}>
                  <SelectTrigger className="h-8 w-44">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">auto</SelectItem>
                    <SelectItem value="none">none</SelectItem>
                    <SelectItem value="heartbeat">heartbeat</SelectItem>
                  </SelectContent>
                </Select>
              ) : null}
            </CardContent>
          </Card>

          <div className="flex items-center gap-2">
            <Input
              className="max-w-60"
              placeholder={t('eval.templateName')}
              value={templateName}
              onChange={(event) => setTemplateName(event.target.value)}
            />
            <Button size="sm" variant="outline" onClick={() => void saveTemplate()}>
              <SaveIcon className="mr-1 size-4" />
              {t('eval.saveTemplate')}
            </Button>
            {templates.length > 0 ? (
              <Select value="" onValueChange={loadTemplate}>
                <SelectTrigger className="h-8 w-44">
                  <SelectValue placeholder={t('eval.loadTemplate')} />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((item) => (
                    <SelectItem key={item.name} value={item.name}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
            <Button className="ml-auto" onClick={() => void start()} disabled={submitting}>
              <PlayIcon className="mr-1 size-4" />
              {t('eval.startRun')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
