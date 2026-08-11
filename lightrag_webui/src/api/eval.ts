import axios, { AxiosError } from 'axios'
import { backendBaseUrl } from '@/lib/constants'
import { errorMessage } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settings'
import { navigationService } from '@/services/navigation'

export type MetricItem = {
  key: string
  label: string
  value: number | boolean | string | null
  type?: 'number' | 'bool' | 'text'
}

export type TableColumn = {
  key: string
  label: string
}

export type TableData = {
  columns: TableColumn[]
  rows: Record<string, unknown>[]
}

export type TocEntry = {
  level: number
  title: string
}

export type RunCondition = {
  key: string
  label: string
  value: string
}

export type EvalRunProgress = {
  status?: string | null
  phase?: string | null
  done?: number | null
  total?: number | null
  message?: string | null
  updated_at?: string | null
}

export type EvalRunEvent = {
  timestamp: string
  phase: string
  severity: string
  message: string
  error_type?: string
}

export type EvalRunFailure = {
  phase: string
  error_type: string
  summary: string
  retryable: boolean | null
  recommendation: string
  log_offset: number
}

export type EvalArtifact = {
  rel_path: string
  kind: string
  title: string
  updated_at?: string | null
  metrics: MetricItem[]
  table: TableData
  meta: Record<string, unknown>
  report_md?: string | null
  toc?: TocEntry[]
  error?: string | null
}

export type EvalRun = {
  id: string
  run_dir?: string
  restarts?: number
  last_restart_resume?: boolean | null
  label: string
  launch_params?: Record<string, unknown> | null
  dataset?: string | null
  dataset_display_name?: string | null
  updated_at?: string | null
  started_at?: string | null
  duration_seconds?: number | null
  status?: string | null
  conditions: RunCondition[]
  progress: EvalRunProgress
  headline: Record<string, MetricItem>
  artifact_titles: string[]
  failure?: EvalRunFailure | null
}

export type EvalRunDetail = EvalRun & {
  artifacts: EvalArtifact[]
  events?: EvalRunEvent[]
}

export type EvalJob = {
  id: string
  kind: 'run' | 'dataset'
  evaluation?: string | null
  dataset?: string | null
  dataset_id?: string | null
  display_name?: string | null
  output_dir: string
  status: string
  queue_position?: number | null
  started_at?: string | null
  log?: string[]
}

export type DatasetSummary = {
  dataset_id: string
  display_name: string
  title: string
  tier: string
  profile: string
  language: 'en' | 'zh'
  pages: number
  formats: ('docx' | 'pdf')[]
  modalities: ('text' | 'tables' | 'figures' | 'equations')[]
  path: string
  created_at: string
  files: string[]
}

const evalApiClient = axios.create({
  baseURL: backendBaseUrl,
  headers: { 'Content-Type': 'application/json' }
})

evalApiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('LIGHTRAG-API-TOKEN')
  const apiKey = useSettingsStore.getState().apiKey
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey
  }
  return config
})

evalApiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      navigationService.navigateToLogin()
      throw new Error('Authentication required')
    }
    const payload = error.response?.data as { detail?: unknown; message?: unknown } | undefined
    const detail =
      typeof payload?.detail === 'string'
        ? payload.detail
        : typeof payload?.message === 'string'
          ? payload.message
          : null
    // A 400 is actionable only when the API's validation reason survives the
    // client interceptor.  Axios' default message was hiding it as merely
    // "Request failed with status code 400".
    if (detail) throw new Error(detail)
    if (Array.isArray(payload?.detail)) {
      const first = payload.detail[0] as
        { loc?: unknown; msg?: unknown; type?: unknown } | undefined
      const location = Array.isArray(first?.loc)
        ? first.loc.filter((item) => item !== 'body').map(String).join('.')
        : ''
      if (first?.type === 'extra_forbidden' && location === 'name') {
        throw new Error('服务器接口尚未更新。请重启 LightRAG API 后重试。')
      }
      if (typeof first?.msg === 'string') {
        throw new Error(`${location ? `${location}：` : ''}${first.msg}`)
      }
    }
    throw new Error(errorMessage(error))
  }
)

export async function listEvalRuns(params?: {
  dataset?: string
  q?: string
}): Promise<{ runs: EvalRun[]; runs_root: string }> {
  const response = await evalApiClient.get('/eval/runs', { params })
  return response.data
}

export async function getEvalRunLog(
  runId: string,
  lines = 200
): Promise<{ exists: boolean; lines: string[] }> {
  const response = await evalApiClient.get(`/eval/runs/${encodeURIComponent(runId)}/log`, {
    params: { lines }
  })
  return response.data
}

export async function listEvalModels(): Promise<{
  models: string[]
  embedding_filtered: string[]
  selectable_models?: string[]
  default_model?: string | null
  provider?: string
  model_selection?: 'selectable' | 'fixed'
  configuration_error?: string | null
  parser_engines?: string[]
  default_parser_engine?: string | null
}> {
  const response = await evalApiClient.get('/eval/models')
  return response.data
}

export async function createEvalJob(payload: {
  kind: 'run' | 'dataset'
  name?: string
  dataset?: string
  params?: Record<string, unknown>
  dataset_create?: {
    dataset_id?: string
    display_name: string
    tier?: string
    profile?: string
    language?: 'en' | 'zh'
    pages?: number | null
    formats?: string[]
    modalities?: string[]
    force?: boolean
    allow_oversized_generation?: boolean
  } | null
}): Promise<EvalJob> {
  const response = await evalApiClient.post('/eval/jobs', payload)
  return response.data
}

export async function listEvalJobs(): Promise<EvalJob[]> {
  const response = await evalApiClient.get('/eval/jobs')
  return response.data.jobs
}

export async function getEvalJob(jobId: string): Promise<EvalJob> {
  const response = await evalApiClient.get(`/eval/jobs/${encodeURIComponent(jobId)}`)
  return response.data
}

export async function cancelEvalJob(jobId: string): Promise<EvalJob> {
  const response = await evalApiClient.post(`/eval/jobs/${encodeURIComponent(jobId)}/cancel`)
  return response.data
}

export async function listDatasets(params?: {
  limit?: number
  offset?: number
}): Promise<{ datasets: DatasetSummary[]; total: number }> {
  const response = await evalApiClient.get('/eval/datasets', { params })
  return response.data
}

export async function importEvalDataset(file: File): Promise<DatasetSummary> {
  const payload = new FormData()
  payload.append('file', file)
  const response = await evalApiClient.post('/eval/datasets/import', payload, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  return response.data
}

export async function deleteDataset(datasetId: string): Promise<unknown> {
  const response = await evalApiClient.delete(`/eval/datasets/${encodeURIComponent(datasetId)}`)
  return response.data
}

export async function deleteEvalRun(runId: string): Promise<{ deleted: string }> {
  const response = await evalApiClient.delete(`/eval/runs/${encodeURIComponent(runId)}`)
  return response.data
}

export type RunComparisonContract = {
  comparable: boolean
  ranking_permitted: boolean
  incompatible_fields: string[]
  observed_values: Record<string, unknown[]>
}

export async function validateRunComparison(runIds: string[]): Promise<RunComparisonContract> {
  const response = await evalApiClient.post('/eval/comparisons/validate', { run_ids: runIds })
  return response.data
}

export async function getEvalRun(runId: string): Promise<EvalRunDetail> {
  const response = await evalApiClient.get(`/eval/runs/${encodeURIComponent(runId)}`)
  return response.data
}

export async function refreshEvalIndex(): Promise<{
  indexed_at: string
  file_count: number
  run_count: number
}> {
  const response = await evalApiClient.post('/eval/refresh')
  return response.data
}
