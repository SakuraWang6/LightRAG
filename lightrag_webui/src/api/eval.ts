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
  kind: EvalRunKind
  label: string
  dataset?: string | null
  updated_at?: string | null
  status?: string | null
  conditions: RunCondition[]
  headline: Record<string, MetricItem>
  artifact_titles: string[]
}

export type EvalRunDetail = EvalRun & {
  artifacts: EvalArtifact[]
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
