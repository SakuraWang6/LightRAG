import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, FileTextIcon, ListIcon, BarChart3Icon, BeakerIcon } from 'lucide-react'

import type { EvalArtifact, EvalRunDetail, TableData } from '@/api/eval'
import Badge from '@/components/ui/Badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/Card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import EmptyCard from '@/components/ui/EmptyCard'
import ConditionChips from '@/features/eval/ConditionChips'
import EvalDataTable from '@/features/eval/EvalDataTable'
import MarkdownReport from '@/features/eval/MarkdownReport'
import MetricCards from '@/features/eval/MetricCards'
import MethodCompare from '@/features/eval/MethodCompare'
import ReportDocument from '@/features/eval/ReportDocument'
import { formatDate, runKindClass, statusBadgeClass } from '@/features/eval/utils'

interface EvalRunDetailProps {
  run: EvalRunDetail
}

function RunHeader({ run }: { run: EvalRunDetail }) {
  const { t } = useTranslation()
  return (
    <div className="border-b px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{run.label}</h2>
        <Badge variant="outline" className={runKindClass(run.kind)}>{run.kind}</Badge>
        {run.status ? <Badge variant="outline" className={statusBadgeClass(run.status)}>{run.status}</Badge> : null}
      </div>
      <p className="text-muted-foreground mt-1 text-xs">
        {t('eval.updatedAt')}: {formatDate(run.updated_at)}
        {' · '}
        {run.artifacts.length} {t('eval.artifactLabel')}
      </p>
      <div className="mt-2">
        <ConditionChips conditions={run.conditions} limit={10} />
      </div>
    </div>
  )
}

function ArtifactMetricsCard({ artifact }: { artifact: EvalArtifact }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          {artifact.title}
          <Badge variant="outline" className="text-xs">{artifact.kind}</Badge>
        </CardTitle>
        <CardDescription className="text-xs">
          {artifact.updated_at ? formatDate(artifact.updated_at) : artifact.rel_path}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {artifact.error ? (
          <p className="text-destructive text-sm">{artifact.error}</p>
        ) : (
          <MetricCards metrics={artifact.metrics} />
        )}
      </CardContent>
    </Card>
  )
}

function StandardRunView({ run }: { run: EvalRunDetail }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('metrics')
  const caseArtifacts = useMemo(
    () => run.artifacts.filter((artifact) => artifact.table.rows.length > 0),
    [run.artifacts]
  )
  const reportArtifacts = useMemo(
    () => run.artifacts.filter((artifact) => artifact.report_md),
    [run.artifacts]
  )
  const [selectedCasePath, setSelectedCasePath] = useState<string | null>(null)
  const [selectedReportPath, setSelectedReportPath] = useState<string | null>(null)
  const selectedCase = caseArtifacts.find((a) => a.rel_path === selectedCasePath) ?? caseArtifacts[0]
  const selectedReport = reportArtifacts.find((a) => a.rel_path === selectedReportPath) ?? reportArtifacts[0]

  return (
    <div className="p-4">
      <div className="mb-4">
        <MetricCards
          metrics={Object.values(run.headline ?? {}).length > 0
            ? Object.values(run.headline ?? {})
            : (run.artifacts[0]?.metrics ?? []).slice(0, 12)}
        />
      </div>

      {run.artifacts.some((a) => a.error) ? (
        <div className="bg-destructive/10 text-destructive mb-4 flex items-center gap-2 rounded-md border border-destructive/30 px-3 py-2 text-sm">
          <AlertTriangle className="size-4" />
          {t('eval.artifactErrors')}
        </div>
      ) : null}

      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="metrics">
            <BarChart3Icon className="mr-1 size-4" />
            {t('eval.metrics')}
          </TabsTrigger>
          <TabsTrigger value="cases">
            <ListIcon className="mr-1 size-4" />
            {t('eval.cases')}
          </TabsTrigger>
          <TabsTrigger value="reports">
            <FileTextIcon className="mr-1 size-4" />
            {t('eval.reports')}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="metrics" className="mt-3 space-y-3">
          {run.artifacts.map((artifact) => (
            <ArtifactMetricsCard key={artifact.rel_path} artifact={artifact} />
          ))}
        </TabsContent>

        <TabsContent value="cases" className="mt-3">
          {caseArtifacts.length === 0 ? (
            <EmptyCard title={t('eval.noCases')} description={t('eval.noCasesHint')} />
          ) : (
            <div className="space-y-3">
              <div className="w-72">
                <Select value={selectedCase?.rel_path} onValueChange={setSelectedCasePath}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('eval.selectArtifact')} />
                  </SelectTrigger>
                  <SelectContent>
                    {caseArtifacts.map((artifact) => (
                      <SelectItem key={artifact.rel_path} value={artifact.rel_path}>
                        {artifact.title} ({artifact.table.rows.length})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {selectedCase ? <EvalDataTable data={selectedCase.table} /> : null}
            </div>
          )}
        </TabsContent>

        <TabsContent value="reports" className="mt-3">
          {reportArtifacts.length === 0 ? (
            <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
          ) : (
            <div className="space-y-3">
              <div className="w-72">
                <Select value={selectedReport?.rel_path} onValueChange={setSelectedReportPath}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('eval.selectReport')} />
                  </SelectTrigger>
                  <SelectContent>
                    {reportArtifacts.map((artifact) => (
                      <SelectItem key={artifact.rel_path} value={artifact.rel_path}>
                        {artifact.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {selectedReport?.report_md ? (
                <Card>
                  <CardContent className="p-4">
                    <MarkdownReport content={selectedReport.report_md} />
                  </CardContent>
                </Card>
              ) : null}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ExperimentView({ run }: { run: EvalRunDetail }) {
  const { t } = useTranslation()
  const [tab, setTab] = useState('methods')
  const experimentArtifacts = run.artifacts.filter((artifact) => artifact.kind === 'experiment')
  const methodArtifact = experimentArtifacts.find((a) => a.table.rows.length > 0) ?? experimentArtifacts[0]
  const caseArtifacts = run.artifacts.filter((artifact) => artifact.meta.cases)
  const reportArtifacts = useMemo(
    () => run.artifacts.filter((artifact) => artifact.report_md),
    [run.artifacts]
  )
  const [selectedReportPath, setSelectedReportPath] = useState<string | null>(null)
  const selectedReport = reportArtifacts.find((a) => a.rel_path === selectedReportPath) ?? reportArtifacts[0]

  return (
    <div className="p-4">
      <Tabs value={tab} onValueChange={setTab} className="w-full">
        <TabsList>
          <TabsTrigger value="methods">
            <BeakerIcon className="mr-1 size-4" />
            {t('eval.methodCompare')}
          </TabsTrigger>
          <TabsTrigger value="cases">
            <ListIcon className="mr-1 size-4" />
            {t('eval.cases')}
          </TabsTrigger>
          <TabsTrigger value="reports">
            <FileTextIcon className="mr-1 size-4" />
            {t('eval.reports')}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="methods" className="mt-3">
          {methodArtifact ? (
            <MethodCompare table={methodArtifact.table} />
          ) : (
            <EmptyCard title={t('eval.noMethods')} description={t('eval.noMethodsHint')} />
          )}
        </TabsContent>

        <TabsContent value="cases" className="mt-3">
          {caseArtifacts.length === 0 ? (
            <EmptyCard title={t('eval.noCases')} description={t('eval.noCasesHint')} />
          ) : (
            <div className="space-y-3">
              {caseArtifacts.map((artifact) => (
                <Card key={artifact.rel_path}>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm">{artifact.title}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <EvalDataTable data={artifact.meta.cases as TableData} maxHeight="max-h-[50vh]" />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="reports" className="mt-3">
          {reportArtifacts.length === 0 ? (
            <EmptyCard title={t('eval.noReports')} description={t('eval.noReportsHint')} />
          ) : (
            <div className="space-y-3">
              <div className="w-72">
                <Select value={selectedReport?.rel_path} onValueChange={setSelectedReportPath}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('eval.selectReport')} />
                  </SelectTrigger>
                  <SelectContent>
                    {reportArtifacts.map((artifact) => (
                      <SelectItem key={artifact.rel_path} value={artifact.rel_path}>
                        {artifact.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {selectedReport?.report_md ? (
                <Card>
                  <CardContent className="p-4">
                    <MarkdownReport content={selectedReport.report_md} />
                  </CardContent>
                </Card>
              ) : null}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

export default function EvalRunDetail({ run }: EvalRunDetailProps) {
  if (run.kind === 'report') {
    const report = run.artifacts.find((artifact) => artifact.report_md) ?? run.artifacts[0]
    return (
      <div className="flex h-full flex-col overflow-hidden">
        <RunHeader run={run} />
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {report ? <ReportDocument artifact={report} /> : <EmptyCard title="—" description="—" />}
        </div>
      </div>
    )
  }
  if (run.kind === 'experiment') {
    return (
      <div className="flex h-full flex-col overflow-hidden">
        <RunHeader run={run} />
        <div className="min-h-0 flex-1 overflow-auto">
          <ExperimentView run={run} />
        </div>
      </div>
    )
  }
  return (
    <div className="flex h-full flex-col overflow-hidden">
      <RunHeader run={run} />
      <div className="min-h-0 flex-1 overflow-auto">
        <StandardRunView run={run} />
      </div>
    </div>
  )
}
