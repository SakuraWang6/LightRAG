import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon, SaveIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  listDatasets,
  listEvalExperiments,
  listEvalJobs,
  listEvalTemplates,
  saveEvalTemplate,
  type DatasetSummary,
  type EvalExperiment,
  type EvalTemplate
} from '@/api/eval'
import { hasRunningJobs } from '@/features/eval/utils'
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
  { key: 'model', type: 'text' },
  { key: 'mode', type: 'text' },
  { key: 'top_k', type: 'number' },
  { key: 'chunk_top_k', type: 'number' },
  { key: 'max_cases', type: 'number' },
  { key: 'num_ctx', type: 'number' },
  { key: 'num_predict', type: 'number' },
  { key: 'temperature', type: 'number' }
]

function fieldInputType(schemaType: string): 'number' | 'text' {
  return schemaType === 'int' || schemaType === 'float' ? 'number' : 'text'
}

export default function NewRunWizard({ initial, onBack, onStarted }: NewRunWizardProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [experiments, setExperiments] = useState<EvalExperiment[]>([])
  const [templates, setTemplates] = useState<EvalTemplate[]>([])
  const [dataset, setDataset] = useState<string>('')
  const [experiment, setExperiment] = useState<string>('')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [supervise, setSupervise] = useState(false)
  const [supervision, setSupervision] = useState('auto')
  const [extraText, setExtraText] = useState('')
  const [templateName, setTemplateName] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
    void listEvalExperiments().then(setExperiments).catch(() => undefined)
    void listEvalTemplates().then(setTemplates).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (initial) {
      setDataset(initial.dataset)
      setExperiment(initial.experiment)
      setParams(initial.params ?? {})
      setSupervise(initial.supervise)
    }
  }, [initial])

  const spec = useMemo(
    () => experiments.find((item) => item.id === experiment),
    [experiments, experiment]
  )

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
        supervise
      })
      toast.success(t('eval.templateSaved'))
      setTemplateName('')
      void listEvalTemplates().then(setTemplates)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }, [templateName, experiment, dataset, params, supervise, t])

  const loadTemplate = (name: string) => {
    const item = templates.find((template) => template.name === name)
    if (!item) return
    setDataset(item.dataset)
    setExperiment(item.experiment)
    setParams(item.params ?? {})
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
  }, [experiment, dataset, params, extraText, supervise, supervision, t, onStarted])

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
                  {experiments.map((item) => (
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
            <CardContent className="grid grid-cols-2 gap-3">
              {GENERIC_FIELDS.map((field) => (
                <label key={field.key} className="flex flex-col gap-1 text-xs">
                  <span className="text-muted-foreground">{field.key}</span>
                  <Input
                    type={field.type}
                    value={params[field.key] == null ? '' : String(params[field.key])}
                    onChange={(event) => {
                      const raw = event.target.value
                      setParam(field.key, raw === '' ? null : field.type === 'number' ? Number(raw) : raw)
                    }}
                  />
                </label>
              ))}
              {spec
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
              <label className="col-span-2 flex flex-col gap-1 text-xs">
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
