import axios, { AxiosError } from 'axios'
import { backendBaseUrl } from '@/lib/constants'
import { errorMessage } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settings'
import { navigationService } from '@/services/navigation'

export type EvalRunKind = 'offline' | 'online' | 'experiment' | 'report'

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

export type VariableArm = {
  arm: string
  label?: string
  [key: string]: unknown
}

export type EvalVariable = {
  axis: string
  label?: string
  arms: VariableArm[]
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
  kind: EvalRunKind
  legacy?: boolean
  restarts?: number
  last_restart_resume?: boolean | null
  label: string
  experiment?: string | null
  launch_params?: Record<string, unknown> | null
  dataset?: string | null
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_seconds?: number | null
  status?: string | null
  conditions: RunCondition[]
  description?: string
  variables: EvalVariable[]
  progress: EvalRunProgress
  failed_checks?: string[]
  headline: Record<string, MetricItem>
  artifact_titles: string[]
}

export type EvalRunDetail = EvalRun & {
  artifacts: EvalArtifact[]
}

export type EvalExperiment = {
  id: string
  label: string
  description: string
  supervision: string
  supports_resume: boolean
  default_baseline: Record<string, unknown>
  variables: EvalVariable[]
  extra_schema: Record<string, string>
  env_required: string[]
  env_ready: boolean
}

export type EvalJob = {
  id: string
  kind: 'run' | 'dataset'
  experiment?: string | null
  dataset?: string | null
  dataset_id?: string | null
  output_dir: string
  supervise?: boolean
  status: string
  queue_position?: number | null
  active_count?: number | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  log?: string[]
  params?: Record<string, unknown>
}

export type DatasetSummary = {
  dataset_id: string
  tier: string
  profile: string
  pages: number
  path: string
  created_at: string
  files: string[]
}

export type EvalTemplate = {
  name: string
  experiment: string
  dataset: string
  params: Record<string, unknown>
  extraText?: string
  supervise: boolean
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
    throw new Error(errorMessage(error))
  }
)

export async function listEvalRuns(params?: {
  kind?: EvalRunKind | ''
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
  const response = await evalApiClient.get(
    `/eval/runs/${encodeURIComponent(runId)}/log`,
    { params: { lines } }
  )
  return response.data
}

export async function listEvalExperiments(): Promise<EvalExperiment[]> {
  const response = await evalApiClient.get('/eval/experiments')
  return response.data.experiments
}

export async function createEvalJob(payload: {
  kind: 'run' | 'dataset'
  experiment?: string
  dataset?: string
  params?: Record<string, unknown>
  supervise?: boolean
  supervision?: string
  stale_minutes?: number
  max_restarts?: number
  poll_seconds?: number
  dataset_create?: {
    dataset_id: string
    tier?: string
    profile?: string
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
  const response = await evalApiClient.post(
    `/eval/jobs/${encodeURIComponent(jobId)}/cancel`
  )
  return response.data
}

export async function listDatasets(
  params?: { limit?: number; offset?: number }
): Promise<{ datasets: DatasetSummary[]; total: number }> {
  const response = await evalApiClient.get('/eval/datasets', { params })
  return response.data
}

export async function deleteDataset(datasetId: string): Promise<unknown> {
  const response = await evalApiClient.delete(
    `/eval/datasets/${encodeURIComponent(datasetId)}`
  )
  return response.data
}

export async function deleteEvalRun(runId: string): Promise<{ deleted: string }> {
  const response = await evalApiClient.delete(
    `/eval/runs/${encodeURIComponent(runId)}`
  )
  return response.data
}

export async function listEvalModels(): Promise<{
  models: string[]
  embedding_filtered: string[]
}> {
  const response = await evalApiClient.get('/eval/models')
  return response.data
}

export async function listEvalTemplates(): Promise<EvalTemplate[]> {
  const response = await evalApiClient.get('/eval/templates')
  return response.data.templates
}

export async function saveEvalTemplate(
  template: EvalTemplate
): Promise<{ saved: string }> {
  const response = await evalApiClient.post('/eval/templates', template)
  return response.data
}

export async function deleteEvalTemplate(name: string): Promise<{ deleted: string }> {
  const response = await evalApiClient.delete('/eval/templates', {
    params: { name }
  })
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

export async function analyzeEvalRun(
  runId: string,
  force = false
): Promise<{ created_at: string; model: string; text: string }> {
  const response = await evalApiClient.post(
    `/eval/runs/${encodeURIComponent(runId)}/analyze`,
    null,
    { params: { force: force ? '1' : undefined } }
  )
  return response.data
}
