import { MessageSquareTextIcon, SearchCheckIcon, WaypointsIcon, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import type { MetricItem } from '@/api/eval'
import { formatMetricValue } from '@/features/eval/utils'

type CaseRow = Record<string, unknown>
type Detail = Record<string, unknown>

function detailOf(row: CaseRow): Detail {
  return (row.detail && typeof row.detail === 'object') ? row.detail as Detail : {}
}

function recordAt(row: CaseRow, key: string): Detail {
  const value = row[key] ?? detailOf(row)[key]
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Detail : {}
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function metric(metrics: Record<string, MetricItem>, key: string): number | null {
  return numeric(metrics[key]?.value)
}

function Rate({ value }: { value: number | null }) {
  return <>{value == null ? '—' : formatMetricValue(value)}</>
}

function SummaryBlock({
  title,
  description,
  icon: Icon,
  children
}: {
  title: string
  description: string
  icon: LucideIcon
  children: ReactNode
}) {
  return (
    <section className="rounded-lg border bg-card px-4 py-4 shadow-sm">
      <div className="mb-4 flex items-start gap-3">
        <span className="bg-primary/10 text-primary mt-0.5 rounded-md p-2">
          <Icon className="size-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">{description}</p>
        </div>
      </div>
      {children}
    </section>
  )
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-border/70 flex min-w-0 items-baseline justify-between gap-3 border-t pt-2 first:border-t-0 first:pt-0">
      <dt className="text-muted-foreground text-sm">{label}</dt>
      <dd className="shrink-0 text-right text-sm font-semibold tabular-nums">{children}</dd>
    </div>
  )
}

interface EvaluationSummaryProps {
  metrics: Record<string, MetricItem>
  rows: CaseRow[]
}

export default function EvaluationSummary({ metrics, rows }: EvaluationSummaryProps) {
  const answerRows = rows.filter((row) => typeof row.answer === 'string' || typeof row.exact_match === 'boolean')
  const answerTotal = metric(metrics, 'cases') ?? answerRows.length
  const correct = metric(metrics, 'correct_cases') ?? answerRows.filter((row) => row.exact_match === true).length
  const uncertain = answerRows.filter((row) => row.answer_verdict === 'uncertain').length

  const retrievalRows = answerRows.filter((row) => recordAt(row, 'retrieval').status === 'observed')
  const retrievalTotal = metric(metrics, 'retrieval_cases') ?? retrievalRows.length
  const fullRecall = retrievalRows.filter((row) => recordAt(row, 'retrieval').recall_at_k === 1).length
  const answerWrong = (row: CaseRow) => row.exact_match === false && row.answer_verdict !== 'uncertain'
  const retrievalMissAndWrong = retrievalRows.filter((row) => {
    const retrieval = recordAt(row, 'retrieval')
    return answerWrong(row) && typeof retrieval.recall_at_k === 'number' && retrieval.recall_at_k < 1
  }).length
  const retrievedButWrong = retrievalRows.filter((row) => {
    const retrieval = recordAt(row, 'retrieval')
    return answerWrong(row) && retrieval.recall_at_k === 1
  }).length
  const contextUnobservable = answerRows.filter(
    (row) => recordAt(row, 'final_context_evidence').status === 'unavailable'
  ).length

  return (
    <div className="grid gap-3 xl:grid-cols-3">
      <SummaryBlock
        title="回答测评"
        description="按题目标准答案与当前评分规则得出的结论。"
        icon={MessageSquareTextIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="正确题数 / 总题数">{correct} / {answerTotal}</Stat>
          <Stat label="回答准确率"><Rate value={metric(metrics, 'answer_accuracy')} /></Stat>
          {uncertain > 0 ? <Stat label="需人工复核">{uncertain} 题</Stat> : null}
        </dl>
      </SummaryBlock>

      <SummaryBlock
        title="检索测评"
        description="只统计有标准证据的非拒答题；用于定位证据是否被召回。"
        icon={SearchCheckIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="目标证据完整命中">{fullRecall} / {retrievalTotal}</Stat>
          <Stat label="平均证据召回@K"><Rate value={metric(metrics, 'average_recall')} /></Stat>
          <Stat label="首条证据排序（MRR）"><Rate value={metric(metrics, 'mrr')} /></Stat>
        </dl>
      </SummaryBlock>

      <SummaryBlock
        title="定位提示"
        description="把检索和回答合看，帮助决定优先排查的环节。"
        icon={WaypointsIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="检索未全命中且回答错误">{retrievalMissAndWrong} 题</Stat>
          <Stat label="检索完整命中但回答错误">{retrievedButWrong} 题</Stat>
          {contextUnobservable > 0 ? <Stat label="最终上下文未记录">{contextUnobservable} 题</Stat> : null}
        </dl>
      </SummaryBlock>
    </div>
  )
}
