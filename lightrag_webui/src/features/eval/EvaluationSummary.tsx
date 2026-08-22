import { AlertTriangleIcon, MessageSquareTextIcon, SearchCheckIcon, WaypointsIcon, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import type { MetricItem } from '@/api/eval'
import type { RunCapabilities } from '@/features/eval/runCapabilities'
import { formatMetricValue } from '@/features/eval/utils'

type CaseRow = Record<string, unknown>
type Detail = Record<string, unknown>

function detailOf(row: CaseRow): Detail {
  return row.detail && typeof row.detail === 'object' && !Array.isArray(row.detail)
    ? row.detail as Detail
    : {}
}

function recordAt(row: CaseRow, key: string): Detail {
  const value = row[key] ?? detailOf(row)[key]
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Detail
  if (key !== 'retrieval') return {}
  const detail = detailOf(row)
  const direct: Detail = {}
  for (const retrievalKey of ['recall_at_k', 'first_evidence_rank', 'top_contexts', 'hit_evidence']) {
    const retrievalValue = detail[retrievalKey] ?? row[retrievalKey]
    if (retrievalValue !== undefined) direct[retrievalKey] = retrievalValue
  }
  return Object.keys(direct).length > 0 ? { status: 'observed', ...direct } : {}
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function metric(metrics: Record<string, MetricItem>, key: string): number | null {
  return numeric(metrics[key]?.value)
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

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="border-border/70 flex min-w-0 items-baseline justify-between gap-3 border-t pt-2 first:border-t-0 first:pt-0">
      <dt className="text-muted-foreground text-sm">{label}</dt>
      <dd className="shrink-0 text-right text-sm font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

function Rate({ value }: { value: number | null }) {
  return <>{value == null ? '—' : formatMetricValue(value)}</>
}

interface EvaluationSummaryProps {
  metrics: Record<string, MetricItem>
  rows: CaseRow[]
  capabilities: RunCapabilities
}

/** A scope-neutral overview assembled from the capabilities available for this run. */
export default function EvaluationSummary({ metrics, rows, capabilities }: EvaluationSummaryProps) {
  const answerRows = rows.filter((row) => typeof row.answer === 'string' || typeof row.exact_match === 'boolean')
  const retrievalRows = rows.filter((row) => recordAt(row, 'retrieval').status === 'observed')
  const retrievalTotal = metric(metrics, 'retrieval_cases') ?? retrievalRows.length
  const fullRecall = retrievalRows.filter((row) => recordAt(row, 'retrieval').recall_at_k === 1).length
  const missedRetrieval = retrievalRows.filter((row) => {
    const recall = recordAt(row, 'retrieval').recall_at_k
    return typeof recall === 'number' && recall < 1
  }).length
  const rankedLate = retrievalRows.filter((row) => {
    const rank = recordAt(row, 'retrieval').first_evidence_rank
    return typeof rank === 'number' && rank > 1
  }).length
  const answerTotal = metric(metrics, 'cases') ?? answerRows.length
  const correct = metric(metrics, 'correct_cases') ?? answerRows.filter((row) => row.exact_match === true).length
  const uncertain = answerRows.filter((row) => row.answer_verdict === 'uncertain').length
  const answerWrong = (row: CaseRow) => row.exact_match === false && row.answer_verdict !== 'uncertain'
  const retrievalMissAndWrong = retrievalRows.filter((row) => {
    const recall = recordAt(row, 'retrieval').recall_at_k
    return answerWrong(row) && typeof recall === 'number' && recall < 1
  }).length
  const retrievedButWrong = retrievalRows.filter((row) => answerWrong(row) && recordAt(row, 'retrieval').recall_at_k === 1).length

  const blocks: ReactNode[] = []
  if (capabilities.hasRetrievalMetrics && (retrievalTotal > 0 || metric(metrics, 'recall_at_1') != null || metric(metrics, 'mrr') != null)) {
    blocks.push(
      <SummaryBlock
        key="retrieval"
        title="召回结果"
        description="目标证据是否在候选结果中被召回，以及首次命中的排序位置。"
        icon={SearchCheckIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="完整召回题数" value={`${fullRecall} / ${retrievalTotal}`} />
          <Stat label="Recall@1" value={<Rate value={metric(metrics, 'recall_at_1')} />} />
          <Stat label="Recall@5" value={<Rate value={metric(metrics, 'recall_at_5')} />} />
          <Stat label="MRR" value={<Rate value={metric(metrics, 'mrr')} />} />
        </dl>
      </SummaryBlock>
    )
  }
  if (capabilities.hasAnswerMetrics && (answerTotal > 0 || metric(metrics, 'answer_accuracy') != null)) {
    blocks.push(
      <SummaryBlock
        key="answer"
        title="回答结果"
        description="按题目标准答案与当前评分规则得出的结论。"
        icon={MessageSquareTextIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="正确题数 / 总题数" value={`${correct} / ${answerTotal}`} />
          <Stat label="回答准确率" value={<Rate value={metric(metrics, 'answer_accuracy')} />} />
          {metric(metrics, 'groundedness') != null ? <Stat label="证据支撑率" value={<Rate value={metric(metrics, 'groundedness')} />} /> : null}
          {uncertain > 0 ? <Stat label="需人工复核" value={`${uncertain} 题`} /> : null}
        </dl>
      </SummaryBlock>
    )
  }
  if (capabilities.hasFailureAnalysis && retrievalRows.length > 0) {
    blocks.push(
      <SummaryBlock
        key="failure"
        title={capabilities.hasAnswer ? '失败分析' : '召回失败分析'}
        description={capabilities.hasAnswer
          ? '将检索与回答结果合看，判断优先排查的环节。'
          : '按未召回和排序靠后聚合，帮助定位检索链路问题。'}
        icon={capabilities.hasAnswer ? WaypointsIcon : AlertTriangleIcon}
      >
        <dl className="space-y-2.5">
          <Stat label="未完整召回" value={`${missedRetrieval} 题`} />
          {capabilities.hasGoldRank ? <Stat label="Gold Rank 非首位" value={`${rankedLate} 题`} /> : null}
          {capabilities.hasAnswer ? <Stat label="检索未全命中且回答错误" value={`${retrievalMissAndWrong} 题`} /> : null}
          {capabilities.hasAnswer ? <Stat label="检索完整命中但回答错误" value={`${retrievedButWrong} 题`} /> : null}
        </dl>
      </SummaryBlock>
    )
  }

  if (blocks.length === 0) return null
  return <div className="grid gap-3 xl:grid-cols-3">{blocks}</div>
}
