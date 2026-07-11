import axios from 'axios'
import type {
  AgentChatRequest,
  AgentChatResponse,
  DestinationChatRequest,
  DestinationChatResponse,
  TripFormData,
  TripPlanResponse
} from '@/types'
import { getCurrentUser } from './auth'

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
  headers: {
    'Content-Type': 'application/json'
  }
})

const isTimeoutError = (error: any) => {
  return error?.code === 'ECONNABORTED' || String(error?.message || '').toLowerCase().includes('timeout')
}

const formatTimeout = (timeoutMs: number) => {
  const minutes = Math.round(timeoutMs / 60000)
  return minutes >= 1 ? `${minutes} 分钟` : `${Math.round(timeoutMs / 1000)} 秒`
}

apiClient.interceptors.request.use(
  (config) => {
    const user = getCurrentUser()
    if (user) {
      config.headers = config.headers || {}
      config.headers['x-user-id'] = user.user_id
      config.headers['x-user-role'] = user.role
    }
    return config
  },
  (error) => Promise.reject(error)
)

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
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

export async function chatDestinationRecommendation(
  payload: DestinationChatRequest
): Promise<DestinationChatResponse> {
  try {
    const response = await apiClient.post<DestinationChatResponse>('/api/recommend/chat', payload)
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '目的地推荐失败')
  }
}

export async function chatAgentAnalysis(payload: AgentChatRequest): Promise<AgentChatResponse> {
  try {
    const response = await apiClient.post<AgentChatResponse>('/api/agent/chat', payload)
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '智能分析失败')
  }
}

export async function analyzeFile(
  file: File,
  question: string = '',
  userId: string = '',
  role: string = 'user'
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
    formData.append('user_id', userId)
    formData.append('role', role)
    const response = await apiClient.post('/api/agent/analyze-file', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000
    })
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '文件分析失败')
  }
}

export async function fetchTripHistory(userId?: string): Promise<{
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
  }[]
}> {
  const currentUser = getCurrentUser()
  const targetUserId = userId || currentUser?.user_id
  if (!targetUserId) {
    return {
      success: false,
      user_id: '',
      stats: { total_trips: 0, avg_budget: 0, total_days: 0 },
      fav_cities: [],
      trips: []
    }
  }
  try {
    const response = await apiClient.get('/api/trip/history', {
      params: { user_id: targetUserId }
    })
    return response.data
  } catch {
    return {
      success: false,
      user_id: targetUserId,
      stats: { total_trips: 0, avg_budget: 0, total_days: 0 },
      fav_cities: [],
      trips: []
    }
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
