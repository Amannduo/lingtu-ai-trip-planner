import axios from 'axios'
import type {
  AgentChatRequest,
  AgentChatResponse,
  DestinationChatRequest,
  DestinationChatResponse,
  MapContextPOI,
  TripFormData,
  TripPlan,
  TripPlanResponse
} from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
const DEFAULT_REQUEST_TIMEOUT_MS = 120000
const planEtags = new Map<string, string>()

const planPath = (planNo: string) => `/api/trip/history/${encodeURIComponent(planNo)}`

const rememberPlanEtag = (planNo: string, value: unknown) => {
  if (typeof value === 'string' && value.trim()) {
    planEtags.set(planNo, value.trim())
  }
}

const resolveTripStreamUrl = (rawUrl: string): string => {
  const apiOrigin = new URL(API_BASE_URL || window.location.origin, window.location.origin)
  const target = new URL(rawUrl, apiOrigin)
  if (target.origin !== apiOrigin.origin) {
    throw new Error('服务器返回了不受信任的进度地址')
  }
  if (!/^\/api\/trip\/plan-jobs\/[a-f0-9]{32}\/events$/.test(target.pathname)) {
    throw new Error('服务器返回了无法识别的进度地址')
  }
  return target.toString()
}

const readTimeoutMs = (rawValue: string | undefined, fallbackMs: number) => {
  const timeoutMs = Number(rawValue)
  return Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : fallbackMs
}

export const TRIP_PLAN_TIMEOUT_MS = readTimeoutMs(import.meta.env.VITE_TRIP_PLAN_TIMEOUT_MS, 600000)
export const PHOTO_REQUEST_TIMEOUT_MS = readTimeoutMs(import.meta.env.VITE_PHOTO_REQUEST_TIMEOUT_MS, 12000)

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: DEFAULT_REQUEST_TIMEOUT_MS,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

export interface PushSubscriptionPayload {
  endpoint: string
  expirationTime: number | null
  keys: {
    p256dh: string
    auth: string
  }
}

type VapidPublicKeyResponse = {
  success: boolean
  public_key: string
}

type SavePushSubscriptionResponse = {
  success: boolean
  subscription_id: string
  created: boolean
}

type DeletePushSubscriptionResponse = {
  success: boolean
  deleted: boolean
}

const isTimeoutError = (error: any) =>
  error?.code === 'ECONNABORTED' || String(error?.message || '').toLowerCase().includes('timeout')

const formatTimeout = (timeoutMs: number) => {
  const minutes = Math.round(timeoutMs / 60000)
  return minutes >= 1 ? `${minutes} 分钟` : `${Math.round(timeoutMs / 1000)} 秒`
}

/** Structured issue from backend 422 hard-block / validation payloads. */
export type ApiIssue = {
  code?: string
  severity?: string
  path?: string
  message?: string
  suggestion?: string
  fields?: string[]
  conflicts?: string[]
  details?: string[]
  auto_repaired?: boolean
}

export class ApiClientError extends Error {
  status?: number
  issues: ApiIssue[]

  constructor(
    message: string,
    options?: { status?: number; issues?: ApiIssue[] }
  ) {
    super(message)
    this.name = 'ApiClientError'
    this.status = options?.status
    this.issues = options?.issues || []
  }
}

const asIssue = (value: unknown): ApiIssue | null => {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const message = typeof raw.message === 'string' ? raw.message : ''
  const msg = typeof raw.msg === 'string' ? raw.msg : ''
  const text = message || msg
  if (!text && !raw.code) return null
  return {
    code: typeof raw.code === 'string' ? raw.code : undefined,
    severity: typeof raw.severity === 'string' ? raw.severity : undefined,
    path: typeof raw.path === 'string'
      ? raw.path
      : Array.isArray(raw.loc)
        ? raw.loc.join('.')
        : undefined,
    message: text || undefined,
    suggestion: typeof raw.suggestion === 'string' ? raw.suggestion : undefined,
    fields: Array.isArray(raw.fields)
      ? raw.fields.filter((item): item is string => typeof item === 'string')
      : undefined,
    conflicts: Array.isArray(raw.conflicts)
      ? raw.conflicts.filter((item): item is string => typeof item === 'string')
      : undefined,
    details: Array.isArray(raw.details)
      ? raw.details.filter((item): item is string => typeof item === 'string')
      : undefined,
    auto_repaired: typeof raw.auto_repaired === 'boolean' ? raw.auto_repaired : undefined
  }
}

export const parseApiErrorDetail = (
  detail: unknown
): { message: string; issues: ApiIssue[] } => {
  if (typeof detail === 'string' && detail.trim()) {
    return { message: detail.trim(), issues: [] }
  }

  // FastAPI request-validation style: [{loc, msg, type}, ...]
  if (Array.isArray(detail)) {
    const issues = detail
      .map((item) => asIssue(item))
      .filter((item): item is ApiIssue => item !== null)
    const message = issues
      .map((item) => item.message)
      .filter(Boolean)
      .slice(0, 3)
      .join('；')
    return { message: message || '请求参数无效', issues }
  }

  if (detail && typeof detail === 'object') {
    const raw = detail as Record<string, unknown>
    const issues = Array.isArray(raw.issues)
      ? raw.issues
          .map((item) => asIssue(item))
          .filter((item): item is ApiIssue => item !== null)
      : []
    let message =
      typeof raw.message === 'string' && raw.message.trim()
        ? raw.message.trim()
        : ''
    if (!message && issues.length) {
      message = issues
        .map((item) => item.message)
        .filter(Boolean)
        .slice(0, 2)
        .join('；')
    }
    // Append first concrete issue when summary is too generic
    if (
      message
      && issues[0]?.message
      && !message.includes(issues[0].message)
    ) {
      message = `${message} ${issues[0].message}`
    }
    return { message: message || '请求被拒绝', issues }
  }

  return { message: '', issues: [] }
}

/**
 * Prefer the unified top-level {message, issues} the backend now sends for
 * every error class; fall back to parsing the legacy `detail` shapes.
 */
const parseApiErrorPayload = (
  data: unknown
): { message: string; issues: ApiIssue[] } => {
  if (data && typeof data === 'object') {
    const raw = data as Record<string, unknown>
    const topLevel = parseApiErrorDetail(raw)
    if (topLevel.issues.length || topLevel.message) return topLevel
    return parseApiErrorDetail(raw.detail)
  }
  return { message: '', issues: [] }
}

const responseError = (error: any, fallback: string) => {
  const parsed = parseApiErrorPayload(error?.response?.data)
  if (parsed.message) return parsed.message
  return error?.message || fallback
}

const toApiClientError = (error: any, fallback: string) => {
  const status = error?.response?.status
  const parsed = parseApiErrorPayload(error?.response?.data)
  const message = parsed.message || error?.message || fallback
  return new ApiClientError(message, {
    status: typeof status === 'number' ? status : undefined,
    issues: parsed.issues
  })
}


export async function fetchVapidPublicKey(): Promise<string> {
  try {
    const response = await apiClient.get<VapidPublicKeyResponse>(
      '/api/push/vapid-public-key'
    )
    if (!response.data.public_key) {
      throw new Error('\u670d\u52a1\u7aef\u672a\u63d0\u4f9b Web Push \u516c\u94a5')
    }
    return response.data.public_key
  } catch (error: any) {
    throw new Error(responseError(error, '\u65e0\u6cd5\u83b7\u53d6 Web Push \u516c\u94a5'))
  }
}

export async function savePushSubscription(
  subscription: PushSubscriptionPayload
): Promise<SavePushSubscriptionResponse> {
  try {
    const response = await apiClient.post<SavePushSubscriptionResponse>(
      '/api/push/subscriptions',
      { subscription }
    )
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '\u4fdd\u5b58\u540e\u53f0\u901a\u77e5\u8ba2\u9605\u5931\u8d25'))
  }
}

export async function deletePushSubscription(
  subscription: PushSubscriptionPayload
): Promise<DeletePushSubscriptionResponse> {
  try {
    const response = await apiClient.delete<DeletePushSubscriptionResponse>(
      '/api/push/subscriptions',
      { data: { subscription } }
    )
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '\u53d6\u6d88\u540e\u53f0\u901a\u77e5\u8ba2\u9605\u5931\u8d25'))
  }
}
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData, {
      timeout: TRIP_PLAN_TIMEOUT_MS
    })
    return response.data
  } catch (error: any) {
    if (isTimeoutError(error)) {
      throw new Error(`旅行计划生成时间较长，已超过 ${formatTimeout(TRIP_PLAN_TIMEOUT_MS)}，请减少天数或稍后重试`)
    }
    throw toApiClientError(error, '生成旅行计划失败')
  }
}


export interface TripGenerationProgress {
  id: number
  type: 'stage' | 'result' | 'error'
  stage?: string
  progress?: number
  message?: string
  detail?: string
  meta?: Record<string, string | number | boolean>
}

export async function generateTripPlanWithProgress(
  formData: TripFormData,
  onProgress: (event: TripGenerationProgress) => void
): Promise<TripPlanResponse> {
  if (typeof EventSource === 'undefined') {
    return generateTripPlan(formData)
  }

  let response
  try {
    response = await apiClient.post<{
      success: boolean
      job_id: string
      stream_url: string
    }>('/api/trip/plan-jobs', formData)
  } catch (error: any) {
    throw toApiClientError(error, '无法创建旅行规划任务')
  }

  return new Promise((resolve, reject) => {
    let streamUrl: string
    try {
      streamUrl = resolveTripStreamUrl(response.data.stream_url)
    } catch (error) {
      reject(error)
      return
    }
    const source = new EventSource(streamUrl, { withCredentials: true })
    let settled = false
    let timeoutId = 0

    const cleanup = () => {
      settled = true
      window.clearTimeout(timeoutId)
      source.close()
    }
    const failMalformedEvent = () => {
      if (settled) return
      cleanup()
      reject(new Error('服务器返回了无法识别的进度数据，请稍后重试'))
    }
    timeoutId = window.setTimeout(() => {
      if (settled) return
      cleanup()
      reject(new Error('旅行规划生成时间超过 ' + formatTimeout(TRIP_PLAN_TIMEOUT_MS) + '，请稍后重试'))
    }, TRIP_PLAN_TIMEOUT_MS)

    source.addEventListener('stage', rawEvent => {
      try {
        const event = JSON.parse((rawEvent as MessageEvent).data) as TripGenerationProgress
        onProgress(event)
      } catch {
        failMalformedEvent()
      }
    })
    source.addEventListener('result', rawEvent => {
      if (settled) return
      try {
        const event = JSON.parse((rawEvent as MessageEvent).data)
        if (!event.data) throw new Error('missing result')
        cleanup()
        resolve(event.data as TripPlanResponse)
      } catch {
        failMalformedEvent()
      }
    })
    source.addEventListener('error', rawEvent => {
      if (settled || !(rawEvent as MessageEvent).data) return
      try {
        const event = JSON.parse((rawEvent as MessageEvent).data)
        cleanup()
        // Keep the structured issues from the SSE error payload so the
        // form can highlight concrete problems and suggestions, exactly
        // like the sync path's 422 handling.
        const parsed = parseApiErrorDetail(event)
        const errorType = typeof event.error_type === 'string' ? event.error_type : ''
        const status = errorType === 'quality_rejected'
          ? 422
          : errorType === 'generation_timeout'
            ? 504
            : undefined
        reject(new ApiClientError(
          parsed.message || event.message || '旅行规划生成失败',
          { status, issues: parsed.issues }
        ))
      } catch {
        failMalformedEvent()
      }
    })
  })
}

export async function chatDestinationRecommendation(
  payload: DestinationChatRequest
): Promise<DestinationChatResponse> {
  try {
    const response = await apiClient.post<DestinationChatResponse>('/api/recommend/chat', payload)
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '目的地推荐失败'))
  }
}

export async function chatAgentAnalysis(payload: AgentChatRequest): Promise<AgentChatResponse> {
  try {
    const response = await apiClient.post<AgentChatResponse>('/api/agent/chat', payload)
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '智能分析失败'))
  }
}

export async function analyzeFile(
  file: File,
  question: string = ''
): Promise<{
  success: boolean
  summary: string
  suggestions: string[]
  extracted_info: Record<string, any>
  table: Record<string, any>[]
  file_type: string
}> {
  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('question', question)
    const response = await apiClient.post('/api/agent/analyze-file', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000
    })
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '文件分析失败'))
  }
}

export type TripHistoryResponse = {
  success: boolean
  user_id: string
  stats: { total_trips: number; avg_budget: number; total_days: number }
  fav_cities: { city: string; count: number }[]
  trips: {
    plan_no: string
    destination: string
    start_date: string
    end_date: string
    travel_days: number
    budget: number
    transportation: string
    summary: string
    created_at: string
    has_detail: boolean
  }[]
}

export async function fetchTripHistory(): Promise<TripHistoryResponse> {
  try {
    const response = await apiClient.get<TripHistoryResponse>('/api/trip/history')
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '\u8bfb\u53d6\u65c5\u884c\u5386\u53f2\u5931\u8d25'))
  }
}

export async function fetchTripPlan(planNo: string): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.get<TripPlanResponse>(planPath(planNo))
    rememberPlanEtag(planNo, response.headers.etag)
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '读取历史行程失败'))
  }
}

export async function updateTripPlan(
  planNo: string,
  plan: NonNullable<TripPlanResponse['data']>
): Promise<TripPlanResponse> {
  try {
    const etag = planEtags.get(planNo)
    const response = await apiClient.put<TripPlanResponse>(
      planPath(planNo),
      plan,
      { headers: etag ? { 'If-Match': etag } : undefined }
    )
    rememberPlanEtag(planNo, response.headers.etag)
    return response.data
  } catch (error: any) {
    throw new Error(responseError(error, '保存旅行计划失败'))
  }
}

export async function fetchMapContext(plan: TripPlan): Promise<MapContextPOI[]> {
  const locations = plan.days.flatMap(day =>
    day.attractions
      .map(attraction => attraction.location)
      .filter(location =>
        Number.isFinite(Number(location?.longitude))
        && Number.isFinite(Number(location?.latitude))
      )
  )
  if (!locations.length) return []

  try {
    const response = await apiClient.post<{
      success: boolean
      data: MapContextPOI[]
    }>('/api/map/context', {
      city: plan.city,
      locations,
      limit: 24
    }, {
      timeout: 45000
    })
    return response.data.data || []
  } catch (error: any) {
    console.warn('获取地图周边场所失败:', error)
    return []
  }
}

export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient