import type { EvalArtifact, EvalRun, MetricItem } from '@/api/eval'

export type EvaluationScope = 'retrieval_only' | 'end_to_end'
export type RetrievalDiagnostics = 'summary' | 'detailed'
export type MetricDomain = 'retrieval' | 'answer' | 'other'

type CapabilityInput = Pick<
  EvalRun,
  'evaluation_scope' | 'retrieval_diagnostics' | 'retrieval_evaluation' | 'answer_evaluation' | 'headline' | 'status' | 'progress'
> & {
  artifacts?: EvalArtifact[]
}

/**
 * The client-facing contract for an evaluation run.
 *
 * Scope answers "what was this run allowed to do?" while diagnostics answers
 * "how deeply can the retrieval path be inspected?" Artifact signals refine
 * the latter for historic/partial runs. Views consume this object and never
 * need to reinterpret scope themselves.
 */
export type RunCapabilities = {
  scope: EvaluationScope
  diagnostics: RetrievalDiagnostics
  isLegacy: boolean
  hasRetrieval: boolean
  hasAnswer: boolean
  hasRetrievalMetrics: boolean
  hasRetrievalDetails: boolean
  hasAnswerMetrics: boolean
  hasAnswerDetails: boolean
  hasFailureAnalysis: boolean
  hasCandidateRanking: boolean
  hasGoldRank: boolean
  hasRetrievalCases: boolean
  hasDetailedDiagnostics: boolean
}

export const ANSWER_METRIC_KEYS = new Set([
  'correct_cases',
  'answer_accuracy',
  'abstention_accuracy',
  'numeric_unit_accuracy',
  'formula_accuracy',
  'table_cell_accuracy',
  'groundedness',
  'ungrounded_rate',
  'citation_presence',
  'citation_correctness',
  'evidence_available',
  'final_context_observable_rate',
  'final_context_evidence_coverage',
  'final_context_evidence_available',
  'cases'
])

export const RETRIEVAL_METRIC_KEYS = new Set([
  'retrieval_cases',
  'average_recall',
  'mrr',
  'recall_at_1',
  'recall_at_3',
  'recall_at_5',
  'full_recall_at_1',
  'full_recall_at_3',
  'full_recall_at_5',
  'first_rank_one_cases',
  'mean_fact_mrr',
  'context_precision',
  'object_hit_rate',
  'full_recall_cases'
])

export function metricDomain(key: string): MetricDomain {
  if (ANSWER_METRIC_KEYS.has(key)) return 'answer'
  if (RETRIEVAL_METRIC_KEYS.has(key)) return 'retrieval'
  return 'other'
}

function hasMetric(metrics: Iterable<MetricItem>, domain: MetricDomain): boolean {
  return Array.from(metrics).some((item) => metricDomain(item.key) === domain && item.value != null)
}

function rowDetail(row: Record<string, unknown>, key: string): Record<string, unknown> {
  const nested = row.detail
  const value = row[key] ?? (nested && typeof nested === 'object' && !Array.isArray(nested)
    ? (nested as Record<string, unknown>)[key]
    : undefined)
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function retrievalDetail(row: Record<string, unknown>): Record<string, unknown> {
  const embedded = rowDetail(row, 'retrieval')
  if (Object.keys(embedded).length > 0) return embedded
  const detail = row.detail && typeof row.detail === 'object' && !Array.isArray(row.detail)
    ? row.detail as Record<string, unknown>
    : {}
  const keys = ['recall_at_k', 'first_evidence_rank', 'top_contexts', 'hit_evidence']
  const result: Record<string, unknown> = {}
  for (const key of keys) {
    const value = detail[key] ?? row[key]
    if (value !== undefined) result[key] = value
  }
  return result
}

function hasCaseSignal(artifacts: EvalArtifact[], predicate: (row: Record<string, unknown>) => boolean): boolean {
  return artifacts.some((artifact) => artifact.kind === 'cases' && artifact.table.rows.some(predicate))
}

function inferredScope(input: CapabilityInput): { scope: EvaluationScope; isLegacy: boolean } {
  if (input.evaluation_scope === 'retrieval_only' || input.evaluation_scope === 'end_to_end') {
    return { scope: input.evaluation_scope, isLegacy: false }
  }
  // Historic runs did not persist scope. Prefer explicit producer flags and
  // answer signals; retain the old full evaluation behaviour as a last resort.
  if (input.answer_evaluation?.enabled === false) return { scope: 'retrieval_only', isLegacy: true }
  if (input.answer_evaluation?.enabled === true) return { scope: 'end_to_end', isLegacy: true }
  const artifacts = input.artifacts ?? []
  const metrics = [
    ...Object.values(input.headline ?? {}),
    ...artifacts.flatMap((artifact) => artifact.metrics)
  ]
  if (hasMetric(metrics, 'answer') || hasCaseSignal(artifacts, (row) => typeof row.answer === 'string')) {
    return { scope: 'end_to_end', isLegacy: true }
  }
  if (hasMetric(metrics, 'retrieval') || input.retrieval_evaluation?.enabled === true) {
    return { scope: 'retrieval_only', isLegacy: true }
  }
  return { scope: 'end_to_end', isLegacy: true }
}

function inferredDiagnostics(input: CapabilityInput): RetrievalDiagnostics {
  if (input.retrieval_diagnostics === 'detailed' || input.retrieval_diagnostics === 'summary') {
    return input.retrieval_diagnostics
  }
  const artifacts = input.artifacts ?? []
  const hasRanking = hasCaseSignal(artifacts, (row) => {
    const retrieval = retrievalDetail(row)
    return Array.isArray(retrieval.top_contexts) || typeof retrieval.first_evidence_rank === 'number'
  })
  return hasRanking ? 'detailed' : 'summary'
}

export function getRunCapabilities(input: CapabilityInput): RunCapabilities {
  const { scope, isLegacy } = inferredScope(input)
  const diagnostics = inferredDiagnostics(input)
  const artifacts = input.artifacts ?? []
  const allMetrics = [
    ...Object.values(input.headline ?? {}),
    ...artifacts.flatMap((artifact) => artifact.metrics)
  ]
  const casesArtifact = artifacts.find((artifact) => artifact.kind === 'cases')
  const rows = casesArtifact?.table.rows ?? []
  const hasCases = rows.length > 0
  const hasRetrieval = scope === 'retrieval_only' || scope === 'end_to_end'
  const hasAnswer = scope === 'end_to_end'
  const isLive = ['queued', 'pending', 'running'].includes(input.status ?? '') ||
    ['queued', 'pending', 'running'].includes(input.progress?.status ?? '')
  const hasRetrievalMetrics = hasRetrieval && (isLive || hasMetric(allMetrics, 'retrieval'))
  const hasAnswerMetrics = hasAnswer && (isLive || hasMetric(allMetrics, 'answer'))
  const recordedRetrievalCases = hasRetrieval && hasCases && rows.some((row) => {
    const retrieval = retrievalDetail(row)
    return Object.keys(retrieval).length > 0 || typeof row.recall_at_k === 'number'
  })
  // Case sheets and candidate evidence are a diagnostics-depth feature. The
  // backend can retain raw rows for a summary run, but that does not make the
  // UI a second "detailed page" by accident.
  const hasRetrievalCases = hasRetrieval && diagnostics === 'detailed' && recordedRetrievalCases
  const hasAnswerDetails = hasAnswer && diagnostics === 'detailed' && hasCases && rows.some((row) =>
    typeof row.answer === 'string' || typeof row.exact_match === 'boolean'
  )
  const hasCandidateRanking = diagnostics === 'detailed' && hasRetrievalCases && rows.some((row) => {
    const retrieval = retrievalDetail(row)
    return Array.isArray(retrieval.top_contexts) && retrieval.top_contexts.length > 0
  })
  const hasGoldRank = diagnostics === 'detailed' && hasRetrievalCases && rows.some((row) => {
    const retrieval = retrievalDetail(row)
    return typeof retrieval.first_evidence_rank === 'number' || Array.isArray(retrieval.hit_evidence)
  })
  const hasRetrievalDetails = diagnostics === 'detailed' && hasRetrievalCases
  const hasFailureAnalysis = hasCases && (
    rows.some((row) => {
      const retrieval = retrievalDetail(row)
      return typeof retrieval.recall_at_k === 'number'
    }) ||
    (hasAnswer && rows.some((row) => typeof row.exact_match === 'boolean'))
  )

  return {
    scope,
    diagnostics,
    isLegacy,
    hasRetrieval,
    hasAnswer,
    hasRetrievalMetrics,
    hasRetrievalDetails,
    hasAnswerMetrics,
    hasAnswerDetails,
    hasFailureAnalysis,
    hasCandidateRanking,
    hasGoldRank,
    hasRetrievalCases,
    hasDetailedDiagnostics: diagnostics === 'detailed'
  }
}

/** Capability adapter for the create form before a persisted run exists. */
export function getDraftRunCapabilities(
  scope: EvaluationScope,
  diagnostics: RetrievalDiagnostics
): RunCapabilities {
  return getRunCapabilities({
    evaluation_scope: scope,
    retrieval_diagnostics: diagnostics,
    headline: {},
    progress: {},
    status: 'pending'
  })
}

export function runKindLabel(capabilities: RunCapabilities): string {
  return capabilities.hasAnswer ? 'END-TO-END' : 'RETRIEVAL'
}

export function runTitle(capabilities: RunCapabilities): string {
  return capabilities.hasAnswer ? '测评结果' : '召回测评结果'
}

export function listSummaryMetricKeys(capabilities: RunCapabilities): string[] {
  return capabilities.hasAnswer
    ? ['answer_accuracy', 'recall_at_5', 'groundedness', 'failed_cases']
    : ['recall_at_1', 'recall_at_5', 'mrr', 'failed_retrieval_cases']
}

export type RunListSummaryItem = {
  key: string
  label: string
  value: MetricItem['value']
}

/** List cards use a different metric priority per capability, including a
 * derived failure count when the producer only records totals and passes. */
export function getRunListSummary(
  capabilities: RunCapabilities,
  headline: Record<string, MetricItem> | null | undefined
): RunListSummaryItem[] {
  const metrics = headline ?? {}
  const item = (key: string): RunListSummaryItem | null => {
    const metric = metrics[key]
    return metric?.value != null ? { key, label: metric.label || key, value: metric.value } : null
  }
  const numericMetric = (key: string) => typeof metrics[key]?.value === 'number' ? metrics[key].value : null
  const requested = listSummaryMetricKeys(capabilities).slice(0, 3)
  const result = requested.map(item).filter((value): value is RunListSummaryItem => Boolean(value))
  if (capabilities.hasAnswer) {
    const total = numericMetric('cases')
    const correct = numericMetric('correct_cases')
    if (total != null && correct != null) {
      result.push({ key: 'failed_cases', label: '失败案例', value: Math.max(0, total - correct) })
    }
  } else {
    const total = numericMetric('retrieval_cases')
    const fullRecall = numericMetric('full_recall_cases')
    if (total != null && fullRecall != null) {
      result.push({ key: 'failed_retrieval_cases', label: '未完整召回', value: Math.max(0, total - fullRecall) })
    }
  }
  return result
}

export type RunPipelineStage = {
  id: string
  label: string
  phaseNames: string[]
}

export function getRunPipelineStages(capabilities: RunCapabilities): RunPipelineStage[] {
  // These are observable runner phases, not an aspirational product diagram.
  // The runner emits one `retrieval` phase for retrieval + recall scoring and
  // one `answer` phase for generation + scoring, so splitting either phase in
  // the UI would create fake, independently progressing steps.
  const stages: RunPipelineStage[] = [
    { id: 'preparation', label: '运行环境准备', phaseNames: ['runtime', 'preparation', 'starting', 'queued', 'pending'] },
    { id: 'ingestion', label: '文档入库与索引', phaseNames: ['ingestion', 'index'] },
    {
      id: 'retrieval',
      label: capabilities.hasDetailedDiagnostics ? '检索、排序与召回评测' : '检索与召回评测',
      phaseNames: ['retrieval', 'ranking', 'rerank', 'recall_evaluation']
    }
  ]
  if (capabilities.hasAnswer) {
    stages.push({
      id: 'answer_generation',
      label: '回答生成与评测',
      phaseNames: ['answer', 'answer_generation', 'answer_evaluation']
    })
  }
  stages.push({
    id: 'report',
    label: capabilities.hasDetailedDiagnostics ? '诊断汇总与报告' : '汇总与报告',
    phaseNames: ['report']
  })
  return stages
}
