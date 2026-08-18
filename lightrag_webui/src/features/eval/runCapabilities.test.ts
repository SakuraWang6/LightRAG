import { describe, expect, test } from 'bun:test'

import { getRunCapabilities, getRunListSummary, getRunPipelineStages, listSummaryMetricKeys } from '@/features/eval/runCapabilities'

describe('run capabilities', () => {
  test('retrieval-only removes answer capabilities and keeps detailed ranking data', () => {
    const capabilities = getRunCapabilities({
      evaluation_scope: 'retrieval_only',
      retrieval_diagnostics: 'detailed',
      retrieval_evaluation: { enabled: true },
      answer_evaluation: { enabled: false },
      headline: {
        recall_at_1: { key: 'recall_at_1', label: 'Recall@1', value: 0.5 }
      },
      status: 'complete',
      progress: {},
      artifacts: [{
        rel_path: 'cases', kind: 'cases', title: 'cases', metrics: [], meta: {},
        table: { columns: [], rows: [{ question_id: 'Q-1', recall_at_k: 1, first_evidence_rank: 2, detail: { top_contexts: [{ chunks: [] }] } }] }
      }]
    })

    expect(capabilities.hasRetrieval).toBe(true)
    expect(capabilities.hasAnswer).toBe(false)
    expect(capabilities.hasAnswerMetrics).toBe(false)
    expect(capabilities.hasAnswerDetails).toBe(false)
    expect(capabilities.hasRetrievalCases).toBe(true)
    expect(capabilities.hasCandidateRanking).toBe(true)
    expect(capabilities.hasGoldRank).toBe(true)
    expect(listSummaryMetricKeys(capabilities)).toEqual(['recall_at_1', 'recall_at_5', 'mrr', 'failed_retrieval_cases'])
    expect(getRunPipelineStages(capabilities).map((stage) => stage.id)).not.toContain('answer_generation')
  })

  test('end-to-end exposes both domains and retains answer pipeline stages', () => {
    const capabilities = getRunCapabilities({
      evaluation_scope: 'end_to_end',
      retrieval_diagnostics: 'summary',
      headline: {
        answer_accuracy: { key: 'answer_accuracy', label: '回答准确率', value: 0.8 },
        recall_at_5: { key: 'recall_at_5', label: 'Recall@5', value: 0.9 }
      },
      status: 'complete',
      progress: {},
      artifacts: [{
        rel_path: 'cases', kind: 'cases', title: 'cases', metrics: [], meta: {},
        table: { columns: [], rows: [{ question_id: 'Q-1', answer: 'A', exact_match: true, retrieval: { status: 'observed', recall_at_k: 1 } }] }
      }]
    })

    expect(capabilities.hasAnswer).toBe(true)
    expect(capabilities.hasRetrieval).toBe(true)
    expect(capabilities.hasAnswerMetrics).toBe(true)
    expect(capabilities.hasRetrievalMetrics).toBe(true)
    expect(capabilities.hasDetailedDiagnostics).toBe(false)
    expect(getRunPipelineStages(capabilities).map((stage) => stage.id)).toContain('answer_generation')
  })

  test('legacy metadata infers a retrieval run from an explicit disabled answer stage', () => {
    const capabilities = getRunCapabilities({
      answer_evaluation: { enabled: false },
      retrieval_evaluation: { enabled: true },
      headline: {},
      status: 'complete',
      progress: {}
    })

    expect(capabilities.isLegacy).toBe(true)
    expect(capabilities.scope).toBe('retrieval_only')
  })

  test('list summary derives the scope-appropriate failure count', () => {
    const retrieval = getRunCapabilities({
      evaluation_scope: 'retrieval_only', headline: {}, status: 'complete', progress: {}
    })
    const answer = getRunCapabilities({
      evaluation_scope: 'end_to_end', headline: {}, status: 'complete', progress: {}
    })
    const metric = (key: string, value: number) => ({ key, label: key, value })
    expect(getRunListSummary(retrieval, {
      recall_at_1: metric('recall_at_1', 0.5),
      retrieval_cases: metric('retrieval_cases', 10),
      full_recall_cases: metric('full_recall_cases', 7)
    }).find((item) => item.key === 'failed_retrieval_cases')?.value).toBe(3)
    expect(getRunListSummary(answer, {
      answer_accuracy: metric('answer_accuracy', 0.8),
      cases: metric('cases', 10),
      correct_cases: metric('correct_cases', 8)
    }).find((item) => item.key === 'failed_cases')?.value).toBe(2)
  })
})
