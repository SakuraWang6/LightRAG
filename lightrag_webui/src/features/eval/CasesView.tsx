import { Fragment, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, XIcon, MinusIcon } from 'lucide-react'

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

type NormalizedCase = {
  id: string
  question: string
  answer: string
  expected: string
  passed: boolean | null
  group: string
  type: string
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
          <dt className="text-muted-foreground w-40 shrink-0 break-all font-medium">{key}</dt>
          <dd className="min-w-0 break-words">{formatDetailValue(value)}</dd>
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
  const [expanded, setExpanded] = useState<string | null>(null)

  const cases = useMemo(() => rows.map(normalize), [rows])
  const groups = useMemo(
    () => Array.from(new Set(cases.map((c) => c.group).filter(Boolean))).sort(),
    [cases]
  )
  const filtered = useMemo(
    () =>
      cases.filter((c) => {
        if (filter === 'pass' && c.passed !== true) return false
        if (filter === 'fail' && c.passed !== false) return false
        if (groupFilter !== 'all' && c.group !== groupFilter) return false
        return true
      }),
    [cases, filter, groupFilter]
  )

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
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
