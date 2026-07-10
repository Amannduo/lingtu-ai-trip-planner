import axios from 'axios'
import type { DestinationChatRequest, DestinationChatResponse, TripFormData, TripPlanResponse } from '@/types'

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
  return minutes >= 1 ? `${minutes}分钟` : `${Math.round(timeoutMs / 1000)}秒`
}

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData, {
      timeout: TRIP_PLAN_TIMEOUT_MS
    })
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    if (isTimeoutError(error)) {
      throw new Error(`旅行计划生成耗时较长，已超过${formatTimeout(TRIP_PLAN_TIMEOUT_MS)}等待时间，请减少天数或稍后重试`)
    }
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

/**
 * 目的地推荐对话
 */
export async function chatDestinationRecommendation(
  payload: DestinationChatRequest
): Promise<DestinationChatResponse> {
  try {
    const response = await apiClient.post<DestinationChatResponse>('/api/recommend/chat', payload)
    return response.data
  } catch (error: any) {
    console.error('目的地推荐失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '目的地推荐失败')
  }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient
