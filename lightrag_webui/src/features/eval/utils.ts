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

export function formatDelta(value: unknown, baseline: unknown): string {
  if (typeof value !== 'number' || typeof baseline !== 'number') return '—'
  const delta = value - baseline
  if (delta === 0) return '0'
  const formatted = Number(Math.abs(delta)).toFixed(4).replace(/\.?0+$/, '')
  return `${delta > 0 ? '+' : '-'}${formatted}`
}

function escapeCsvCell(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

export function buildCompareCsv(
  runs: Array<{ id: string; label: string }>,
  rows: Array<{ key: string; label: string; values: Array<unknown> }>,
  baselineIndex: number | null
): string {
  const headers = [
    'metric',
    ...runs.map((run, index) =>
      index === baselineIndex ? `${run.label} (baseline)` : run.label
    )
  ]
  const lines = [headers.map(escapeCsvCell).join(',')]
  for (const row of rows) {
    lines.push(
      [row.key, ...row.values.map((value) => escapeCsvCell(formatMetricCell(value)))].join(',')
    )
  }
  return lines.join('\n') + '\n'
}

const CASE_FIELD_LABELS: Record<string, { zh: string; en: string }> = {
  recall_at_k: { zh: '召回@K', en: 'Recall@K' },
  reciprocal_rank: { zh: 'MRR 排名', en: 'MRR rank' },
  context_precision: { zh: '上下文精确率', en: 'Context precision' },
  object_hit_rate: { zh: '对象命中率', en: 'Object hit rate' },
  exact_match: { zh: '精确匹配', en: 'Exact match' },
  grounded: { zh: '有证据支撑', en: 'Grounded' },
  evidence_available: { zh: '证据可得', en: 'Evidence available' },
  abstention_correct: { zh: '拒答正确', en: 'Abstention correct' },
  hit_fact_ids: { zh: '命中证据', en: 'Hit facts' },
  expected_fact_ids: { zh: '期望证据', en: 'Expected facts' },
  top_contexts: { zh: '命中上下文', en: 'Top contexts' },
  hit_evidence: { zh: '命中证据原文', en: 'Hit evidence' },
  question_id: { zh: '题号', en: 'Question ID' },
  question_group: { zh: '分组', en: 'Group' },
  question_type: { zh: '题型', en: 'Type' },
  method: { zh: '方法', en: 'Method' }
}

export function caseFieldLabel(key: string, locale: string): string {
  const entry = CASE_FIELD_LABELS[key]
  if (!entry) return key
  return (locale === 'zh' || locale.startsWith('zh')) ? entry.zh : entry.en
}

export function buildCasesCsv(rows: Array<Record<string, unknown>>): string {
  const keys = new Set<string>([
    'question_id',
    'question',
    'answer',
    'expected',
    'group',
    'type',
    'method'
  ])
  for (const row of rows) {
    const detail = (row.detail ?? {}) as Record<string, unknown>
    for (const key of Object.keys(detail)) {
      if (!keys.has(key)) keys.add(key)
    }
  }
  const ordered = Array.from(keys)
  const lines = [ordered.map(escapeCsvCell).join(',')]
  for (const row of rows) {
    const detail = (row.detail ?? {}) as Record<string, unknown>
    lines.push(
      ordered
        .map((key) => {
          const value = detail[key] ?? row[key]
          return escapeCsvCell(formatMetricCell(value))
        })
        .join(',')
    )
  }
  return lines.join('\n') + '\n'
}

export function metricStats(
  values: Array<unknown>
): { n: number; sigma: number | null } {
  const numbers = values.filter((value): value is number => typeof value === 'number')
  if (numbers.length === 0) return { n: 0, sigma: null }
  const mean = numbers.reduce((sum, value) => sum + value, 0) / numbers.length
  let sigma: number | null = null
  if (numbers.length >= 2) {
    const variance =
      numbers.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
      (numbers.length - 1)
    sigma = Math.sqrt(variance)
  }
  return { n: numbers.length, sigma }
}

export function hasRunningJobs(
  jobs: Array<{ status?: string | null }>
): boolean {
  return jobs.some((job) => job.status === 'running')
}

export function diffParams(
  original: Record<string, unknown>,
  current: Record<string, unknown>
): string[] {
  const keys = new Set([...Object.keys(original), ...Object.keys(current)])
  const changed: string[] = []
  for (const key of keys) {
    const before = original[key]
    const after = current[key]
    if (before == null || after == null) continue
    if (String(before) !== String(after)) changed.push(key)
  }
  return changed.sort()
}

export function compareCompatible(
  runs: Array<{ kind?: string; dataset?: string | null }>
): boolean {
  if (runs.length < 2) return true
  const first = runs[0]
  return runs.every(
    (run) =>
      run.kind === first.kind &&
      (run.dataset ?? null) === (first.dataset ?? null)
  )
}

export function buildReproduceDraft(run: {
  launch_params?: Record<string, unknown> | null
  conditions: Array<{ key: string; value: string }>
  experiment?: string | null
  dataset?: string | null
}): { params: Record<string, unknown>; extraText: string; experiment: string; dataset: string } {
  if (run.launch_params && typeof run.launch_params === 'object') {
    const { extra, ...rest } = run.launch_params
    return {
      params: rest,
      extraText: Array.isArray(extra) ? (extra as unknown[]).map(String).join(',') : '',
      experiment: run.experiment ?? '',
      dataset: run.dataset ?? ''
    }
  }
  const params: Record<string, unknown> = {}
  const keys = [
    'model',
    'mode',
    'top_k',
    'chunk_top_k',
    'max_cases',
    'num_ctx',
    'num_predict',
    'temperature',
    'engine',
    'kg'
  ]
  for (const condition of run.conditions) {
    if (!keys.includes(condition.key)) continue
    params[condition.key] =
      condition.key === 'kg' ? condition.value === '开' : condition.value
  }
  return {
    params,
    extraText: '',
    experiment: run.experiment ?? '',
    dataset: run.dataset ?? ''
  }
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

export function statusLabel(run: {
  kind?: string
  status?: string | null
  failed_checks?: string[]
}): string {
  if (run.kind === 'offline' && run.status === 'failed') {
    return 'eval.statusFailed'
  }
  return run.status ?? ''
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
  'ungrounded_rate',
  'hallucinated_rate',
  'abstention_accuracy',
  'evidence_available',
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
