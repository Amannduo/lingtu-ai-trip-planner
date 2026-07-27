import apiClient from '@/services/api'
import type { AgentChartPayload } from '@/types/agentChart'

export type { AgentChartPayload, AgentChartKind, AgentChartSeries } from '@/types/agentChart'

export interface AgentPermission {
  role: string
  allowed: boolean
  reason: string
}

export interface AgentPeriod {
  key: string
  label: string
  start: string | null
  end: string | null
  comparison_start?: string | null
  comparison_end?: string | null
  comparison_label?: string
}

export interface AgentAnalysisMeta {
  scope: 'personal' | 'global_aggregate' | 'global' | string
  scope_label: string
  period: AgentPeriod
  sample_size: number
  row_count: number
  data_quality: Record<string, number>
  sufficient_for: Record<string, boolean>
  warnings: string[]
}

export interface AgentChatResponseV1 {
  success: boolean
  intent: string
  agent: string
  tool: string
  table: Record<string, unknown>[]
  chart: AgentChartPayload | null
  result: string
  permission: AgentPermission
  sensitive: Record<string, unknown>
  extra: {
    analysis?: AgentAnalysisMeta
    [key: string]: unknown
  }
}

export interface AgentCapabilities {
  role: 'user' | 'manager' | 'admin' | string
  scope: string
  scope_label: string
  permissions: string[]
  restrictions: string[]
  quick_prompts: string[]
}

export interface AgentDataStatus {
  role: string
  scope: string
  scope_label: string
  visible_plans: number
  visible_users: number
  destinations: number
  date_range: {
    min: string | null
    max: string | null
    span_days: number
    covered_months: number
  }
  sources: Array<{ source: string; count: number }>
  quality: {
    budget_completeness: number
    actual_cost_completeness: number
    plan_json_completeness: number
    synthetic_ratio: number
  }
  sufficient_for: {
    facts: boolean
    trend: boolean
    prediction: boolean
    year_over_year: boolean
  }
  warnings: string[]
}

const errorMessage = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

export async function fetchAgentCapabilities(): Promise<AgentCapabilities> {
  try {
    const response = await apiClient.get<AgentCapabilities>('/api/agent/capabilities')
    return response.data
  } catch (error: any) {
    throw new Error(errorMessage(error, '读取角色分析能力失败'))
  }
}

export async function fetchAgentDataStatus(): Promise<AgentDataStatus> {
  try {
    const response = await apiClient.get<AgentDataStatus>('/api/agent/data-status')
    return response.data
  } catch (error: any) {
    throw new Error(errorMessage(error, '读取分析数据状态失败'))
  }
}

export async function chatAgentV1(payload: {
  message: string
  email?: string
}): Promise<AgentChatResponseV1> {
  try {
    const response = await apiClient.post<AgentChatResponseV1>('/api/agent/chat', payload)
    return response.data
  } catch (error: any) {
    throw new Error(errorMessage(error, '智能分析失败'))
  }
}
