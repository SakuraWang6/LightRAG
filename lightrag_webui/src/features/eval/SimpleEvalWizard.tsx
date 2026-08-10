import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  listDatasets,
  listEvalJobs,
  listEvalModels,
  type DatasetSummary
} from '@/api/eval'
import { hasRunningJobs } from '@/features/eval/utils'
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

interface SimpleEvalWizardProps {
  onBack: () => void
  onStarted: () => void
}

export default function SimpleEvalWizard({ onBack, onStarted }: SimpleEvalWizardProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [models, setModels] = useState<string[]>([])
  const [dataset, setDataset] = useState('')
  const [model, setModel] = useState('')
  const [customModel, setCustomModel] = useState(false)
  const [topK, setTopK] = useState('5')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
    void listEvalModels()
      .then((data) => setModels(data.models))
      .catch(() => setModels([]))
  }, [])

  const start = useCallback(async () => {
    if (!dataset) {
      toast.error(t('eval.wizardIncomplete'))
      return
    }
    if (model === '__custom__' || !model.trim()) {
      toast.error(t('eval.paramModelPick'))
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
        experiment: 'online_baseline',
        dataset,
        params: { model, top_k: Number(topK) || 5, mode: 'mix' }
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [dataset, model, topK, t, onStarted])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">{t('eval.simpleEval')}</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto max-w-2xl space-y-4">
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
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.paramModel')}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {!customModel ? (
                <Select value={model} onValueChange={setModel}>
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
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  placeholder="qwen3:8b"
                />
              )}
              {model === '__custom__' && !customModel ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setCustomModel(true)
                    setModel('')
                  }}
                >
                  {t('eval.paramModelCustom')}
                </Button>
              ) : null}
              {models.length === 0 ? (
                <p className="text-muted-foreground text-xs">{t('eval.modelsUnavailable')}</p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">top_k</CardTitle>
            </CardHeader>
            <CardContent>
              <Input
                type="number"
                value={topK}
                onChange={(event) => setTopK(event.target.value)}
              />
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
