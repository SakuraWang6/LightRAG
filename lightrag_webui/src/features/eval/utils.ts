import type { EvalRunKind, MetricItem } from '@/api/eval'

export function formatMetricValue(value: MetricItem['value']): string {
  if (typeof value === 'boolean') {
    return value ? '✓' : '✗'
  }
  if (typeof value === 'number') {
    const formatted = Number(value).toFixed(4).replace(/\.?0+$/, '')
    return formatted || '0'
  }
  return value == null ? '—' : String(value)
}

export function formatMetricCell(value: unknown): string {
  if (typeof value === 'boolean') {
    return value ? '✓' : '✗'
  }
  if (typeof value === 'number') {
    return Number(value).toFixed(4).replace(/\.?0+$/, '')
  }
  return value == null ? '—' : String(value)
}

export function runKindClass(kind: EvalRunKind): string {
  switch (kind) {
    case 'offline':
      return 'border-sky-300 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950 dark:text-sky-300'
    case 'online':
      return 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
    case 'experiment':
      return 'border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-700 dark:bg-violet-950 dark:text-violet-300'
    default:
      return 'border-zinc-300 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300'
  }
}

export function statusBadgeClass(status?: string | null): string {
  const value = (status || '').toLowerCase()
  if (value.includes('pass') || value.includes('complete')) {
    return 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
  }
  if (value.includes('fail') || value === 'failed') {
    return 'border-red-300 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-300'
  }
  if (value.includes('partial')) {
    return 'border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300'
  }
  return 'border-zinc-300 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300'
}

export function formatDate(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}

/** Metrics shown first in the comparison view; the rest follow alphabetically. */
export const COMPARE_METRIC_ORDER = [
  'passed',
  'answer_accuracy',
  'accuracy',
  'summary.answer_accuracy',
  'groundedness',
  'grounded_rate',
  'hallucination_rate',
  'hallucinated_rate',
  'abstention_accuracy',
  'citation_accuracy',
  'citation_presence',
  'citation_correctness',
  'citation_rate',
  'numeric_unit_accuracy',
  'formula_accuracy',
  'table_cell_accuracy',
  'average_recall',
  'evidence_recall_at_5',
  'retrieval_recall',
  'mrr',
  'context_precision',
  'object_hit_rate',
  'full_recall_cases',
  'candidate_recall',
  'selected_recall',
  'selection_precision',
  'role_coverage',
  'full_role_coverage_rate',
  'exact_match_rate',
  'mean_context_chars',
  'mean_selected_context_chars',
  'mean_candidate_context_chars',
  'changed_cases',
  'chunk_sidecar_coverage',
  'fact_evidence_hit_rate',
  'object_fact_evidence_hit_rate',
  'position_coverage',
  'meaningful_position_coverage',
  'page_or_bbox_position_coverage',
  'oracle_page_metadata_coverage',
  'cases',
  'pages',
  'facts',
  'questions',
  'objects',
  'relations'
]

export function metricRank(key: string): number {
  const index = COMPARE_METRIC_ORDER.indexOf(key)
  return index === -1 ? COMPARE_METRIC_ORDER.length : index
}

export function isMetricValue(value: unknown): value is number | boolean | string {
  return (
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    (typeof value === 'string' && value.length > 0)
  )
}
