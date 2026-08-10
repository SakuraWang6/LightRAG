import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, PlayIcon, ShieldCheckIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  listDatasets,
  listEnvironmentProfiles,
  listEvalJobs,
  type DatasetSummary,
  type EnvironmentProfile
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
  const [profiles, setProfiles] = useState<EnvironmentProfile[]>([])
  const [dataset, setDataset] = useState('')
  const [profileVersion, setProfileVersion] = useState('')
  const [topK, setTopK] = useState('5')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    void listDatasets().then((data) => setDatasets(data.datasets)).catch(() => undefined)
    void listEnvironmentProfiles().then(setProfiles).catch(() => setProfiles([]))
  }, [])

  const start = useCallback(async () => {
    if (!dataset) {
      toast.error(t('eval.wizardIncomplete'))
      return
    }
    const [profileId, rawVersion] = profileVersion.split('@')
    if (!profileId || !rawVersion) {
      toast.error('请选择已发布的评测环境')
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
          environment_profile_id: profileId,
          environment_profile_version: Number(rawVersion),
          top_k: Number(topK) || 5,
          mode: 'mix'
        }
      })
      toast.success(t('eval.jobStarted'))
      onStarted()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }, [dataset, profileVersion, topK, t, onStarted])

  const publishedProfiles = profiles.flatMap((profile) =>
    profile.versions
      .filter((version) => version.status === 'published')
      .map((version) => ({
        value: `${profile.id}@${version.version}`,
        label: `${profile.name} · v${version.version}`
      }))
  )

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

          <Card className="border-primary/30 bg-primary/[0.02]">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-sm">
                <ShieldCheckIcon className="size-4" />
                已发布的评测环境
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Select value={profileVersion} onValueChange={setProfileVersion}>
                <SelectTrigger className="h-9">
                  <SelectValue placeholder="选择已发布环境" />
                </SelectTrigger>
                <SelectContent>
                  {publishedProfiles.map((profile) => (
                    <SelectItem key={profile.value} value={profile.value}>
                      {profile.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {publishedProfiles.length === 0 ? (
                <p className="text-muted-foreground text-xs">尚无已发布环境；请先在高级评测计划中创建并发布环境档案。</p>
              ) : null}
              <p className="text-muted-foreground text-xs">将在独立 workspace 与 storage 中执行：入库、索引、检索、回答和诊断。</p>
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
