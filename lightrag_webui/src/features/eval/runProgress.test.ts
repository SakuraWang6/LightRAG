import { describe, expect, test } from 'bun:test'

import { getRunCapabilities } from '@/features/eval/runCapabilities'
import { getRunProgressPresentation } from '@/features/eval/runProgress'

const retrievalCapabilities = getRunCapabilities({
  evaluation_scope: 'retrieval_only',
  retrieval_diagnostics: 'detailed',
  answer_evaluation: { enabled: false },
  headline: {},
  status: 'running',
  progress: { status: 'running' }
})

describe('run progress presentation', () => {
  test('treats ingestion totals as source-document context, never whole-run completion', () => {
    const presentation = getRunProgressPresentation({
      status: 'running',
      phase: 'ingestion',
      done: 0,
      total: 2,
      message: '正在上传、解析并建立文档索引（文档状态: processing；已等待 5 分；已生成 9 个 chunk）'
    }, retrievalCapabilities)

    expect(presentation.stage?.label).toBe('文档入库与索引')
    expect(presentation.workSummary).toBe('2 个源文件处理中')
    expect(presentation.detail).toBe('文档状态：解析中；已等待 5 分；已生成 9 个 chunk')
    expect(presentation.meter).toEqual({
      mode: 'indeterminate',
      label: 'Chunk 解析与索引',
      valueLabel: '已生成 9 个 chunk',
      hint: '总 chunk 数会在解析完成后确定'
    })
    expect(presentation.stages.filter((stage) => stage.state === 'active')).toHaveLength(1)
  })

  test('uses question counts only for a question-evaluation phase', () => {
    const presentation = getRunProgressPresentation({
      status: 'running', phase: 'retrieval', done: 3, total: 17
    }, retrievalCapabilities)

    expect(presentation.workSummary).toBe('已评测 3/17 道检索题')
    expect(presentation.meter).toEqual({
      mode: 'determinate', label: '检索题评测', valueLabel: '3/17 题', percent: 18
    })
    expect(presentation.stages.map((stage) => stage.label)).toEqual([
      '运行环境准备', '文档入库与索引', '检索、排序与召回评测', '诊断汇总与报告'
    ])
  })
})
