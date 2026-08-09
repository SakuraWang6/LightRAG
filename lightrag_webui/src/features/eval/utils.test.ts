import { describe, expect, test } from 'bun:test'

import {
  buildCompareCsv,
  formatDelta,
  metricRank,
  statusLabel
} from '@/features/eval/utils'

describe('eval utils', () => {
  test('offline failed always renders as 未通过 even without failed_checks', () => {
    expect(statusLabel({ kind: 'offline', status: 'failed', failed_checks: [] })).toBe('未通过')
    expect(statusLabel({ kind: 'offline', status: 'failed', failed_checks: ['词法检索'] })).toBe('未通过')
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
})
