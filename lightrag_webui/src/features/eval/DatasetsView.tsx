import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, ChevronsUpDownIcon, TrashIcon, UploadIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  cancelEvalJob,
  createEvalJob,
  deleteDataset,
  importEvalDataset,
  listDatasets,
  listEvalJobs,
  type DatasetSummary,
  type EvalJob
} from '@/api/eval'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import Checkbox from '@/components/ui/Checkbox'
import Input from '@/components/ui/Input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from '@/components/ui/Table'

interface DatasetsViewProps {
  onBack: () => void
}

type ContentElement = 'tables' | 'figures' | 'equations'

const SCALE_LABELS: Record<string, string> = {
  smoke: 'eval.scaleSmoke',
  medium: 'eval.scaleMedium',
  large: 'eval.scaleLarge',
  stress: 'eval.scaleStress'
}
const COMPLEXITY_LABELS: Record<string, string> = {
  basic: 'eval.complexityBasic',
  rich: 'eval.complexityRich'
}
const CONTENT_LABELS: Record<string, string> = {
  text: 'eval.contentText',
  tables: 'eval.contentTables',
  figures: 'eval.contentFigures',
  equations: 'eval.contentEquations'
}

export default function DatasetsView({ onBack }: DatasetsViewProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [jobs, setJobs] = useState<EvalJob[]>([])
  const [datasetName, setDatasetName] = useState('')
  const [tier, setTier] = useState('smoke')
  const [profile, setProfile] = useState('rich')
  const [language, setLanguage] = useState<'en' | 'zh'>('en')
  const [pages, setPages] = useState<string>('')
  const [outputFormat, setOutputFormat] = useState<'docx' | 'docx,pdf'>('docx')
  const [contentElements, setContentElements] = useState<ContentElement[]>([
    'tables',
    'figures',
    'equations'
  ])
  const [force, setForce] = useState(false)
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const importInputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await listDatasets()
      setDatasets(data.datasets)
      setJobs(await listEvalJobs())
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  const activeDatasetJobs = useMemo(
    () => jobs.filter((job) => job.kind === 'dataset' && job.status === 'running'),
    [jobs]
  )

  const create = useCallback(async () => {
    if (!datasetName.trim()) return
    setCreating(true)
    try {
      await createEvalJob({
        kind: 'dataset',
        dataset_create: {
          display_name: datasetName.trim(),
          tier,
          profile,
          language,
          pages: pages === '' ? null : Number(pages),
          formats: outputFormat.split(',') as ('docx' | 'pdf')[],
          modalities: ['text', ...contentElements],
          force
        }
      })
      toast.success(t('eval.datasetJobStarted'))
      setDatasetName('')
      void refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setCreating(false)
    }
  }, [
    datasetName,
    tier,
    profile,
    language,
    pages,
    outputFormat,
    contentElements,
    force,
    refresh,
    t
  ])

  const toggleContentElement = useCallback((element: ContentElement, checked: boolean) => {
    setContentElements((current) => {
      if (checked) return current.includes(element) ? current : [...current, element]
      return current.filter((item) => item !== element)
    })
  }, [])

  const selectedContentLabel = [
    t('eval.contentText'),
    ...contentElements.map((element) => t(CONTENT_LABELS[element]))
  ].join(', ')

  const displayDatasetName = (item: DatasetSummary) => {
    if (item.display_name.trim()) return item.display_name
    return t('eval.legacyDatasetName', {
      complexity: t(COMPLEXITY_LABELS[item.profile] ?? item.profile),
      scale: t(SCALE_LABELS[item.tier] ?? item.tier),
      pages: item.pages
    })
  }

  const remove = useCallback(
    async (datasetIdToRemove: string) => {
      if (!window.confirm(t('eval.deleteDatasetConfirm', { name: datasetIdToRemove }))) return
      try {
        await deleteDataset(datasetIdToRemove)
        toast.success(t('eval.datasetDeleted'))
        void refresh()
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      }
    },
    [refresh, t]
  )

  const cancelDatasetJob = useCallback(
    async (job: EvalJob) => {
      if (!window.confirm(t('eval.cancelJobConfirm', { id: job.id }))) return
      try {
        await cancelEvalJob(job.id)
        toast.success(t('eval.jobCanceled'))
        void refresh()
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      }
    },
    [refresh, t]
  )

  const importDataset = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      event.target.value = ''
      if (!file) return
      if (!file.name.toLowerCase().endsWith('.zip')) {
        toast.error('请选择包含 manifest.json、oracle.json 和文档的 .zip 数据集包')
        return
      }
      setImporting(true)
      try {
        const dataset = await importEvalDataset(file)
        toast.success(`已导入数据集：${dataset.display_name || dataset.title}`)
        void refresh()
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error))
      } finally {
        setImporting(false)
      }
    },
    [refresh]
  )

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <Button variant="ghost" size="icon" onClick={onBack} tooltip={t('eval.backToDetail')}>
          <ArrowLeftIcon className="size-4" />
        </Button>
        <h2 className="text-lg font-semibold">{t('eval.datasets')}</h2>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div className="mx-auto max-w-5xl space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-sm">新建或导入数据集</CardTitle>
                </div>
                <input
                  ref={importInputRef}
                  type="file"
                  accept=".zip,application/zip"
                  className="hidden"
                  onChange={(event) => void importDataset(event)}
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={importing}
                  onClick={() => importInputRef.current?.click()}
                >
                  <UploadIcon className="mr-1 size-4" />
                  {importing ? '导入中…' : '导入数据集包'}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.datasetName')}</span>
                <Input
                  value={datasetName}
                  onChange={(event) => setDatasetName(event.target.value)}
                  placeholder={t('eval.datasetNameHint')}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.documentScale')}</span>
                <Select value={tier} onValueChange={setTier}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="smoke">{t('eval.scaleSmoke')}</SelectItem>
                    <SelectItem value="medium">{t('eval.scaleMedium')}</SelectItem>
                    <SelectItem value="large">{t('eval.scaleLarge')}</SelectItem>
                    <SelectItem value="stress">{t('eval.scaleStress')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.documentComplexity')}</span>
                <Select value={profile} onValueChange={setProfile}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="basic">{t('eval.complexityBasic')}</SelectItem>
                    <SelectItem value="rich">{t('eval.complexityRich')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.datasetLanguage')}</span>
                <Select
                  value={language}
                  onValueChange={(value) => setLanguage(value as 'en' | 'zh')}
                >
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="en">{t('eval.languageEnglish')}</SelectItem>
                    <SelectItem value="zh">{t('eval.languageChinese')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.customPages')}</span>
                <Input
                  type="number"
                  value={pages}
                  onChange={(event) => setPages(event.target.value)}
                  placeholder={t('eval.customPagesHint')}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.outputFormat')}</span>
                <Select
                  value={outputFormat}
                  onValueChange={(value) => setOutputFormat(value as 'docx' | 'docx,pdf')}
                >
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="docx">{t('eval.formatDocx')}</SelectItem>
                    <SelectItem value="docx,pdf">{t('eval.formatDocxPdf')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <div className="col-span-2 flex flex-col gap-1 text-xs lg:col-span-2">
                <span className="text-muted-foreground">{t('eval.contentElements')}</span>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button variant="outline" className="h-9 w-full justify-between px-3 font-normal">
                      <span className="truncate">{selectedContentLabel}</span>
                      <ChevronsUpDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-2">
                    <div className="space-y-1" role="group" aria-label={t('eval.contentElements')}>
                      <label className="bg-muted/30 text-muted-foreground flex items-center gap-2 rounded-sm px-2 py-2 text-sm">
                        <Checkbox checked disabled />
                        <span>{t('eval.contentText')}</span>
                      </label>
                      {([
                        ['tables', 'contentTables'],
                        ['figures', 'contentFigures'],
                        ['equations', 'contentEquations']
                      ] as const).map(([element, labelKey]) => {
                        const selected = contentElements.includes(element)
                        return (
                          <label
                            key={element}
                            className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded-sm px-2 py-2 text-sm"
                          >
                            <Checkbox
                              checked={selected}
                              onCheckedChange={(checked) =>
                                toggleContentElement(element, checked === true)
                              }
                            />
                            <span>{t(`eval.${labelKey}`)}</span>
                          </label>
                        )
                      })}
                    </div>
                  </PopoverContent>
                </Popover>
              </div>
              <div className="col-span-2 flex items-center gap-4 lg:col-span-4">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={force}
                    onChange={(event) => setForce(event.target.checked)}
                  />
                  {t('eval.force')}
                </label>
                <Button size="sm" onClick={() => void create()} disabled={creating}>
                  {t('eval.createDataset')}
                </Button>
              </div>
            </CardContent>
          </Card>

          {activeDatasetJobs.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted-foreground">{t('eval.datasetJobsActive')}:</span>
              {activeDatasetJobs.map((job) => (
                <span key={job.id} className="flex items-center gap-1">
                  <Badge variant="outline" className="text-[10px]">
                    {job.display_name || job.dataset_id}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-1.5 text-[10px]"
                    onClick={() => void cancelDatasetJob(job)}
                  >
                    {t('eval.cancelRun')}
                  </Button>
                </span>
              ))}
            </div>
          ) : null}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.datasets')}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-auto rounded-md border">
                <Table className="min-w-full text-left text-sm">
                  <TableHeader className="bg-background sticky top-0">
                    <TableRow>
                      <TableHead className="px-3 py-2">{t('eval.datasetName')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.documentScale')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.documentComplexity')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.datasetLanguage')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.pages')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.contentElements')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.outputFormat')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.updatedAt')}</TableHead>
                      <TableHead className="px-3 py-2" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {datasets.map((item) => (
                      <TableRow key={item.dataset_id}>
                        <TableCell className="px-3 py-2 font-medium">
                          {displayDatasetName(item)}
                        </TableCell>
                        <TableCell className="px-3 py-2">
                          {t(SCALE_LABELS[item.tier] ?? item.tier)}
                        </TableCell>
                        <TableCell className="px-3 py-2">
                          {t(COMPLEXITY_LABELS[item.profile] ?? item.profile)}
                        </TableCell>
                        <TableCell className="px-3 py-2">
                          <Badge variant="outline" className="text-[10px]">
                            {item.language === 'zh'
                              ? t('eval.languageChinese')
                              : t('eval.languageEnglish')}
                          </Badge>
                        </TableCell>
                        <TableCell className="px-3 py-2">{item.pages}</TableCell>
                        <TableCell className="px-3 py-2">
                          <div className="flex flex-wrap gap-1">
                            {item.modalities.map((modality) => (
                              <Badge key={modality} variant="outline" className="text-[10px]">
                                {t(CONTENT_LABELS[modality] ?? modality)}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="px-3 py-2">
                          {item.formats
                            .map((format) => (format === 'docx' ? t('eval.formatDocx') : 'PDF'))
                            .join(' + ')}
                        </TableCell>
                        <TableCell className="px-3 py-2">{item.created_at.slice(0, 19)}</TableCell>
                        <TableCell className="px-3 py-2 text-right">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => void remove(item.dataset_id)}
                          >
                            <TrashIcon className="size-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                    {datasets.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={9} className="text-muted-foreground h-24 text-center">
                          {t('eval.noDatasets')}
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
