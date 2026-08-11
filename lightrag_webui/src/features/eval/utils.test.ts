import { describe, expect, test } from 'bun:test'

import {
  buildCompareCsv,
  buildCasesCsv,
  buildReproduceDraft,
  caseFieldLabel,
  compactRunStages,
  compareCompatible,
  evalStatusLabel,
  formatDelta,
  hasRunningJobs,
  metricStats,
  metricRank,
  questionTypeLabel
} from '@/features/eval/utils'

describe('eval utils', () => {
  test('maps evaluation statuses to translation keys and preserves unknown values', () => {
    expect(evalStatusLabel('complete')).toBe('eval.statusComplete')
    expect(evalStatusLabel('succeeded')).toBe('eval.statusComplete')
    expect(evalStatusLabel('failed')).toBe('eval.failed')
    expect(evalStatusLabel('unknown')).toBe('unknown')
    expect(evalStatusLabel(null)).toBe('')
  })

  test('canonical metric keys rank before unknown keys', () => {
    expect(metricRank('ungrounded_rate')).toBeLessThan(metricRank('unknown_metric'))
    expect(metricRank('evidence_available')).toBeLessThan(metricRank('unknown_metric'))
  })

  test('formatDelta renders signed numeric deltas only', () => {
    expect(formatDelta(0.9, 0.8)).toBe('+0.1')
    expect(formatDelta(0.7, 0.8)).toBe('-0.1')
    expect(formatDelta(0.8, 0.8)).toBe('0')
    expect(formatDelta('x', 0.8)).toBe('—')
    expect(formatDelta(null, null)).toBe('—')
  })

  test('buildCompareCsv marks the baseline column and escapes cells', () => {
    const runs = [
      { id: 'a', label: 'Run A' },
      { id: 'b', label: 'Run B' }
    ]
    const rows = [
      { key: 'mrr', label: 'MRR', values: [0.5, 0.9] },
      { key: 'note', label: 'Note', values: ['x, "quoted"', null] }
    ]
    const csv = buildCompareCsv(runs, rows, 0)
    expect(csv.split('\n')[0]).toBe('metric,Run A (baseline),Run B')
    expect(csv).toContain('"x, ""quoted"""')
    expect(csv).toContain('0.5,0.9')
    expect(csv.endsWith('\n')).toBe(true)
  })

  test('caseFieldLabel localizes known keys and falls back to the raw key', () => {
    expect(caseFieldLabel('hit_fact_ids', 'zh-CN')).toBe('命中证据')
    expect(caseFieldLabel('hit_fact_ids', 'en')).toBe('Hit facts')
    expect(caseFieldLabel('unknown_key', 'zh')).toBe('unknown_key')
  })

  test('questionTypeLabel renders formal reviewer-facing names', () => {
    expect(questionTypeLabel('direct_numeric')).toBe('数值事实题')
    expect(questionTypeLabel('multi_hop')).toBe('多跳推理题')
    expect(questionTypeLabel('unknown_type')).toBe('其他题型')
  })

  test('compactRunStages keeps one latest event per stage in first-seen order', () => {
    const stages = compactRunStages([
      { timestamp: '09:00', phase: 'answer', severity: 'info', message: '第 1 题' },
      { timestamp: '09:01', phase: 'retrieval', severity: 'info', message: '检索完成' },
      { timestamp: '09:02', phase: 'answer', severity: 'info', message: '第 2 题' }
    ])
    expect(stages.map((stage) => stage.phase)).toEqual(['answer', 'retrieval'])
    expect(stages[0]?.message).toBe('第 2 题')
  })

  test('buildCasesCsv includes detail fields without capping', () => {
    const rows = [
      {
        question_id: 'Q1',
        question: 'Q?',
        hit_fact_ids: 'FACT-1, FACT-2, FACT-3, FACT-4, FACT-5, FACT-6',
        detail: {
          hit_fact_ids: ['FACT-1', 'FACT-2', 'FACT-3', 'FACT-4', 'FACT-5', 'FACT-6'],
          hit_evidence: [{ fact_id: 'FACT-1', text: 'evidence text' }]
        }
      }
    ]
    const csv = buildCasesCsv(rows)
    expect(csv.split('\n')[0]).toContain('hit_fact_ids')
    expect(csv).toContain('FACT-1,FACT-2,FACT-3,FACT-4,FACT-5,FACT-6')
  })

  test('metricStats computes n and sample stdev', () => {
    expect(metricStats([])).toEqual({ n: 0, sigma: null })
    expect(metricStats(['x', 1])).toEqual({ n: 1, sigma: null })
    const stats = metricStats([0.8, 0.9, 0.85])
    expect(stats.n).toBe(3)
    expect(stats.sigma).toBeCloseTo(0.05, 5)
  })

  test('hasRunningJobs detects running jobs only', () => {
    expect(hasRunningJobs([{ status: 'running' }, { status: 'succeeded' }])).toBe(true)
    expect(hasRunningJobs([{ status: 'succeeded' }, { status: 'failed' }])).toBe(false)
    expect(hasRunningJobs([])).toBe(false)
  })

  test('compareCompatible requires the same dataset', () => {
    expect(
      compareCompatible([
        { dataset: 'd1' },
        { dataset: 'd1' }
      ])
    ).toBe(true)
    expect(
      compareCompatible([
        { dataset: 'd1' },
        { dataset: 'd1' }
      ])
    ).toBe(true)
    expect(compareCompatible([{ dataset: 'd1' }, { dataset: 'd2' }])).toBe(false)
    expect(compareCompatible([{ dataset: 'd1' }])).toBe(true)
  })

  test('buildReproduceDraft prefers launch_params and fills extraText', () => {
    const draft = buildReproduceDraft({
      launch_params: {
        model: 'gpt-4o-mini',
        top_k: 5,
        kg: true,
        extra: ['stage=eval', 'selected_limit=5']
      },
      conditions: [],
      dataset: 'rich-smoke-v1'
    })
    expect(draft.params).toEqual({ model: 'gpt-4o-mini', top_k: 5, kg: true })
    expect(draft.extraText).toBe('stage=eval,selected_limit=5')
  })

  test('buildReproduceDraft falls back to conditions with kg mapping', () => {
    const draft = buildReproduceDraft({
      launch_params: null,
      conditions: [
        { key: 'model', value: 'qwen3:8b' },
        { key: 'top_k', value: '5' },
        { key: 'kg', value: '开' }
      ],
      dataset: 'rich-smoke-v1'
    })
    expect(draft.params).toEqual({ model: 'qwen3:8b', top_k: '5', kg: true })
    expect(draft.extraText).toBe('')
  })

})
