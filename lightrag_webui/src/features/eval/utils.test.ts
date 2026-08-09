import { describe, expect, test } from 'bun:test'

import {
  buildCompareCsv,
  buildCasesCsv,
  caseFieldLabel,
  formatDelta,
  hasRunningJobs,
  metricStats,
  metricRank,
  statusLabel
} from '@/features/eval/utils'

describe('eval utils', () => {
  test('offline failed always renders as 未通过 even without failed_checks', () => {
    expect(statusLabel({ kind: 'offline', status: 'failed', failed_checks: [] })).toBe('eval.statusFailed')
    expect(statusLabel({ kind: 'offline', status: 'failed', failed_checks: ['词法检索'] })).toBe('eval.statusFailed')
  })

  test('other statuses pass through unchanged', () => {
    expect(statusLabel({ kind: 'offline', status: 'complete' })).toBe('complete')
    expect(statusLabel({ kind: 'online', status: 'failed' })).toBe('failed')
    expect(statusLabel({ kind: 'offline', status: null })).toBe('')
    expect(statusLabel({})).toBe('')
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
})
