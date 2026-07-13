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

const responseError = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback


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
    throw new Error(responseError(error, '生成旅行计划失败'))
  }
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

const emptyHistory = (): TripHistoryResponse => ({
  success: false,
  user_id: '',
  stats: { total_trips: 0, avg_budget: 0, total_days: 0 },
  fav_cities: [],
  trips: []
})

export async function fetchTripHistory(): Promise<TripHistoryResponse> {
  try {
    const response = await apiClient.get<TripHistoryResponse>('/api/trip/history')
    return response.data
  } catch {
    return emptyHistory()
  }
}

export async function fetchTripPlan(planNo: string): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.get<TripPlanResponse>(`/api/trip/history/${planNo}`)
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
    const response = await apiClient.put<TripPlanResponse>(
      `/api/trip/history/${planNo}`,
      plan
    )
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