import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftIcon, TrashIcon } from 'lucide-react'
import { toast } from 'sonner'

import {
  createEvalJob,
  deleteDataset,
  listDatasets,
  listEvalJobs,
  type DatasetSummary,
  type EvalJob
} from '@/api/eval'
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

export default function DatasetsView({ onBack }: DatasetsViewProps) {
  const { t } = useTranslation()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [jobs, setJobs] = useState<EvalJob[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [tier, setTier] = useState('smoke')
  const [profile, setProfile] = useState('rich')
  const [pages, setPages] = useState<string>('')
  const [formats, setFormats] = useState('docx')
  const [modalities, setModalities] = useState('text,tables,figures,equations')
  const [force, setForce] = useState(false)
  const [creating, setCreating] = useState(false)

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
    void refresh()
  }, [refresh])

  const activeDatasetJobs = useMemo(
    () => jobs.filter((job) => job.kind === 'dataset' && job.status === 'running'),
    [jobs]
  )

  const create = useCallback(async () => {
    if (!datasetId.trim()) return
    setCreating(true)
    try {
      await createEvalJob({
        kind: 'dataset',
        dataset_create: {
          dataset_id: datasetId.trim(),
          tier,
          profile,
          pages: pages === '' ? null : Number(pages),
          formats: formats.split(',').map((item) => item.trim()).filter(Boolean),
          modalities: modalities.split(',').map((item) => item.trim()).filter(Boolean),
          force
        }
      })
      toast.success(t('eval.datasetJobStarted'))
      setDatasetId('')
      void refresh()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setCreating(false)
    }
  }, [datasetId, tier, profile, pages, formats, modalities, force, refresh, t])

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
              <CardTitle className="text-sm">{t('eval.createDataset')}</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-3 gap-3">
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">dataset_id</span>
                <Input value={datasetId} onChange={(event) => setDatasetId(event.target.value)} />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.tier')}</span>
                <Select value={tier} onValueChange={setTier}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {['smoke', 'medium', 'large', 'stress'].map((item) => (
                      <SelectItem key={item} value={item}>{item}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.profile')}</span>
                <Select value={profile} onValueChange={setProfile}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="basic">basic</SelectItem>
                    <SelectItem value="rich">rich</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.pages')}</span>
                <Input
                  type="number"
                  value={pages}
                  onChange={(event) => setPages(event.target.value)}
                  placeholder={t('eval.pagesHint')}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.formats')}</span>
                <Input value={formats} onChange={(event) => setFormats(event.target.value)} />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-muted-foreground">{t('eval.modalities')}</span>
                <Input value={modalities} onChange={(event) => setModalities(event.target.value)} />
              </label>
              <div className="col-span-2 flex items-center gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
                  {t('eval.force')}
                </label>
                <Button size="sm" onClick={() => void create()} disabled={creating}>
                  {t('eval.createDataset')}
                </Button>
              </div>
            </CardContent>
          </Card>

          {activeDatasetJobs.length > 0 ? (
            <p className="text-muted-foreground text-xs">
              {t('eval.datasetJobsActive')}:{' '}
              {activeDatasetJobs.map((job) => job.dataset_id).join(', ')}
            </p>
          ) : null}

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">{t('eval.datasets')}</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-auto rounded-md border">
                <Table className="min-w-full text-left text-sm">
                  <TableHeader className="sticky top-0 bg-background">
                    <TableRow>
                      <TableHead className="px-3 py-2">id</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.tier')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.pages')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.modalities')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.files')}</TableHead>
                      <TableHead className="px-3 py-2">{t('eval.updatedAt')}</TableHead>
                      <TableHead className="px-3 py-2" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {datasets.map((item) => (
                      <TableRow key={item.dataset_id}>
                        <TableCell className="px-3 py-2 font-medium">
                          {item.dataset_id}
                          {item.profile === 'rich' ? (
                            <Badge variant="outline" className="ml-1 text-[10px]">rich</Badge>
                          ) : null}
                        </TableCell>
                        <TableCell className="px-3 py-2">{item.tier}</TableCell>
                        <TableCell className="px-3 py-2">{item.pages}</TableCell>
                        <TableCell className="px-3 py-2">
                          {item.files.filter((name) => name !== 'manifest.json').length}
                        </TableCell>
                        <TableCell className="px-3 py-2">{item.files.length}</TableCell>
                        <TableCell className="px-3 py-2">{item.created_at.slice(0, 19)}</TableCell>
                        <TableCell className="px-3 py-2 text-right">
                          <Button size="sm" variant="ghost" onClick={() => void remove(item.dataset_id)}>
                            <TrashIcon className="size-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                    {datasets.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={7} className="text-muted-foreground h-24 text-center">
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
