import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CheckIcon,
  DownloadIcon,
  FileSearchIcon,
  MinusIcon,
  ScanSearchIcon,
  TriangleAlertIcon,
  XIcon
} from 'lucide-react'

import Button from '@/components/ui/Button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import { buildCasesCsv, formatMetricValue } from '@/features/eval/utils'

type Detail = Record<string, unknown>

type NormalizedCase = {
  id: string
  question: string
  answer: string
  expected: string
  passed: boolean | null
  verdict: string
  type: string
  expectedBehavior: string
  scorer: Detail
  retrieval: Detail
  finalContextEvidence: Detail
  raw: Record<string, unknown>
}

function asRecord(value: unknown): Detail {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Detail : {}
}

function asList(value: unknown): Detail[] {
  return Array.isArray(value) ? value.filter((item): item is Detail => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : []
}

function detailValue(row: Record<string, unknown>, key: string): unknown {
  return row[key] ?? asRecord(row.detail)[key]
}

function compactNumber(value: unknown): string {
  return typeof value === 'number' ? formatMetricValue(value) : '—'
}

function normalize(row: Record<string, unknown>): NormalizedCase {
  const question = String(row.question ?? row.question_id ?? '')
  const answer = String(row.answer ?? row.oracle_answer ?? '')
  const expected = String(row.expected ?? row.oracle_expected ?? '')
  const verdict = String(row.answer_verdict ?? '')
  let passed: boolean | null = null
  if (verdict === 'pass') passed = true
  else if (verdict === 'fail') passed = false
  else if (typeof row.exact_match === 'boolean') passed = row.exact_match
  else if (typeof row.passed === 'boolean') passed = row.passed
  return {
    id: String(row.question_id ?? question ?? ''),
    question,
    answer,
    expected,
    passed,
    verdict,
    type: String(row.question_type ?? ''),
    expectedBehavior: String(row.expected_behavior ?? 'answer'),
    scorer: asRecord(detailValue(row, 'scorer')),
    retrieval: asRecord(detailValue(row, 'retrieval')),
    finalContextEvidence: asRecord(detailValue(row, 'final_context_evidence')),
    raw: row
  }
}

function Verdict({ c }: { c: NormalizedCase }) {
  const uncertain = c.verdict === 'uncertain'
  const className = c.passed === true
    ? 'text-emerald-600 dark:text-emerald-400'
    : c.passed === false
      ? 'text-red-600 dark:text-red-400'
      : 'text-muted-foreground'
  return (
    <span className={`ml-auto inline-flex items-center gap-1 text-sm font-medium ${className}`}>
      {c.passed === true ? <CheckIcon className="size-4" /> : c.passed === false ? <XIcon className="size-4" /> : <MinusIcon className="size-4" />}
      {uncertain ? '需复核' : c.passed === true ? '通过' : c.passed === false ? '未通过' : '未判定'}
    </span>
  )
}

function ScoreExplanation({ c }: { c: NormalizedCase }) {
  const reason = typeof c.scorer.reason === 'string' ? c.scorer.reason : ''
  const mode = typeof c.scorer.mode === 'string' ? c.scorer.mode : ''
  const text = c.verdict === 'uncertain'
    ? '此题需要语义评分，但当前运行没有配置语义评分器，因此不计入回答准确率分母。'
    : c.passed === true
      ? '模型回答满足本题的评分规则。'
      : '模型回答未满足本题的评分规则。'
  return (
    <section className="rounded-md border bg-muted/20 px-4 py-3">
      <p className="text-muted-foreground mb-1 text-[11px] font-semibold tracking-[0.14em]">评分结论</p>
      <p className="text-sm leading-6">{text}</p>
      {reason ? <p className="text-muted-foreground mt-1 text-xs leading-5">{reason}</p> : null}
      {mode ? <p className="text-muted-foreground mt-2 text-xs">评分方式：{mode}</p> : null}
    </section>
  )
}

function ContextObservation({ c }: { c: NormalizedCase }) {
  const observation = c.finalContextEvidence
  const status = observation.status
  if (!status) return null
  if (status === 'not_applicable') return null
  if (status !== 'observed') {
    return (
      <p className="text-muted-foreground mt-3 text-xs leading-5">
        最终回答上下文未记录，不能据此判断证据是否真正提供给模型。
      </p>
    )
  }
  const expected = Array.isArray(observation.expected_fact_ids) ? observation.expected_fact_ids.length : 0
  const hit = Array.isArray(observation.hit_fact_ids) ? observation.hit_fact_ids.length : 0
  const missing = Array.isArray(observation.missing_fact_ids) ? observation.missing_fact_ids.map(String) : []
  return (
    <div className="mt-3 border-t pt-3 text-xs">
      <p className="font-medium">最终回答上下文：{hit} / {expected} 条目标事实已进入上下文</p>
      <p className="text-muted-foreground mt-1">
        证据覆盖：{compactNumber(observation.coverage)}
        {typeof observation.context_chars === 'number' ? ` · 上下文 ${observation.context_chars} 字符` : ''}
      </p>
      {missing.length > 0 ? <p className="text-amber-700 dark:text-amber-300 mt-1">未进入上下文：{missing.join('、')}</p> : null}
    </div>
  )
}

function RetrievalEvidence({ c }: { c: NormalizedCase }) {
  const retrieval = c.retrieval
  const status = retrieval.status
  if (status === 'not_applicable') {
    return <p className="text-muted-foreground text-sm">本题要求拒答，不进行检索召回评分。</p>
  }
  if (status !== 'observed') {
    return (
      <p className="text-muted-foreground text-sm leading-6">
        本题没有可用的检索 trace，无法判断检索阶段是否命中目标证据。
      </p>
    )
  }
  const expectedIds = Array.isArray(retrieval.expected_fact_ids) ? retrieval.expected_fact_ids.map(String) : []
  const hitIds = Array.isArray(retrieval.hit_fact_ids) ? retrieval.hit_fact_ids.map(String) : []
  const evidence = asList(retrieval.hit_evidence)
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground text-xs">目标事实命中</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{hitIds.length} / {expectedIds.length}</p>
        </div>
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground text-xs">证据召回@K</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{compactNumber(retrieval.recall_at_k)}</p>
        </div>
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground text-xs">首条命中位置</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{retrieval.first_evidence_rank ? `第 ${retrieval.first_evidence_rank} 条` : '未命中'}</p>
        </div>
      </div>
      {expectedIds.length > 0 ? (
        <p className="text-muted-foreground text-xs leading-5">
          目标：{expectedIds.join('、')}<br />
          命中：{hitIds.length ? hitIds.join('、') : '无'}
        </p>
      ) : null}
      {evidence.length === 0 ? (
        <p className="text-amber-700 dark:text-amber-300 flex items-center gap-1.5 text-sm">
          <TriangleAlertIcon className="size-4" /> 前 K 条检索结果中没有命中目标事实。
        </p>
      ) : (
        <div className="space-y-2">
          <p className="text-muted-foreground text-[11px] font-semibold tracking-[0.14em]">命中证据片段</p>
          {evidence.map((item, index) => (
            <blockquote key={`${String(item.fact_id ?? '')}-${index}`} className="border-primary/25 bg-primary/[0.025] rounded-md border-l-2 px-3 py-2.5">
              <p className="text-muted-foreground mb-1 text-xs">
                {String(item.fact_id ?? '目标事实')} · 第 {String(item.rank ?? '—')} 条
                {item.file_path ? ` · ${String(item.file_path)}` : ''}
                {item.kind ? ` · ${String(item.kind)}` : ''}
              </p>
              <p className="whitespace-pre-wrap break-words text-sm leading-6">{String(item.text ?? '—')}</p>
            </blockquote>
          ))}
        </div>
      )}
      <ContextObservation c={c} />
    </div>
  )
}

function diagnosticHint(c: NormalizedCase): string {
  if (c.verdict === 'uncertain') {
    return '回答表达需要语义评分器复核；当前不把它归因为检索或生成问题。'
  }
  if (c.expectedBehavior === 'abstain') {
    return c.passed ? '模型按要求拒答。' : '该题应该拒答，但模型给出了不可支持的回答。'
  }
  const recall = c.retrieval.recall_at_k
  const context = c.finalContextEvidence
  if (c.passed) return '回答通过；检索数据保留作追溯，不再额外归因。'
  if (typeof recall === 'number' && recall < 1) {
    return '检索没有取全本题所需证据，应优先检查索引、检索参数或文档解析结果。'
  }
  if (context.status === 'observed' && context.available === false) {
    return '检索已命中目标证据，但最终回答上下文不完整；请检查上下文选择或截断。'
  }
  if (typeof recall === 'number' && recall === 1 && context.available === true) {
    return '所需证据已被检索且进入最终上下文，但回答仍未通过；请检查生成模型、提示词或评分规则。'
  }
  return '回答未通过，但当前 trace 不足以可靠定位具体阶段。'
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
    () => cases.filter((c) => {
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
            <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
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
            <SelectContent>
              <SelectItem value="all">全部题型</SelectItem>
              {types.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <span className="text-muted-foreground self-center text-xs">{filtered.length}/{cases.length}</span>
        <Button size="sm" variant="outline" className="ml-auto" onClick={exportCsv}>
          <DownloadIcon className="mr-1 size-4" />
          {t('eval.exportCsv')}
        </Button>
      </div>

      {filtered.length === 0 ? (
        <div className="text-muted-foreground rounded-lg border border-dashed py-12 text-center text-sm">{t('eval.noCases')}</div>
      ) : (
        <div className="space-y-4">
          {filtered.map((c, index) => (
            <article key={`${c.id}-${index}`} className="relative overflow-hidden rounded-lg border bg-card shadow-sm">
              <div className={`absolute inset-y-0 left-0 w-1 ${c.passed === true ? 'bg-emerald-500' : c.passed === false ? 'bg-red-500' : 'bg-muted-foreground/40'}`} />
              <header className="flex flex-wrap items-center gap-2 border-b bg-muted/25 px-5 py-3 pl-6">
                <span className="font-serif text-lg font-semibold">第 {index + 1} 题</span>
                {c.type ? <span className="rounded-full border bg-background px-2 py-0.5 text-xs text-muted-foreground">{c.type}</span> : null}
                <Verdict c={c} />
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
                <ScoreExplanation c={c} />
                <section className="rounded-md border bg-muted/[0.08] p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <ScanSearchIcon className="text-primary size-4" />
                    <h3 className="text-sm font-semibold">检索证据</h3>
                  </div>
                  <RetrievalEvidence c={c} />
                </section>
                <section className="border-border/70 flex gap-2 rounded-md border border-dashed px-3 py-2.5 text-sm leading-6">
                  <FileSearchIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                  <div><span className="font-medium">结果解读：</span>{diagnosticHint(c)}</div>
                </section>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
