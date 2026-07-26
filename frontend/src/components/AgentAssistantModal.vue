<template>
  <a-modal
    :open="open"
    width="1040px"
    :footer="null"
    class="agent-modal"
    @cancel="$emit('close')"
  >
    <template #title>
      <div class="modal-title">
        <div>
          <span>智能旅行数据分析</span>
          <p>每个结论都附带权限范围、时间口径和样本量</p>
        </div>
        <a-tag v-if="user" :color="roleColor">{{ roleLabel }}</a-tag>
      </div>
    </template>

    <div v-if="!user" class="login-required">
      <h3>登录后使用受权限保护的旅行分析</h3>
      <p>系统只会读取当前账号角色允许的数据，并返回表格、图表和数据质量说明。</p>
      <a-button type="primary" @click="$emit('request-login')">去登录</a-button>
    </div>

    <div v-else class="assistant-layout">
      <section class="context-panel">
        <a-skeleton v-if="contextLoading" active :paragraph="{ rows: 2 }" />
        <template v-else>
          <div class="scope-row">
            <div>
              <small>当前数据权限</small>
              <strong>{{ capabilities?.scope_label || dataStatus?.scope_label || fallbackScope }}</strong>
            </div>
            <button type="button" class="refresh-button" @click="loadContext">刷新数据状态</button>
          </div>

          <div class="status-grid">
            <div class="status-item">
              <span>可见计划</span>
              <strong>{{ dataStatus?.visible_plans ?? '—' }}</strong>
            </div>
            <div class="status-item">
              <span>可见用户</span>
              <strong>{{ dataStatus?.visible_users ?? '—' }}</strong>
            </div>
            <div class="status-item">
              <span>目的地</span>
              <strong>{{ dataStatus?.destinations ?? '—' }}</strong>
            </div>
            <div class="status-item status-wide">
              <span>时间覆盖</span>
              <strong>{{ coverageLabel }}</strong>
            </div>
          </div>

          <div v-if="dataStatus?.sources?.length" class="source-row">
            <span>数据来源</span>
            <a-tag v-for="source in dataStatus.sources" :key="source.source">
              {{ source.source }} · {{ source.count }}
            </a-tag>
          </div>

          <div class="capability-row">
            <div>
              <small>可以分析</small>
              <span>{{ capabilities?.permissions?.join('、') || '正在读取' }}</span>
            </div>
            <div>
              <small>限制</small>
              <span>{{ capabilities?.restrictions?.join('；') || '由服务端角色策略控制' }}</span>
            </div>
          </div>

          <a-alert
            v-for="warning in contextWarnings"
            :key="warning"
            type="warning"
            show-icon
            :message="warning"
            class="context-warning"
          />
        </template>
      </section>

      <div ref="chatStreamRef" class="chat-area">
        <div v-for="item in messages" :key="item.id" class="message" :class="item.role">
          <div class="message-head">
            <strong>{{ item.role === 'user' ? '你' : item.agent || '灵途分析助手' }}</strong>
            <span v-if="item.intent">{{ intentLabel(item.intent) }} · {{ item.tool }}</span>
          </div>
          <p class="message-content">{{ item.content }}</p>

          <a-alert
            v-if="item.permission && !item.permission.allowed"
            type="warning"
            show-icon
            :message="item.permission.reason"
          />

          <div v-if="item.analysis" class="analysis-meta">
            <a-tag color="blue">{{ item.analysis.scope_label }}</a-tag>
            <a-tag>{{ periodLabel(item.analysis.period) }}</a-tag>
            <a-tag :color="item.analysis.sample_size < 3 ? 'orange' : 'green'">
              样本 {{ item.analysis.sample_size }} 条
            </a-tag>
          </div>

          <a-alert
            v-for="warning in item.analysis?.warnings || []"
            :key="warning"
            type="warning"
            show-icon
            :message="warning"
            class="message-warning"
          />

          <AgentChart v-if="item.chart" :chart="item.chart" class="message-chart" />

          <a-table
            v-if="item.table?.length"
            :columns="columnsFor(item.table)"
            :data-source="tableRows(item.table)"
            :pagination="{ pageSize: 6, hideOnSinglePage: true }"
            :scroll="{ x: true }"
            size="small"
            class="message-table"
          />
        </div>
        <div v-if="loading" class="thinking">正在解析问题、检查权限并查询数据…</div>
      </div>

      <div class="assistant-tools">
        <button type="button" class="example-toggle" @click="showExamples = !showExamples">
          {{ showExamples ? '收起适用问题' : `查看${roleLabel}可用问题` }}
        </button>
        <div v-if="showExamples" class="examples">
          <button v-for="prompt in quickPrompts" :key="prompt" type="button" @click="ask(prompt)">
            {{ prompt }}
          </button>
        </div>
      </div>

      <div class="composer">
        <a-input
          v-model:value="email"
          placeholder="仅发送个人报告时填写邮箱，可选"
          class="email-input"
        />
        <a-textarea
          v-model:value="input"
          :rows="3"
          :placeholder="composerPlaceholder"
          @press-enter.ctrl="send"
        />
        <a-button type="primary" :loading="loading" @click="send">分析</a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { message as toast } from 'ant-design-vue'
import AgentChart from '@/components/AgentChart.vue'
import type { LocalUser } from '@/services/auth'
import {
  chatAgentV1,
  fetchAgentCapabilities,
  fetchAgentDataStatus,
  type AgentAnalysisMeta,
  type AgentCapabilities,
  type AgentChartPayload,
  type AgentDataStatus,
  type AgentPermission
} from '@/services/agentAnalytics'
import { normalizeAgentChart } from '@/types/agentChart'

type ChatItem = {
  id: number
  role: 'user' | 'assistant'
  content: string
  intent?: string
  agent?: string
  tool?: string
  table?: Record<string, unknown>[]
  chart?: AgentChartPayload | null
  permission?: AgentPermission
  analysis?: AgentAnalysisMeta
}

const props = defineProps<{
  open: boolean
  user: LocalUser | null
}>()

defineEmits<{
  close: []
  'request-login': []
}>()

const input = ref('')
const email = ref('')
const loading = ref(false)
const contextLoading = ref(false)
const showExamples = ref(true)
const chatStreamRef = ref<HTMLElement | null>(null)
const capabilities = ref<AgentCapabilities | null>(null)
const dataStatus = ref<AgentDataStatus | null>(null)
const requestEpoch = ref(0)
const messages = ref<ChatItem[]>([
  {
    id: 1,
    role: 'assistant',
    content: '我会先确定你的角色权限和时间范围，再查询真实数据、绘图并说明样本是否足够。'
  }
])

const roleLabels: Record<string, string> = {
  guest: '访客',
  user: '普通用户',
  manager: '经理',
  admin: '管理员'
}

const roleLabel = computed(() => roleLabels[props.user?.role || 'guest'] || '用户')
const roleColor = computed(() => {
  const colors: Record<string, string> = { user: 'blue', manager: 'purple', admin: 'red' }
  return colors[props.user?.role || ''] || 'default'
})
const fallbackScope = computed(() => {
  if (props.user?.role === 'admin') return '全站汇总、非敏感明细与审计数据'
  if (props.user?.role === 'manager') return '全站匿名汇总，不含用户或计划明细'
  return '仅当前账号的旅行计划'
})

const fallbackPrompts: Record<string, string[]> = {
  user: ['分析我的旅行兴趣画像', '统计我本季度最常去的目的地', '展示我的月度预算趋势'],
  manager: ['统计本季度最热门的旅行目的地', '对比本月和去年同期的目的地热度', '预测下个月热门目的地'],
  admin: ['统计本季度所有人的旅行去向', '检查当前数据质量和来源分布', '查看最近的智能分析审计日志']
}

const quickPrompts = computed(() =>
  capabilities.value?.quick_prompts?.length
    ? capabilities.value.quick_prompts
    : fallbackPrompts[props.user?.role || 'user']
)

const coverageLabel = computed(() => {
  const range = dataStatus.value?.date_range
  if (!range?.min || !range?.max) return '暂无数据'
  return `${range.min} 至 ${range.max}（约 ${range.covered_months} 个月）`
})

const contextWarnings = computed(() => (dataStatus.value?.warnings || []).slice(0, 3))
const composerPlaceholder = computed(() => `以${roleLabel.value}权限提问，例如：${quickPrompts.value?.[0] || '统计旅行目的地'}`)

function resetSession() {
  input.value = ''
  email.value = ''
  loading.value = false
  contextLoading.value = false
  capabilities.value = null
  dataStatus.value = null
  messages.value = [
    {
      id: Date.now(),
      role: 'assistant',
      content: '这是一个新的账号会话。我会按当前角色重新检查权限和数据范围。'
    }
  ]
}

const sessionKey = computed(() =>
  props.user ? `${props.user.user_id}:${props.user.role}` : ''
)

watch(
  () => sessionKey.value,
  () => {
    requestEpoch.value += 1
    resetSession()
    if (props.open && props.user) void loadContext()
  },
  { flush: 'sync' }
)

watch(
  () => props.open,
  isOpen => {
    if (isOpen && props.user) {
      void loadContext()
      nextTick(scrollToBottom)
    }
  }
)

async function loadContext() {
  const userId = props.user?.user_id
  if (!userId || contextLoading.value) return
  const epoch = requestEpoch.value
  const isCurrent = () => (
    requestEpoch.value === epoch
    && props.user?.user_id === userId
  )
  contextLoading.value = true
  try {
    const [nextCapabilities, nextStatus] = await Promise.all([
      fetchAgentCapabilities(),
      fetchAgentDataStatus()
    ])
    if (!isCurrent()) return
    capabilities.value = nextCapabilities
    dataStatus.value = nextStatus
  } catch (error: any) {
    if (isCurrent()) toast.warning(error.message || '暂时无法读取分析数据状态')
  } finally {
    if (isCurrent()) contextLoading.value = false
  }
}

function ask(prompt: string) {
  input.value = prompt
  send()
}

async function send() {
  if (!props.user) {
    toast.warning('请先登录')
    return
  }
  const userId = props.user.user_id
  const epoch = requestEpoch.value
  const isCurrent = () => (
    requestEpoch.value === epoch
    && props.user?.user_id === userId
  )
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({ id: Date.now(), role: 'user', content: text })
  input.value = ''
  loading.value = true
  await nextTick(scrollToBottom)

  try {
    const response = await chatAgentV1({
      message: text,
      email: email.value.trim() || undefined
    })
    if (!isCurrent()) return
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: response.result,
      intent: response.intent,
      agent: response.agent,
      tool: response.tool,
      table: response.table,
      chart: normalizeAgentChart(response.chart),
      permission: response.permission,
      analysis: response.extra?.analysis
    })
    await loadContext()
  } catch (error: any) {
    if (isCurrent()) toast.error(error.message || '智能分析失败')
  } finally {
    if (isCurrent()) {
      loading.value = false
      await nextTick(scrollToBottom)
    }
  }
}

function columnsFor(rows: Record<string, unknown>[]) {
  return Object.keys(rows[0] || {}).map(key => ({
    title: key,
    dataIndex: key,
    key,
    ellipsis: true
  }))
}

function tableRows(rows: Record<string, unknown>[]) {
  return rows.map((row, index) => ({ ...row, key: index }))
}

function periodLabel(period: AgentAnalysisMeta['period']) {
  if (!period?.start || !period?.end) return period?.label || '全部历史'
  return `${period.label} · ${period.start} 至 ${period.end}`
}

function intentLabel(intent: string) {
  const labels: Record<string, string> = {
    city_rank: '目的地排行',
    avg_budget: '预算分析',
    budget_trend: '月度趋势',
    profile: '个人画像',
    recommendation: '画像推荐',
    prediction: '趋势预测',
    traveler_type_distribution: '人群分布',
    all_plan_detail: '计划明细',
    data_quality: '数据质量',
    audit_log: '审计日志'
  }
  return labels[intent] || intent
}

function scrollToBottom() {
  const element = chatStreamRef.value
  if (element) element.scrollTop = element.scrollHeight
}
</script>

<style scoped>
.modal-title,
.scope-row,
.message-head,
.composer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.modal-title span {
  color: #172033;
  font-size: 18px;
  font-weight: 800;
}

.modal-title p {
  margin: 3px 0 0;
  color: #667085;
  font-size: 12px;
  font-weight: 400;
}

.login-required {
  min-height: 340px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
}

.login-required h3,
.login-required p {
  margin: 0;
}

.login-required p {
  max-width: 520px;
  color: #667085;
}

.assistant-layout {
  display: grid;
  gap: 14px;
}

.context-panel {
  padding: 16px;
  border: 1px solid #d9e7e3;
  border-radius: 12px;
  background: linear-gradient(135deg, #f0fdfa, #f8fafc 65%);
}

.scope-row small,
.capability-row small {
  display: block;
  margin-bottom: 3px;
  color: #667085;
  font-size: 12px;
}

.scope-row strong {
  color: #0f766e;
  font-size: 15px;
}

.refresh-button,
.example-toggle,
.examples button {
  border: 0;
  background: transparent;
  color: #0f766e;
  cursor: pointer;
  font: inherit;
}

.refresh-button {
  font-size: 12px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(90px, 1fr)) minmax(220px, 2fr);
  gap: 10px;
  margin: 14px 0;
}

.status-item {
  padding: 10px 12px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.78);
}

.status-item span {
  display: block;
  color: #667085;
  font-size: 12px;
}

.status-item strong {
  color: #172033;
  font-size: 20px;
}

.status-wide strong {
  font-size: 13px;
  line-height: 1.5;
}

.source-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: #475467;
  font-size: 12px;
}

.capability-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  color: #344054;
  font-size: 12px;
  line-height: 1.55;
}

.context-warning {
  margin-top: 8px;
}

.chat-area {
  min-height: 300px;
  max-height: 48vh;
  overflow: auto;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #f8fafc;
}

.message {
  max-width: 94%;
  margin-bottom: 14px;
  padding: 13px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fff;
}

.message.user {
  margin-left: auto;
  border-color: #bfdbfe;
  background: #eff6ff;
}

.message-head span {
  color: #98a2b3;
  font-size: 11px;
}

.message-content {
  margin: 8px 0;
  color: #344054;
  line-height: 1.7;
  white-space: pre-wrap;
}

.analysis-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}

.message-warning {
  margin-top: 7px;
}

.message-chart {
  height: 340px;
  margin-top: 12px;
}

.message-table {
  margin-top: 12px;
}

.thinking {
  color: #0f766e;
  font-size: 13px;
}

.assistant-tools {
  padding: 0 2px;
}

.example-toggle {
  padding: 0;
  font-weight: 700;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 9px;
}

.examples button {
  padding: 7px 10px;
  border: 1px solid #b7ddd5;
  border-radius: 999px;
  background: #f0fdfa;
  font-size: 12px;
}

.composer {
  align-items: flex-end;
}

.email-input {
  width: 235px;
  flex: 0 0 auto;
}

.composer :deep(.ant-input-textarea) {
  flex: 1;
}

@media (max-width: 760px) {
  .status-grid,
  .capability-row {
    grid-template-columns: 1fr 1fr;
  }

  .status-wide {
    grid-column: 1 / -1;
  }

  .composer {
    align-items: stretch;
    flex-direction: column;
  }

  .email-input {
    width: 100%;
  }
}
</style>
