import { describe, expect, test } from 'bun:test'

import { metricRank, statusLabel } from '@/features/eval/utils'

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
})
