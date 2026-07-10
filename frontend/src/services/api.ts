import axios from 'axios'
import type {
  AgentChatRequest,
  AgentChatResponse,
  DestinationChatRequest,
  DestinationChatResponse,
  TripFormData,
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
 * 多智能体自然语言数据分析
 */
export async function chatAgentAnalysis(payload: AgentChatRequest): Promise<AgentChatResponse> {
  try {
    const response = await apiClient.post<AgentChatResponse>('/api/agent/chat', payload)
    return response.data
  } catch (error: any) {
    console.error('多智能体分析失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '多智能体分析失败')
  }
}

/**
 * 文件分析 — 上传旅行文档并获取 AI 分析结果
 */
export async function analyzeFile(
  file: File,
  question: string = '',
  userId: string = 'u_current',
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
      timeout: 300000 // 5 minutes for file analysis
    })
    return response.data
  } catch (error: any) {
    console.error('文件分析失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '文件分析失败')
  }
}

/**
 * 获取用户旅行历史
 */
export async function fetchTripHistory(userId: string = 'u_current'): Promise<{
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
  try {
    const response = await apiClient.get('/api/trip/history', {
      params: { user_id: userId }
    })
    return response.data
  } catch (error: any) {
    console.error('获取历史行程失败:', error)
    return { success: false, user_id: userId, stats: { total_trips: 0, avg_budget: 0, total_days: 0 }, fav_cities: [], trips: [] }
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
