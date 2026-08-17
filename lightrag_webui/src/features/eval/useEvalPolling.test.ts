import { describe, expect, test } from 'bun:test'

import {
  EVAL_POLL_ACTIVE_MS,
  EVAL_POLL_IDLE_MS,
  evalPollInterval
} from '@/features/eval/useEvalPolling'

describe('evalPollInterval', () => {
  test('active uses the fast interval', () => {
    expect(evalPollInterval(true)).toBe(EVAL_POLL_ACTIVE_MS)
    expect(EVAL_POLL_ACTIVE_MS).toBe(5000)
  })

  test('idle keeps a low-frequency poll instead of stopping', () => {
    expect(evalPollInterval(false)).toBe(EVAL_POLL_IDLE_MS)
    expect(EVAL_POLL_IDLE_MS).toBe(15000)
  })

  test('custom intervals are honored', () => {
    expect(evalPollInterval(true, 100, 200)).toBe(100)
    expect(evalPollInterval(false, 100, 200)).toBe(200)
  })
})
