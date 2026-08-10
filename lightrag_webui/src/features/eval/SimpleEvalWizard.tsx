import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, FolderOpenIcon, PlayIcon, Settings2Icon, ShieldCheckIcon } from 'lucide-react'
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
  onManageDatasets: () => void
  onAdvanced: () => void
}

export default function SimpleEvalWizard({ onBack, onStarted, onManageDatasets, onAdvanced }: SimpleEvalWizardProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [dataset, setDataset] = useState('')
  const [topK, setTopK] = useState('5')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
  }, [])

  const start = useCallback(async () => {
    if (!dataset) {
      toast.error(t('eval.wizardIncomplete'))
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
        experiment: 'end_to_end_baseline',
        dataset,
        params: {
          top_k: Math.max(1, Math.floor(Number(topK) || 5))
        }
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [dataset, topK, t, onStarted])

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">新建测评</h2>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={onManageDatasets}>
            <FolderOpenIcon className="mr-1 size-4" />管理场景
          </Button>
          <Button variant="ghost" size="sm" onClick={onAdvanced}>
            <Settings2Icon className="mr-1 size-4" />更多设置
          </Button>
        </div>
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
              {datasets.length === 0 ? (
                <Button className="mt-3" size="sm" variant="outline" onClick={onManageDatasets}>
                  创建或导入场景
                </Button>
              ) : null}
            </CardContent>
          </Card>

          <Card className="border-primary/30 bg-primary/[0.02]">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <ShieldCheckIcon className="size-4" />
                本机隔离运行
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-muted-foreground text-sm">使用服务器当前已配置的模型服务，在独立 workspace 与 storage 中自动完成入库、索引、检索、回答和诊断。</p>
              <p className="text-muted-foreground text-xs">不会创建镜像，也不会把测试文档上传到当前全局知识库。</p>
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
