import { useEffect, useRef } from 'react'

/**
 * Evaluation queue polling policy.
 *
 * The queue list must stay fresh even when no run/job is active: a job that
 * appears from another tab / client / backend would otherwise never show up
 * because polling stops entirely once the current page becomes idle.  While
 * something is active we poll fast (5s); while idle we still poll at a low
 * rate (15s) so late arrivals are picked up without a manual refresh.
 */
export const EVAL_POLL_ACTIVE_MS = 5000
export const EVAL_POLL_IDLE_MS = 15000

export function evalPollInterval(
  active: boolean,
  activeIntervalMs = EVAL_POLL_ACTIVE_MS,
  idleIntervalMs = EVAL_POLL_IDLE_MS
): number {
  return active ? activeIntervalMs : idleIntervalMs
}

interface UseEvalPollingOptions {
  /** True while any run/job is running or queued. */
  active: boolean
  /** Disable polling entirely (e.g. while another view owns the refresh). */
  enabled?: boolean
  activeIntervalMs?: number
  idleIntervalMs?: number
  /** Called on every tick. */
  onTick?: () => void
}

/**
 * Poll a load callback at a rate that depends on whether the queue is active.
 *
 * The callback is held in a ref so the interval is not torn down on every
 * render; only the active/enabled state and interval configuration recreate it.
 */
export function useEvalPolling({
  active,
  enabled = true,
  activeIntervalMs = EVAL_POLL_ACTIVE_MS,
  idleIntervalMs = EVAL_POLL_IDLE_MS,
  onTick,
}: UseEvalPollingOptions): void {
  const onTickRef = useRef(onTick)
  useEffect(() => {
    onTickRef.current = onTick
  })

  useEffect(() => {
    if (!enabled) return
    const intervalMs = evalPollInterval(active, activeIntervalMs, idleIntervalMs)
    const timer = window.setInterval(() => {
      onTickRef.current?.()
    }, intervalMs)
    return () => window.clearInterval(timer)
  }, [active, activeIntervalMs, idleIntervalMs, enabled])
}
