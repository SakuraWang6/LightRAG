import type { EvalRunEvent, MetricItem } from '@/api/eval'

const QUESTION_TYPE_LABELS: Record<string, string> = {
  abstain: '信息缺失拒答题',
  conflict_resolution: '冲突规则裁决题',
  direct_numeric: '数值事实题',
  direct_text: '文本事实题',
  equation: '公式内容题',
  equation_variable: '公式变量释义题',
  figure_caption: '图示信息题',
  figure_text: '图文关联题',
  formula: '公式题',
  formula_variable: '公式与变量综合题',
  multi_hop: '多跳推理题',
  negative_constraint: '负向约束题',
  table_cell: '表格单元格题',
  version_condition: '版本与生效条件题'
}

/** Translate stored question-type codes into reviewer-facing labels. */
export function questionTypeLabel(type: string | null | undefined): string {
  const normalized = type?.trim().toLowerCase()
  if (!normalized) return '未标注题型'
  return QUESTION_TYPE_LABELS[normalized] ?? '其他题型'
}

/**
 * Keep the stage timeline readable while preserving its chronological order.
 * A stage may emit per-case progress events; the latest event replaces the
 * previous one in that stage's original position instead of adding a new row.
 */
export function compactRunStages(events: EvalRunEvent[]): EvalRunEvent[] {
  const indexByPhase = new Map<string, number>()
  const stages: EvalRunEvent[] = []
  for (const event of events) {
    const phase = event.phase.trim() || '运行'
    const normalized = phase.toLowerCase()
    const displayEvent = phase === event.phase ? event : { ...event, phase }
    const existingIndex = indexByPhase.get(normalized)
    if (existingIndex === undefined) {
      indexByPhase.set(normalized, stages.length)
      stages.push(displayEvent)
    } else {
      stages[existingIndex] = displayEvent
    }
  }
  return stages
}

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

export function compareCompatible(
  runs: Array<{ dataset?: string | null }>
): boolean {
  if (runs.length < 2) return true
  const first = runs[0]
  return runs.every(
    (run) => (run.dataset ?? null) === (first.dataset ?? null)
  )
}

export function buildReproduceDraft(run: {
  launch_params?: Record<string, unknown> | null
  conditions: Array<{ key: string; value: string }>
  dataset?: string | null
}): { params: Record<string, unknown>; extraText: string; dataset: string } {
  if (run.launch_params && typeof run.launch_params === 'object') {
    const { extra, ...rest } = run.launch_params
    return {
      params: rest,
      extraText: Array.isArray(extra) ? (extra as unknown[]).map(String).join(',') : '',
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
    'max_total_tokens',
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
    dataset: run.dataset ?? ''
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

const EVAL_STATUS_LABELS: Record<string, string> = {
  complete: 'eval.statusComplete',
  succeeded: 'eval.statusComplete',
  failed: 'eval.failed',
  running: 'eval.running',
  queued: 'eval.statusQueued',
  pending: 'eval.statusPending',
  claiming: 'eval.statusClaiming',
  cancelling: 'eval.statusCancelling',
  cancelled: 'eval.statusCancelled',
  stale: 'eval.statusStale'
}

export function evalStatusLabel(status?: string | null): string {
  const value = status?.toLowerCase() ?? ''
  return EVAL_STATUS_LABELS[value] ?? status ?? ''
}

export function formatDate(iso?: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString()
}

/** Metrics shown first in the comparison view; the rest follow alphabetically. */
export const COMPARE_METRIC_ORDER = [
  'correct_cases',
  'retrieval_cases',
  'answer_accuracy',
  'groundedness',
  'ungrounded_rate',
  'abstention_accuracy',
  'evidence_available',
  'final_context_observable_rate',
  'final_context_evidence_coverage',
  'final_context_evidence_available',
  'citation_presence',
  'citation_correctness',
  'numeric_unit_accuracy',
  'formula_accuracy',
  'table_cell_accuracy',
  'average_recall',
  'mrr',
  'context_precision',
  'object_hit_rate',
  'full_recall_cases',
  'cases'
]

export function metricRank(key: string): number {
  const index = COMPARE_METRIC_ORDER.indexOf(key)
  return index === -1 ? COMPARE_METRIC_ORDER.length : index
}
