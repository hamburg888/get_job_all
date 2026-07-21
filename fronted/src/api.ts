import type {
  AccountPayload,
  AccountItem,
  DashboardFilters,
  DeliveryItem,
  JobItem,
  LogItem,
  PaginatedResult,
  Platform,
  PlatformOverview,
  PreviewState,
  PlatformStats,
  ResumeCenterData,
  ResumeSyncFieldSelection,
  MasterResumeUpdatePayload,
  StartTaskPayload,
  TaskStatus,
  LLMConfig,
  LLMConfigUpdatePayload,
  LLMTestResult,
  TaskRunRecord,
  AIAdvice,
  AIAdviceRequest,
} from './types'

const envApiBaseUrl = typeof import.meta.env.VITE_API_BASE_URL === 'string' ? import.meta.env.VITE_API_BASE_URL.trim() : ''
const defaultApiBaseUrl = import.meta.env.DEV ? 'http://127.0.0.1:8000/api' : '/api'

const API_BASE_URL = (envApiBaseUrl || defaultApiBaseUrl).replace(/\/+$/, '')

function resolveApiOrigin(apiBaseUrl: string) {
  if (/^https?:\/\//i.test(apiBaseUrl)) {
    return apiBaseUrl.replace(/\/api$/, '')
  }
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin
  }
  return ''
}

export const API_ORIGIN = resolveApiOrigin(API_BASE_URL)
const REQUEST_TIMEOUT_MS = 30_000

async function request<T>(path: string, init?: RequestInit, timeoutMs: number = REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        'Content-Type': 'application/json',
      },
      ...init,
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error(`API request timeout after ${Math.floor(timeoutMs / 1000)}s`)
    }
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }

  if (!response.ok) {
    let message = `API request failed: ${response.status}`
    try {
      const data = (await response.json()) as { detail?: string }
      if (typeof data?.detail === 'string' && data.detail.trim()) {
        message = data.detail.trim()
      }
    } catch {
      try {
        const text = await response.text()
        if (text.trim()) {
          message = text.trim()
        }
      } catch {
        // Ignore secondary parse errors and keep the default message.
      }
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}

function buildDashboardQuery(filters?: Partial<DashboardFilters>, page?: number, pageSize?: number) {
  const search = new URLSearchParams()
  if (typeof page === 'number') search.set('page', String(page))
  if (typeof pageSize === 'number') search.set('page_size', String(pageSize))
  if (!filters) return search.toString()
  if (typeof filters.account_id === 'number') search.set('account_id', String(filters.account_id))
  for (const status of filters.statuses ?? []) {
    if (status) search.append('statuses', status)
  }
  for (const status of filters.send_statuses ?? []) {
    if (status) search.append('send_statuses', status)
  }
  if (filters.date_from) search.set('date_from', filters.date_from)
  if (filters.date_to) search.set('date_to', filters.date_to)
  return search.toString()
}

export function fetchOverview(platform: Platform, filters?: Partial<DashboardFilters>) {
  const query = buildDashboardQuery(filters)
  return request<PlatformOverview>(`/dashboard/overview/${platform}${query ? `?${query}` : ''}`)
}

export function fetchAccounts(platform?: Platform) {
  const suffix = platform ? `?platform=${platform}` : ''
  return request<AccountItem[]>(`/accounts${suffix}`)
}

export function createAccount(payload: AccountPayload) {
  return request<AccountItem>('/accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateAccount(accountId: number, payload: AccountPayload) {
  return request<AccountItem>(`/accounts/${accountId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function setDefaultAccount(accountId: number) {
  return request<AccountItem>('/accounts/default', {
    method: 'POST',
    body: JSON.stringify({ account_id: accountId }),
  })
}

export function openAccountBrowserConfig(accountId: number) {
  return request<{ account_id: number; active: boolean; message: string }>(`/accounts/${accountId}/browser-config/open`, {
    method: 'POST',
  })
}

export function closeAccountBrowserConfig(accountId: number) {
  return request<{ account_id: number; active: boolean; message: string }>(`/accounts/${accountId}/browser-config/close`, {
    method: 'POST',
  })
}

export function fetchTaskStatus(platform: Platform) {
  return request<TaskStatus>(`/tasks/status/${platform}`)
}

export function fetchPreviewState(platform: Platform) {
  return request<PreviewState>(`/tasks/preview/state/${platform}`)
}

export function previewStreamUrl(platform: Platform) {
  return `${API_BASE_URL}/tasks/preview/stream/${platform}`
}

export function fetchJobs(platform: Platform, page = 1, pageSize = 10, filters?: Partial<DashboardFilters>) {
  const query = buildDashboardQuery(filters, page, pageSize)
  return request<PaginatedResult<JobItem>>(`/dashboard/jobs/${platform}${query ? `?${query}` : ''}`)
}

export function fetchDeliveries(platform: Platform, page = 1, pageSize = 10, filters?: Partial<DashboardFilters>) {
  const query = buildDashboardQuery(filters, page, pageSize)
  return request<PaginatedResult<DeliveryItem>>(`/dashboard/deliveries/${platform}${query ? `?${query}` : ''}`)
}

export function fetchLogs(platform: Platform, filters?: Partial<DashboardFilters>) {
  const query = buildDashboardQuery(filters)
  return request<LogItem[]>(`/dashboard/logs/${platform}${query ? `?${query}` : ''}`)
}

export function fetchStats(platform: Platform, filters?: Partial<DashboardFilters>) {
  const query = buildDashboardQuery(filters)
  return request<PlatformStats>(`/dashboard/stats/${platform}${query ? `?${query}` : ''}`)
}

export function startTask(payload: StartTaskPayload) {
  return request<TaskStatus>('/tasks/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function stopTask(platform: Platform) {
  return request<TaskStatus>('/tasks/stop', {
    method: 'POST',
    body: JSON.stringify({ platform }),
  })
}

export function confirmLogin(platform: Platform) {
  return request<TaskStatus>('/tasks/confirm-login', {
    method: 'POST',
    body: JSON.stringify({ platform }),
  })
}

export function fetchHealth() {
  const healthUrl = API_ORIGIN ? `${API_ORIGIN}/health` : '/health'
  return request<{ status: string }>(healthUrl.replace(API_ORIGIN, ''))
}

export function fetchResumeCenter() {
  return request<ResumeCenterData>('/resume/center')
}

export function importResume(payload: { source_platform: string; account_id: number | null; headless_enabled?: boolean }) {
  return request<ResumeCenterData>('/resume/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function syncResume(payload: {
  target_platforms: string[]
  account_map?: Record<string, number | null>
  field_selection?: ResumeSyncFieldSelection
}) {
  return request<ResumeCenterData>('/resume/sync', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateMasterResume(payload: MasterResumeUpdatePayload) {
  return request<ResumeCenterData>('/resume/master', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function uploadBossImageResume(file: File) {
  const contentBase64 = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const [, base64 = ''] = result.split(',', 2)
      resolve(base64)
    }
    reader.readAsDataURL(file)
  })

  return request<{ path: string; name: string; url: string | null }>('/uploads/boss-chat-image', {
    method: 'POST',
    body: JSON.stringify({
      file_name: file.name,
      content_base64: contentBase64,
    }),
  })
}

export function fetchLLMConfig() {
  return request<LLMConfig>('/settings/llm')
}

export function updateLLMConfig(payload: LLMConfigUpdatePayload) {
  return request<LLMConfig>('/settings/llm', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function testLLMConfig() {
  return request<LLMTestResult>('/settings/llm/test', {
    method: 'POST',
  }, 60_000)
}

export function fetchTaskRuns(platform?: string, limit = 50) {
  const params = new URLSearchParams()
  if (platform) params.set('platform', platform)
  params.set('limit', String(limit))
  const query = params.toString()
  return request<TaskRunRecord[]>(`/tasks/runs${query ? `?${query}` : ''}`)
}

export function fetchAIAdvice(payload: AIAdviceRequest) {
  return request<AIAdvice>('/ai/advise', {
    method: 'POST',
    body: JSON.stringify(payload),
  }, 120_000)
}
