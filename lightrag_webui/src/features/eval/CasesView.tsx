import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CheckIcon,
  DownloadIcon,
  FileSearchIcon,
  MinusIcon,
  ScanSearchIcon,
  TextSearchIcon,
  TriangleAlertIcon,
  XIcon
} from 'lucide-react'

import Button from '@/components/ui/Button'
import { getCaseContext } from '@/api/eval'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger
} from '@/components/ui/Dialog'
import { ScrollArea } from '@/components/ui/ScrollArea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/Select'
import {
  buildCasesCsv,
  formatMetricValue,
  questionTypeLabel
} from '@/features/eval/utils'
import type { RunCapabilities } from '@/features/eval/runCapabilities'

type Detail = Record<string, unknown>

type NormalizedCase = {
  id: string
  question: string
  answer: string
  expected: string
  passed: boolean | null
  verdict: string
  type: string
  expectedBehavior: string
  responseTruncated: boolean
  retrieval: Detail
  finalContextEvidence: Detail
  evidenceFacts: Detail[]
  raw: Record<string, unknown>
}

function asRecord(value: unknown): Detail {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Detail : {}
}

function asList(value: unknown): Detail[] {
  return Array.isArray(value) ? value.filter((item): item is Detail => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : []
}

function detailValue(row: Record<string, unknown>, key: string): unknown {
  const detail = asRecord(row.detail)
  const nested = detail[key]
  // Flattened retrieval-only artifacts retain structured values under detail
  // while keeping a short string in the table cell. Prefer that real value.
  if (nested !== undefined && (typeof nested === 'object' || row[key] === undefined)) return nested
  return row[key]
}

function retrievalFromRow(row: Record<string, unknown>): Detail {
  const embedded = asRecord(detailValue(row, 'retrieval'))
  if (Object.keys(embedded).length > 0) return embedded
  const keys = [
    'recall_at_k',
    'reciprocal_rank',
    'context_precision',
    'first_evidence_rank',
    'expected_fact_ids',
    'hit_fact_ids',
    'hit_evidence',
    'top_contexts'
  ]
  const direct: Detail = {}
  for (const key of keys) {
    const value = detailValue(row, key)
    if (value !== undefined) direct[key] = value
  }
  return Object.keys(direct).length > 0 ? { status: 'observed', ...direct } : {}
}

function compactNumber(value: unknown): string {
  return typeof value === 'number' ? formatMetricValue(value) : '—'
}

function normalize(row: Record<string, unknown>): NormalizedCase {
  const question = String(row.question ?? row.question_id ?? '')
  const answer = String(row.answer ?? row.oracle_answer ?? '')
  const expected = String(row.expected ?? row.oracle_expected ?? '')
  const verdict = String(row.answer_verdict ?? '')
  let passed: boolean | null = null
  if (verdict === 'pass') passed = true
  else if (verdict === 'fail') passed = false
  else if (typeof row.exact_match === 'boolean') passed = row.exact_match
  else if (typeof row.passed === 'boolean') passed = row.passed
  return {
    id: String(row.question_id ?? question ?? ''),
    question,
    answer,
    expected,
    passed,
    verdict,
    type: String(row.question_type ?? ''),
    expectedBehavior: String(row.expected_behavior ?? 'answer'),
    responseTruncated: row.response_truncated === true,
    retrieval: retrievalFromRow(row),
    finalContextEvidence: asRecord(detailValue(row, 'final_context_evidence')),
    evidenceFacts: asList(detailValue(row, 'evidence_facts')),
    raw: row
  }
}

function retrievalPassed(c: NormalizedCase): boolean | null {
  if (c.retrieval.status !== 'observed') return null
  return typeof c.retrieval.recall_at_k === 'number' ? c.retrieval.recall_at_k === 1 : null
}

function CaseOutcome({ c, capabilities }: { c: NormalizedCase; capabilities: RunCapabilities }) {
  const retrievalResult = retrievalPassed(c)
  const passed = capabilities.hasAnswer ? c.passed : retrievalResult
  const uncertain = c.verdict === 'uncertain'
  const className = passed === true
    ? 'text-emerald-600 dark:text-emerald-400'
    : passed === false
      ? 'text-red-600 dark:text-red-400'
      : 'text-muted-foreground'
  return (
    <span className={`ml-auto inline-flex items-center gap-1 text-sm font-medium ${className}`}>
      {passed === true ? <CheckIcon className="size-4" /> : passed === false ? <XIcon className="size-4" /> : <MinusIcon className="size-4" />}
      {capabilities.hasAnswer
        ? uncertain ? '需复核' : passed === true ? '通过' : passed === false ? '未通过' : '未判定'
        : passed === true ? '完整召回' : passed === false ? '未完整召回' : '未判定'}
    </span>
  )
}

const FACT_HIGHLIGHT_CLASSES = [
  'bg-amber-200/80 text-amber-950 dark:bg-amber-400/30 dark:text-amber-100',
  'bg-sky-200/80 text-sky-950 dark:bg-sky-400/30 dark:text-sky-100',
  'bg-emerald-200/80 text-emerald-950 dark:bg-emerald-400/30 dark:text-emerald-100',
  'bg-fuchsia-200/80 text-fuchsia-950 dark:bg-fuchsia-400/30 dark:text-fuchsia-100',
  'bg-rose-200/80 text-rose-950 dark:bg-rose-400/30 dark:text-rose-100'
]

const FACT_TYPE_LABELS: Record<string, string> = {
  direct_numeric: '参数值',
  table_cell: '表格单元',
  equation: '公式',
  figure_caption: '图示',
  figure_text: '图示',
  delivery_milestone: '交付里程碑',
  release_constraint: '发布约束',
  governance_owner: '治理责任',
  version_condition: '版本条件',
  negative_constraint: '负向约束',
  conflict_resolution: '冲突消解',
  text: '文本',
  caption: '图注'
}

function factColorIndex(factId: string): number {
  let hash = 0
  for (let i = 0; i < factId.length; i += 1) hash = (hash * 31 + factId.charCodeAt(i)) >>> 0
  return hash % FACT_HIGHLIGHT_CLASSES.length
}

type MatchRange = { start: number; end: number; factId: string }

function matchRanges(matches: Detail[]): MatchRange[] {
  const ranges = matches
    .map((match) => ({
      start: typeof match.start === 'number' ? match.start : -1,
      end: typeof match.end === 'number' ? match.end : -1,
      factId: String(match.fact_id ?? '')
    }))
    .filter((range) => range.start >= 0 && range.end > range.start)
    .sort((a, b) => a.start - b.start)
  const merged: MatchRange[] = []
  for (const range of ranges) {
    const last = merged[merged.length - 1]
    if (last && range.start <= last.end) {
      if (range.end > last.end) last.end = range.end
    } else {
      merged.push({ ...range })
    }
  }
  return merged
}

function HighlightedChunk({ text, ranges }: { text: string; ranges: MatchRange[] }) {
  const segments: { text: string; range?: MatchRange }[] = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start > cursor) segments.push({ text: text.slice(cursor, range.start) })
    segments.push({ text: text.slice(range.start, range.end), range })
    cursor = range.end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) })
  return (
    <p className="whitespace-pre-wrap break-words text-sm leading-6">
      {segments.map((segment, index) =>
        segment.range ? (
          <mark
            key={index}
            className={`rounded px-0.5 ${FACT_HIGHLIGHT_CLASSES[factColorIndex(segment.range.factId)]}`}
            title={segment.range.factId}
          >
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        )
      )}
    </p>
  )
}

function ChunkDialog({ item }: { item: Detail }) {
  const text = typeof item.text === 'string' ? item.text : ''
  const ranges = matchRanges(asList(item.matches))
  const facts = Array.from(new Set(ranges.map((range) => range.factId).filter(Boolean)))
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs">
          <FileSearchIcon className="mr-1 size-3.5" />
          查看完整 chunk{typeof item.chars === 'number' ? `（${item.chars} 字符）` : ''}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 text-sm">
            第 {String(item.rank ?? '—')} 条检索 chunk
            {item.file_path ? <span className="text-muted-foreground font-normal">{String(item.file_path)}</span> : null}
          </DialogTitle>
          {facts.length > 0 ? (
            <DialogDescription asChild>
              <div className="flex flex-wrap gap-2 pt-1">
                {facts.map((factId) => (
                  <span
                    key={factId}
                    className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] ${FACT_HIGHLIGHT_CLASSES[factColorIndex(factId)]}`}
                  >
                    <span className="size-1.5 rounded-full bg-current" />
                    {factId}
                  </span>
                ))}
              </div>
            </DialogDescription>
          ) : (
            <DialogDescription className="text-xs">完整检索 chunk 内容。</DialogDescription>
          )}
        </DialogHeader>
        <ScrollArea className="max-h-[70vh] rounded-md border bg-muted/10 p-3">
          {ranges.length > 0 ? (
            <HighlightedChunk text={text} ranges={ranges} />
          ) : (
            <p className="whitespace-pre-wrap break-words text-sm leading-6">{text || '—'}</p>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

function FinalContextDialog({ runId, c }: { runId: string; c: NormalizedCase }) {
  const [tab, setTab] = useState<'context' | 'prompt'>('context')
  const [open, setOpen] = useState(false)
  const [trace, setTrace] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getCaseContext(runId, c.id)
      .then((data) => {
        if (cancelled) return
        setTrace(data)
        setError(data ? null : '该运行没有记录此题的最终上下文。')
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '加载最终上下文失败。')
      })
    return () => {
      cancelled = true
    }
  }, [open, runId, c.id])
  const finalContext = typeof trace?.final_context === 'string' ? trace.final_context : ''
  const finalPrompt = typeof trace?.final_prompt === 'string' ? trace.final_prompt : ''
  const chars = typeof trace?.final_context_chars === 'number'
    ? trace.final_context_chars
    : finalContext ? finalContext.length : 0
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs">
          <TextSearchIcon className="mr-1 size-3.5" />
          查看检索上下文
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="text-sm">检索上下文（进入 LLM 前）{typeof chars === 'number' ? `（${chars} 字符）` : ''}</DialogTitle>
          <DialogDescription className="text-xs">
            这是检索召回 chunk 与知识图谱实体拼装后的上下文，随问题一起发送给 LLM；「完整 Prompt」包含系统提示词与用户问题。
          </DialogDescription>
        </DialogHeader>
        {error ? (
          <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
        ) : !trace ? (
          <p className="text-muted-foreground text-sm">正在加载…</p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Button size="sm" variant={tab === 'context' ? 'default' : 'outline'} onClick={() => setTab('context')}>
                检索上下文（KG + Chunks）
              </Button>
              {finalPrompt ? (
                <Button size="sm" variant={tab === 'prompt' ? 'default' : 'outline'} onClick={() => setTab('prompt')}>
                  完整 Prompt
                </Button>
              ) : null}
            </div>
            <ScrollArea className="max-h-[70vh] rounded-md border bg-muted/10 p-3">
              <pre className="font-sans text-xs leading-5 whitespace-pre-wrap break-words">
                {tab === 'context' ? (finalContext || '—') : finalPrompt}
              </pre>
            </ScrollArea>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

type TopKChunk = {
  rank: number
  filePath: string
  text: string
  hit: Detail | null
}

function TopKDialog({ retrieval }: { retrieval: Detail }) {
  const references = asList(retrieval.top_contexts)
  if (references.length === 0) return null
  const hitByText = new Map<string, Detail>()
  for (const item of asList(retrieval.hit_evidence)) {
    if (typeof item.text === 'string' && item.text.length > 0) {
      hitByText.set(item.text, item)
    }
  }
  let globalRank = 0
  const chunks: TopKChunk[] = []
  for (const ref of references) {
    const refChunks = asList(ref.chunks)
    for (const chunk of refChunks) {
      globalRank += 1
      const text = typeof chunk.text === 'string' ? chunk.text : ''
      chunks.push({
        rank: globalRank,
        filePath: typeof ref.file_path === 'string' ? ref.file_path : '',
        text,
        hit: hitByText.get(text) ?? null
      })
    }
    if (refChunks.length === 0 && typeof ref.chunk_count === 'number' && ref.chunk_count > 0) {
      // Older runs recorded only counts; keep the reference row visible.
      globalRank += ref.chunk_count
    }
  }
  const totalCandidates = references.reduce((sum, ref) => {
    const refChunks = asList(ref.chunks)
    return sum + (refChunks.length > 0 ? refChunks.length : (typeof ref.chunk_count === 'number' ? ref.chunk_count : 0))
  }, 0)
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs">
          <FileSearchIcon className="mr-1 size-3.5" />
          查看 Top-K 检索结果（{totalCandidates}）
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="text-sm">Top-K 检索结果（{totalCandidates} 个候选 chunk）</DialogTitle>
          <DialogDescription className="text-xs">
            按检索排名展示全部候选 chunk；命中的 chunk 以彩色高亮标出目标事实所在位置。
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[70vh] rounded-md border">
          <div className="space-y-3 p-3">
            {chunks.length === 0 ? (
              <p className="text-muted-foreground text-sm">该运行未保存 Top-K chunk 内容，仅记录了候选数量。</p>
            ) : null}
            {chunks.map((chunk) => (
              <details key={`${chunk.rank}-${chunk.filePath}`} className="rounded-md border bg-muted/[0.06] px-3 py-2.5 group">
                <summary className="text-muted-foreground flex cursor-pointer items-center gap-2 text-xs select-none">
                  <span className="font-mono">第 {chunk.rank} 条</span>
                  {chunk.filePath ? <span className="truncate">{chunk.filePath}</span> : null}
                  {chunk.hit ? (
                    <span className="ml-auto inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                      <CheckIcon className="size-3.5" /> 已命中
                    </span>
                  ) : null}
                </summary>
                <div className="mt-2">
                  {chunk.hit ? (
                    <HighlightedChunk text={chunk.text} ranges={matchRanges(asList(chunk.hit.matches))} />
                  ) : (
                    <p className="whitespace-pre-wrap break-words text-sm leading-6">{chunk.text || '—'}</p>
                  )}
                </div>
              </details>
            ))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

function RetrievalEvidence({
  runId,
  c,
  capabilities
}: {
  runId: string
  c: NormalizedCase
  capabilities: RunCapabilities
}) {
  const retrieval = c.retrieval
  const status = retrieval.status
  if (status === 'not_applicable') {
    return <p className="text-muted-foreground text-sm">本题要求拒答，不进行检索召回评分。</p>
  }
  if (status !== 'observed') {
    return (
      <p className="text-muted-foreground text-sm leading-6">
        本题没有可用的检索 trace，无法判断检索阶段是否命中目标证据。
      </p>
    )
  }
  const expectedIds = Array.isArray(retrieval.expected_fact_ids) ? retrieval.expected_fact_ids.map(String) : []
  const hitIds = Array.isArray(retrieval.hit_fact_ids) ? retrieval.hit_fact_ids.map(String) : []
  const evidence = asList(retrieval.hit_evidence)
  const missingInContext = Array.isArray(c.finalContextEvidence.missing_fact_ids)
    ? c.finalContextEvidence.missing_fact_ids.map(String)
    : []
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground text-xs">目标事实命中</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{hitIds.length} / {expectedIds.length}</p>
        </div>
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground text-xs">证据召回@K</p>
          <p className="mt-1 text-sm font-semibold tabular-nums">{compactNumber(retrieval.recall_at_k)}</p>
        </div>
        {capabilities.hasGoldRank ? (
          <div className="rounded-md border bg-background px-3 py-2">
            <p className="text-muted-foreground text-xs">Gold Rank</p>
            <p className="mt-1 text-sm font-semibold tabular-nums">{retrieval.first_evidence_rank ? `第 ${retrieval.first_evidence_rank} 条` : '未命中'}</p>
          </div>
        ) : null}
      </div>

      {expectedIds.length > 0 ? (
        <div className="rounded-md border bg-background px-3 py-2">
          <p className="text-muted-foreground mb-2 text-xs font-medium">
            目标证据清单（命中 {hitIds.length}/{expectedIds.length}）
          </p>
          <ul className="space-y-1">
            {expectedIds.map((factId) => {
              const hit = hitIds.includes(factId)
              const fact = c.evidenceFacts.find((item) => String(item.fact_id) === factId)
              const typeLabel = FACT_TYPE_LABELS[String(fact?.fact_type ?? '')] ?? ''
              const answer = typeof fact?.answer === 'string' ? fact.answer : ''
              const page = typeof fact?.page === 'number' ? `第 ${fact.page} 页` : ''
              const detailText = [answer, page].filter(Boolean).join(' · ')
              return (
                <li key={factId} className="flex items-start gap-2 text-xs leading-5">
                  <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${hit ? 'bg-emerald-500' : 'bg-red-500'}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono">{factId}</span>
                      {typeLabel ? <span className="text-muted-foreground">{typeLabel}</span> : null}
                      <span className={`ml-auto shrink-0 ${hit ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                        {hit ? '已命中' : '未命中'}
                      </span>
                    </div>
                    {detailText ? (
                      <p className="text-muted-foreground mt-0.5 truncate" title={detailText}>{detailText}</p>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}

      {evidence.length === 0 ? (
        <p className="text-amber-700 dark:text-amber-300 flex items-center gap-1.5 text-sm">
          <TriangleAlertIcon className="size-4" /> 前 K 条检索结果中没有命中目标事实。
        </p>
      ) : (
        <div className="space-y-2">
          <p className="text-muted-foreground text-[11px] font-semibold tracking-[0.14em]">命中证据片段</p>
          {evidence.map((item, index) => (
            <blockquote key={`${String(item.fact_id ?? '')}-${index}`} className="border-primary/25 bg-primary/[0.025] rounded-md border-l-2 px-3 py-2.5">
              <p className="text-muted-foreground mb-1 text-xs">
                第 {String(item.rank ?? '—')} 条
                {item.file_path ? ` · ${String(item.file_path)}` : ''}
                {item.kind ? ` · ${String(item.kind)}` : ''}
              </p>
              {asList(item.matches).length > 0 ? (
                <div className="space-y-3">
                  {asList(item.matches).map((match, matchIndex) => (
                    <div key={`${String(match.fact_id ?? '')}-${matchIndex}`}>
                      <p className="text-muted-foreground mb-1 text-xs">
                        {String(match.fact_id ?? '目标事实')} · {String(match.match_type ?? 'evidence')}
                        {typeof match.start === 'number' && typeof item.chars === 'number'
                          ? ` · 位置 ${match.start + 1}/${item.chars}`
                          : ''}
                      </p>
                      <p className="whitespace-pre-wrap break-words text-sm leading-6">{String(match.excerpt ?? item.text ?? '—')}</p>
                    </div>
                  ))}
                  <ChunkDialog item={item} />
                </div>
              ) : (
                <>
                  <p className="text-muted-foreground mb-1 text-xs">{String(item.fact_id ?? '目标事实')}</p>
                  <p className="whitespace-pre-wrap break-words text-sm leading-6">{String(item.text ?? '—')}</p>
                  <ChunkDialog item={item} />
                </>
              )}
            </blockquote>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t pt-2">
        {capabilities.hasCandidateRanking ? <TopKDialog retrieval={retrieval} /> : null}
        {capabilities.hasAnswerDetails ? <FinalContextDialog runId={runId} c={c} /> : null}
        {capabilities.hasAnswerDetails && missingInContext.length > 0 ? (
          <p className="text-amber-700 dark:text-amber-300 text-xs">
            有 {missingInContext.length} 条目标事实未进入最终上下文：{missingInContext.join('、')}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function diagnosticHint(c: NormalizedCase): string {
  if (c.verdict === 'uncertain') {
    return '回答表达需要语义评分器复核；当前不把它归因为检索或生成问题。'
  }
  if (c.expectedBehavior === 'abstain') {
    return c.passed ? '模型按要求拒答。' : '该题应该拒答，但模型给出了不可支持的回答。'
  }
  if (c.passed) return '回答通过；检索与上下文数据保留作追溯，不再额外归因。'
  const recall = c.retrieval.recall_at_k
  const context = c.finalContextEvidence
  const missing = Array.isArray(context.missing_fact_ids) ? context.missing_fact_ids : []
  if (typeof recall === 'number' && recall < 1) {
    return '检索未取全本题所需证据，应优先检查索引、检索参数或文档解析结果。'
  }
  if (context.status === 'observed' && missing.length > 0) {
    return `证据已检索到，但 ${missing.length} 条目标事实未进入最终上下文；请检查上下文选择或截断。`
  }
  if (context.status !== 'observed') {
    return '检索已取全证据，但最终上下文未记录，无法进一步归因；请检查上下文追踪。'
  }
  return '所需证据已全部进入最终上下文，但回答仍未通过；请检查生成模型、提示词或评分规则。'
}

function retrievalDiagnosticHint(c: NormalizedCase): string {
  const retrieval = c.retrieval
  if (retrieval.status === 'not_applicable') return '本题不需要检索召回评分。'
  if (retrieval.status !== 'observed') return '没有可用的检索 trace，无法判断召回表现。'
  if (typeof retrieval.recall_at_k === 'number' && retrieval.recall_at_k < 1) {
    const rank = typeof retrieval.first_evidence_rank === 'number'
      ? `首条命中在第 ${retrieval.first_evidence_rank} 位。`
      : 'Top-K 中没有完整命中目标证据。'
    return `召回未覆盖本题所需证据；${rank}`
  }
  if (typeof retrieval.first_evidence_rank === 'number' && retrieval.first_evidence_rank > 1) {
    return `目标证据已召回，但 Gold Rank 为第 ${retrieval.first_evidence_rank} 位，可继续优化排序。`
  }
  return '目标证据已完整召回，且排序表现正常。'
}

interface CasesViewProps {
  rows: Record<string, unknown>[]
  runId: string
  capabilities: RunCapabilities
}

export default function CasesView({ rows, runId, capabilities }: CasesViewProps) {
  const { t } = useTranslation()
  const [filter, setFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')

  const cases = useMemo(() => rows.map(normalize), [rows])
  const types = useMemo(() => Array.from(new Set(cases.map((c) => c.type).filter(Boolean))).sort(), [cases])
  const filtered = useMemo(
    () => cases.filter((c) => {
      const passed = capabilities.hasAnswer ? c.passed : retrievalPassed(c)
      if (filter === 'pass' && passed !== true) return false
      if (filter === 'fail' && passed !== false) return false
      if (typeFilter !== 'all' && c.type !== typeFilter) return false
      return true
    }),
    [capabilities.hasAnswer, cases, filter, typeFilter]
  )

  const exportCsv = () => {
    const csv = buildCasesCsv(filtered.map((c) => c.raw))
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'eval-cases.csv'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="w-44">
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('eval.caseAll')}</SelectItem>
              <SelectItem value="pass">{t('eval.casePass')}</SelectItem>
              <SelectItem value="fail">{t('eval.caseFail')}</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-44">
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="h-8"><SelectValue placeholder={t('eval.caseType')} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('eval.caseAllGroups')}</SelectItem>
              {types.map((type) => (
                <SelectItem key={type} value={type}>{questionTypeLabel(type)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="text-muted-foreground self-center text-xs">{filtered.length}/{cases.length}</span>
        <Button size="sm" variant="outline" className="ml-auto" onClick={exportCsv}>
          <DownloadIcon className="mr-1 size-4" />
          {t('eval.exportCsv')}
        </Button>
      </div>

      {filtered.length === 0 ? (
        <div className="text-muted-foreground rounded-lg border border-dashed py-12 text-center text-sm">{t('eval.noCases')}</div>
      ) : (
        <div className="space-y-4">
          {filtered.map((c, index) => (
            <article key={`${c.id}-${index}`} className="relative overflow-hidden rounded-lg border bg-card shadow-sm">
              <div className={`absolute inset-y-0 left-0 w-1 ${(capabilities.hasAnswer ? c.passed : retrievalPassed(c)) === true ? 'bg-emerald-500' : (capabilities.hasAnswer ? c.passed : retrievalPassed(c)) === false ? 'bg-red-500' : 'bg-muted-foreground/40'}`} />
              <header className="flex flex-wrap items-center gap-2 border-b bg-muted/25 px-5 py-3 pl-6">
                <span className="font-serif text-lg font-semibold">第 {index + 1} 题</span>
                {c.type ? <span className="rounded-full border bg-background px-2 py-0.5 text-xs text-muted-foreground">{questionTypeLabel(c.type)}</span> : null}
                <CaseOutcome c={c} capabilities={capabilities} />
              </header>
              <div className="space-y-5 px-5 py-5 pl-6">
                <section>
                  <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">{t('eval.caseQuestion')}</p>
                  <p className="font-serif whitespace-pre-wrap break-words text-base leading-7">{c.question || c.id || '—'}</p>
                </section>
                {capabilities.hasAnswerDetails ? (
                  <div className="grid gap-3 lg:grid-cols-2">
                    <section className="rounded-md border bg-muted/25 p-4">
                      <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">{t('eval.caseAnswer')}</p>
                      <p className="whitespace-pre-wrap break-words text-sm leading-6">{c.answer || '—'}</p>
                    </section>
                    <section className="rounded-md border border-primary/20 bg-primary/[0.03] p-4">
                      <p className="text-muted-foreground mb-2 text-[11px] font-semibold tracking-[0.14em]">{t('eval.caseExpected')}</p>
                      <p className="whitespace-pre-wrap break-words text-sm leading-6">{c.expected || '—'}</p>
                    </section>
                  </div>
                ) : null}
                {capabilities.hasAnswerDetails && c.responseTruncated ? (
                  <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                    <TriangleAlertIcon className="size-3.5" /> 模型达到最大输出限制；此回答可能不完整。
                  </p>
                ) : null}
                <section className="rounded-md border bg-muted/[0.08] p-4">
                  <div className="mb-3 flex items-center gap-2">
                    <ScanSearchIcon className="text-primary size-4" />
                    <h3 className="text-sm font-semibold">检索结果</h3>
                  </div>
                  <RetrievalEvidence runId={runId} c={c} capabilities={capabilities} />
                </section>
                <section className="border-border/70 flex gap-2 rounded-md border border-dashed px-3 py-2.5 text-sm leading-6">
                  <FileSearchIcon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                  <div><span className="font-medium">{capabilities.hasAnswer ? t('eval.caseResult') : '检索结论'}：</span>{capabilities.hasAnswer ? diagnosticHint(c) : retrievalDiagnosticHint(c)}</div>
                </section>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
