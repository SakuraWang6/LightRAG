import { useMemo, useState } from 'react'
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
import { buildCasesCsv, caseFieldLabel } from '@/features/eval/utils'

type NormalizedCase = {
  id: string
  question: string
  answer: string
  expected: string
  passed: boolean | null
  type: string
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
    type: String(row.question_type ?? ''),
    raw: row
  }
}

interface CasesViewProps {
  rows: Record<string, unknown>[]
}

export default function CasesView({ rows }: CasesViewProps) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')

  const cases = useMemo(() => rows.map(normalize), [rows])
  const types = useMemo(() => Array.from(new Set(cases.map((c) => c.type).filter(Boolean))).sort(), [cases])
  const filtered = useMemo(
    () =>
      cases.filter((c) => {
        if (filter === 'pass' && c.passed !== true) return false
        if (filter === 'fail' && c.passed !== false) return false
        if (typeFilter !== 'all' && c.type !== typeFilter) return false
        return true
      }),
    [cases, filter, typeFilter]
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
      <div className="flex flex-wrap items-center gap-2">
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
        <span className="text-muted-foreground self-center text-xs">
          {filtered.length}/{cases.length}
        </span>
        <Button size="sm" variant="outline" className="ml-auto" onClick={exportCsv}>
          <DownloadIcon className="mr-1 size-4" />
          {t('eval.exportCsv')}
        </Button>
      </div>

      {filtered.length === 0 ? (
        <div className="text-muted-foreground rounded-lg border border-dashed py-12 text-center text-sm">
          {t('eval.noCases')}
        </div>
      ) : (
        <div className="space-y-4">
          {filtered.map((c, index) => (
            <article key={`${c.id}-${index}`} className="relative overflow-hidden rounded-lg border bg-card shadow-sm">
              <div className={`absolute inset-y-0 left-0 w-1 ${c.passed === true ? 'bg-emerald-500' : c.passed === false ? 'bg-red-500' : 'bg-muted-foreground/40'}`} />
              <header className="flex flex-wrap items-center gap-2 border-b bg-muted/25 px-5 py-3 pl-6">
                <span className="font-serif text-lg font-semibold">第 {index + 1} 题</span>
                {c.type ? <span className="rounded-full border bg-background px-2 py-0.5 text-xs text-muted-foreground">{c.type}</span> : null}
                <span className={`ml-auto inline-flex items-center gap-1 text-sm font-medium ${c.passed === true ? 'text-emerald-600 dark:text-emerald-400' : c.passed === false ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'}`}>
                  {c.passed === true ? <CheckIcon className="size-4" /> : c.passed === false ? <XIcon className="size-4" /> : <MinusIcon className="size-4" />}
                  {c.passed === true ? t('eval.casePass') : c.passed === false ? t('eval.caseFail') : '未判定'}
                </span>
              </header>
              <div className="space-y-5 px-5 py-5 pl-6">
                <section>
                  <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">题目</p>
                  <p className="font-serif whitespace-pre-wrap break-words text-base leading-7">{c.question || c.id || '—'}</p>
                </section>
                <div className="grid gap-3 lg:grid-cols-2">
                  <section className="rounded-md border bg-muted/25 p-4">
                    <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">模型回答</p>
                    <p className="whitespace-pre-wrap break-words text-sm leading-6">{c.answer || '—'}</p>
                  </section>
                  <section className="rounded-md border border-primary/20 bg-primary/[0.03] p-4">
                    <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">标准答案</p>
                    <p className="whitespace-pre-wrap break-words text-sm leading-6">{c.expected || '—'}</p>
                  </section>
                </div>
                <details className="rounded-md border bg-muted/15 px-3 py-2 text-sm">
                  <summary className="cursor-pointer select-none font-medium">评分明细与证据</summary>
                  <div className="mt-3 border-t pt-3">
                    <CaseDetail c={c} />
                  </div>
                </details>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
