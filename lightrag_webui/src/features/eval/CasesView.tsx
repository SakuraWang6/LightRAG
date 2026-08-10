import { Fragment, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, DownloadIcon, XIcon, MinusIcon } from 'lucide-react'

import Button from '@/components/ui/Button'
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
import { buildCasesCsv, caseFieldLabel } from '@/features/eval/utils'

type NormalizedCase = {
  id: string
  question: string
  answer: string
  expected: string
  passed: boolean | null
  group: string
  type: string
  scenario: string
  failureCategory: string
  method: string
  raw: Record<string, unknown>
}

const DETAIL_SCALARS = [
  'recall_at_k',
  'reciprocal_rank',
  'context_precision',
  'object_hit_rate',
  'exact_match',
  'grounded',
  'evidence_available',
  'abstention_correct'
]

function formatDetailValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? '✓' : '✗'
  if (typeof value === 'number') {
    const formatted = Number(value).toFixed(4).replace(/\.?0+$/, '')
    return formatted || '0'
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? '[]' : value.map((item) => JSON.stringify(item)).join(' · ')
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value)
  }
  return value == null ? '—' : String(value)
}

function CaseDetail({ c }: { c: NormalizedCase }) {
  const { i18n } = useTranslation()
  const entries = useMemo(() => {
    const detail = (c.raw.detail ?? {}) as Record<string, unknown>
    const merged: Record<string, unknown> = {}
    for (const key of DETAIL_SCALARS) {
      const value = c.raw[key]
      if (value !== undefined && value !== null) merged[key] = value
    }
    for (const [key, value] of Object.entries(detail)) {
      merged[key] = value
    }
    return merged
  }, [c])
  return (
    <dl className="space-y-1">
      {Object.entries(entries).map(([key, value]) => (
        <div key={key} className="flex gap-3 text-xs">
          <dt className="text-muted-foreground w-40 shrink-0 break-all font-medium">
            {caseFieldLabel(key, i18n.language)}
          </dt>
          <dd className="min-w-0 break-words">
            {key === 'hit_evidence' && Array.isArray(value) ? (
              <div className="space-y-2">
                {value.map((item, index) => (
                  <div key={index} className="rounded-md border p-2">
                    <div className="text-muted-foreground mb-1">
                      {String(item.fact_id ?? '')} · rank {String(item.rank ?? '—')}
                      {item.file_path ? ` · ${String(item.file_path)}` : ''}
                      {item.kind ? ` · ${String(item.kind)}` : ''}
                    </div>
                    <p className="whitespace-pre-wrap break-words">
                      {item.text ? String(item.text) : '—'}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              formatDetailValue(value)
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function normalize(row: Record<string, unknown>): NormalizedCase {
  const question = String(row.question ?? row.question_id ?? '')
  const answer = String(row.answer ?? row.oracle_answer ?? '')
  const expected = String(row.expected ?? row.oracle_expected ?? '')
  let passed: boolean | null = null
  if (typeof row.exact_match === 'boolean') {
    passed = row.exact_match
  } else if (typeof row.passed === 'boolean') {
    passed = row.passed
  } else if (typeof row.grounded === 'boolean' && row.exact_match === undefined) {
    passed = null // retrieval-style rows only carry recall, not pass/fail
  }
  return {
    id: String(row.question_id ?? question ?? ''),
    question,
    answer,
    expected,
    passed,
    group: String(row.question_group ?? ''),
    type: String(row.question_type ?? ''),
    scenario: Array.isArray(row.scenario_labels) ? row.scenario_labels.map(String).join(' · ') : String(row.scenario ?? ''),
    failureCategory: String(row.failure_category ?? row.primary_cause ?? row.diagnosis?.primary_cause ?? ''),
    method: String(row.method ?? row.arm ?? ''),
    raw: row
  }
}

interface CasesViewProps {
  rows: Record<string, unknown>[]
}

export default function CasesView({ rows }: CasesViewProps) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('all')
  const [groupFilter, setGroupFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [scenarioFilter, setScenarioFilter] = useState('all')
  const [failureFilter, setFailureFilter] = useState('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const cases = useMemo(() => rows.map(normalize), [rows])
  const groups = useMemo(
    () => Array.from(new Set(cases.map((c) => c.group).filter(Boolean))).sort(),
    [cases]
  )
  const types = useMemo(() => Array.from(new Set(cases.map((c) => c.type).filter(Boolean))).sort(), [cases])
  const scenarios = useMemo(() => Array.from(new Set(cases.flatMap((c) => c.scenario.split(' · ').filter(Boolean)))).sort(), [cases])
  const failures = useMemo(() => Array.from(new Set(cases.map((c) => c.failureCategory).filter(Boolean))).sort(), [cases])
  const filtered = useMemo(
    () =>
      cases.filter((c) => {
        if (filter === 'pass' && c.passed !== true) return false
        if (filter === 'fail' && c.passed !== false) return false
        if (groupFilter !== 'all' && c.group !== groupFilter) return false
        if (typeFilter !== 'all' && c.type !== typeFilter) return false
        if (scenarioFilter !== 'all' && !c.scenario.split(' · ').includes(scenarioFilter)) return false
        if (failureFilter !== 'all' && c.failureCategory !== failureFilter) return false
        return true
      }),
    [cases, filter, groupFilter, typeFilter, scenarioFilter, failureFilter]
  )

  const exportCsv = () => {
    const csv = buildCasesCsv(filtered.map((c) => c.raw))
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'eval-cases.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="w-44">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('eval.caseAll')}</SelectItem>
              <SelectItem value="pass">{t('eval.casePass')}</SelectItem>
              <SelectItem value="fail">{t('eval.caseFail')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-44">
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="h-8"><SelectValue placeholder="题型" /></SelectTrigger>
            <SelectContent><SelectItem value="all">全部题型</SelectItem>{types.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="w-44">
          <Select value={scenarioFilter} onValueChange={setScenarioFilter}>
            <SelectTrigger className="h-8"><SelectValue placeholder="场景" /></SelectTrigger>
            <SelectContent><SelectItem value="all">全部场景</SelectItem>{scenarios.map((scenario) => <SelectItem key={scenario} value={scenario}>{scenario}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="w-44">
          <Select value={failureFilter} onValueChange={setFailureFilter}>
            <SelectTrigger className="h-8"><SelectValue placeholder="失败分类" /></SelectTrigger>
            <SelectContent><SelectItem value="all">全部失败分类</SelectItem>{failures.map((failure) => <SelectItem key={failure} value={failure}>{failure}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="w-44">
          <Select value={groupFilter} onValueChange={setGroupFilter}>
            <SelectTrigger className="h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('eval.caseAllGroups')}</SelectItem>
              {groups.map((group) => (
                <SelectItem key={group} value={group}>
                  {group}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="text-muted-foreground self-center text-xs">
          {filtered.length}/{cases.length}
        </span>
        <Button size="sm" variant="outline" className="ml-auto" onClick={exportCsv}>
          <DownloadIcon className="mr-1 size-4" />
          {t('eval.exportCsv')}
        </Button>
      </div>

      <div className="overflow-auto rounded-md border">
        <Table className="min-w-full text-left text-sm">
          <TableHeader className="sticky top-0 bg-background">
            <TableRow>
              <TableHead className="px-3 py-2">{t('eval.caseQuestion')}</TableHead>
              <TableHead className="px-3 py-2">{t('eval.caseAnswer')}</TableHead>
              <TableHead className="px-3 py-2">{t('eval.caseExpected')}</TableHead>
              <TableHead className="px-3 py-2">{t('eval.caseResult')}</TableHead>
              <TableHead className="px-3 py-2">{t('eval.caseType')}</TableHead>
              <TableHead className="px-3 py-2">场景 / 失败分类</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((c, index) => {
              const isExpanded = expanded === c.id
              return (
                <Fragment key={`${c.id}-${index}`}>
                  <TableRow
                    className="cursor-pointer"
                    onClick={() => setExpanded(isExpanded ? null : c.id)}
                  >
                    <TableCell className="max-w-[240px] px-3 py-2">
                      <span className="line-clamp-2 whitespace-pre-wrap break-words">
                        {c.question || c.id || '—'}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-[260px] px-3 py-2">
                      <span className={isExpanded ? 'whitespace-pre-wrap break-words' : 'line-clamp-2 whitespace-pre-wrap break-words'}>
                        {c.answer || '—'}
                      </span>
                    </TableCell>
                    <TableCell className="max-w-[200px] px-3 py-2">
                      <span className={isExpanded ? 'whitespace-pre-wrap break-words' : 'line-clamp-2 whitespace-pre-wrap break-words'}>
                        {c.expected || '—'}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap px-3 py-2">
                      {c.passed === true ? (
                        <span className="text-emerald-600 dark:text-emerald-400 inline-flex items-center gap-1">
                          <CheckIcon className="size-4" /> {t('eval.casePass')}
                        </span>
                      ) : c.passed === false ? (
                        <span className="text-red-600 dark:text-red-400 inline-flex items-center gap-1">
                          <XIcon className="size-4" /> {t('eval.caseFail')}
                        </span>
                      ) : (
                        <span className="text-muted-foreground inline-flex items-center gap-1">
                          <MinusIcon className="size-4" /> —
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap px-3 py-2">
                      {[c.group, c.type, c.method].filter(Boolean).join(' · ') || '—'}
                    </TableCell>
                    <TableCell className="max-w-[180px] px-3 py-2 text-xs">
                      {[c.scenario, c.failureCategory].filter(Boolean).join(' · ') || '—'}
                    </TableCell>
                  </TableRow>
                  {isExpanded ? (
                    <TableRow className="bg-muted/40">
                      <TableCell colSpan={5} className="px-3 py-2">
                        <CaseDetail c={c} />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              )
            })}
            {filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="text-muted-foreground h-24 text-center">
                  {t('eval.noCases')}
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
