import type { EvalRunProgress } from '@/api/eval'
import { getRunPipelineStages, type RunCapabilities, type RunPipelineStage } from '@/features/eval/runCapabilities'

export type RunProgressStageState = 'complete' | 'active' | 'upcoming'

export type RunProgressMeter = {
  mode: 'determinate' | 'indeterminate'
  label: string
  valueLabel: string
  percent?: number
  hint?: string
}

export type RunProgressPresentation = {
  isRunning: boolean
  isQueued: boolean
  stage: RunPipelineStage | null
  stageIndex: number
  stages: Array<RunPipelineStage & { state: RunProgressStageState }>
  detail: string | null
  workSummary: string | null
  meter: RunProgressMeter | null
}

function phaseName(progress: EvalRunProgress): string {
  return progress.phase?.trim().toLowerCase() ?? ''
}

function stageIndexForPhase(stages: RunPipelineStage[], rawPhase: string): number {
  const directIndex = stages.findIndex((stage) => stage.phaseNames.includes(rawPhase))
  if (directIndex >= 0) return directIndex
  // The runner emits `starting` before a concrete execution phase. Treat that
  // as environment preparation, but do not invent a percentage for it.
  return ['starting', 'queued', 'pending', 'dispatch'].includes(rawPhase) ? 0 : -1
}

function humanizeIngestionDetail(message: string): string {
  const enclosed = message.match(/[（(]([\s\S]+)[）)]\s*$/)?.[1]
  const concise = enclosed ?? message
  return concise
    .replace(/文档状态\s*:\s*/g, '文档状态：')
    .replace(/\bprocessing\b/gi, '解析中')
    .replace(/\bprocessed\b/gi, '已完成')
    .replace(/\bpending\b/gi, '等待中')
    .replace(/\bfailed\b/gi, '失败')
}

function stageWorkSummary(rawPhase: string, done: number, total: number): string | null {
  if (total <= 0) return null
  if (rawPhase === 'ingestion' || rawPhase === 'index') return `${total} 个源文件处理中`
  if (rawPhase === 'retrieval' || rawPhase === 'recall_evaluation') return `已评测 ${done}/${total} 道检索题`
  if (['answer', 'answer_generation', 'answer_evaluation'].includes(rawPhase)) {
    return `已完成 ${done}/${total} 道题的回答与评测`
  }
  return null
}

function chunkCountFromMessage(message: string | null): number | null {
  if (!message) return null
  const value = message.match(/已生成\s*(\d+)\s*个?\s*chunk/i)?.[1]
  if (!value) return null
  const count = Number(value)
  return Number.isFinite(count) ? count : null
}

function stageMeter(
  rawPhase: string,
  done: number,
  total: number,
  message: string | null
): RunProgressMeter | null {
  if (rawPhase === 'ingestion' || rawPhase === 'index') {
    const chunks = chunkCountFromMessage(message)
    return {
      mode: 'indeterminate',
      label: 'Chunk 解析与索引',
      valueLabel: chunks == null ? '正在等待首个 chunk' : `已生成 ${chunks} 个 chunk`,
      hint: '总 chunk 数会在解析完成后确定'
    }
  }
  if ((rawPhase === 'retrieval' || rawPhase === 'recall_evaluation') && total > 0) {
    return {
      mode: 'determinate',
      label: '检索题评测',
      valueLabel: `${done}/${total} 题`,
      percent: Math.min(100, Math.round((done / total) * 100))
    }
  }
  if (['answer', 'answer_generation', 'answer_evaluation'].includes(rawPhase) && total > 0) {
    return {
      mode: 'determinate',
      label: '回答与评分',
      valueLabel: `${done}/${total} 题`,
      percent: Math.min(100, Math.round((done / total) * 100))
    }
  }
  return null
}

/**
 * `done/total` is deliberately stage-local. A document count must never be
 * rendered as a percentage of the whole evaluation run: later phases use the
 * same fields for question counts. This presentation keeps those meanings
 * separate from the capability-defined execution route.
 */
export function getRunProgressPresentation(
  progress: EvalRunProgress,
  capabilities: RunCapabilities
): RunProgressPresentation {
  const status = progress.status?.toLowerCase() ?? ''
  const isQueued = ['queued', 'pending'].includes(status)
  const isRunning = isQueued || status === 'running'
  const rawPhase = phaseName(progress)
  const pipeline = getRunPipelineStages(capabilities)
  const matchedIndex = stageIndexForPhase(pipeline, rawPhase)
  const stageIndex = isRunning ? (matchedIndex >= 0 ? matchedIndex : 0) : -1
  const message = progress.message?.trim() || null
  const detail = rawPhase === 'ingestion' && message ? humanizeIngestionDetail(message) : message
  const done = Number(progress.done ?? 0)
  const total = Number(progress.total ?? 0)
  const normalizedDone = Number.isFinite(done) ? done : 0
  const normalizedTotal = Number.isFinite(total) ? total : 0

  return {
    isRunning,
    isQueued,
    stage: stageIndex >= 0 ? pipeline[stageIndex] : null,
    stageIndex,
    stages: pipeline.map((stage, index) => ({
      ...stage,
      state: index < stageIndex ? 'complete' : index === stageIndex ? 'active' : 'upcoming'
    })),
    detail,
    workSummary: stageWorkSummary(rawPhase, normalizedDone, normalizedTotal),
    meter: stageMeter(rawPhase, normalizedDone, normalizedTotal, message)
  }
}

export function getRunProgressLabel(progress: EvalRunProgress, capabilities: RunCapabilities): string {
  const presentation = getRunProgressPresentation(progress, capabilities)
  if (presentation.isQueued) return '等待调度'
  return presentation.stage?.label ?? '运行中'
}
