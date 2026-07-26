<template>
  <a-modal
    :open="open"
    width="1080px"
    :footer="null"
    centered
    destroy-on-close
    class="agent-modal"
    :body-style="{ padding: '0 0 18px' }"
    @cancel="$emit('close')"
  >
    <template #title>
      <div class="modal-title">
        <div class="modal-title__copy">
          <span class="modal-kicker">LINGTU ANALYTICS</span>
          <strong>智能旅行数据分析</strong>
          <p>自然语言提问 · 权限范围内查询 · 图表与结论一起返回</p>
        </div>
        <a-tag v-if="user" class="role-tag" :color="roleColor">{{ roleLabel }}</a-tag>
      </div>
    </template>

    <div v-if="!user" class="login-required">
      <div class="login-card">
        <span class="login-badge">需要登录</span>
        <h3>先登录，再看你的旅行数据</h3>
        <p>按账号角色自动限定可见范围，只返回你有权查看的统计与结论。</p>
        <a-button type="primary" size="large" class="login-btn" @click="$emit('request-login')">
          去登录
        </a-button>
      </div>
    </div>

    <div v-else class="assistant-layout">
      <section class="context-panel">
        <a-skeleton v-if="contextLoading" active :paragraph="{ rows: 2 }" />
        <template v-else>
          <div class="scope-row">
            <div>
              <small>数据范围</small>
              <strong>{{ capabilities?.scope_label || dataStatus?.scope_label || fallbackScope }}</strong>
            </div>
            <button type="button" class="refresh-button" @click="loadContext">刷新</button>
          </div>

          <div class="status-grid">
            <div class="status-item">
              <span>可见计划</span>
              <strong>{{ dataStatus?.visible_plans ?? '—' }}</strong>
            </div>
            <div class="status-item">
              <span>用户</span>
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

          <div v-if="displaySources.length" class="source-row">
            <span class="source-label">来源</span>
            <span
              v-for="source in displaySources"
              :key="source.source"
              class="source-chip"
            >
              {{ friendlySource(source.source) }} · {{ source.count }}
            </span>
          </div>

          <div v-if="friendlyEmptyHint" class="soft-hint">
            {{ friendlyEmptyHint }}
          </div>
        </template>
      </section>

      <div ref="chatStreamRef" class="chat-area">
        <div
          v-for="item in messages"
          :key="item.id"
          class="message"
          :class="item.role"
        >
          <div class="message-head">
            <strong>{{ item.role === 'user' ? '你' : item.agent || '灵途分析' }}</strong>
            <span v-if="item.intent">{{ intentLabel(item.intent) }}</span>
          </div>
          <p class="message-content">{{ cleanMessageContent(item.content) }}</p>

          <div v-if="item.permission && !item.permission.allowed" class="deny-note">
            {{ item.permission.reason }}
          </div>

          <div v-if="item.analysis" class="analysis-meta">
            <span class="meta-chip">{{ item.analysis.scope_label }}</span>
            <span class="meta-chip">{{ periodLabel(item.analysis.period) }}</span>
            <span class="meta-chip meta-chip--accent">
              样本 {{ item.analysis.sample_size }} 条
            </span>
          </div>

          <AgentChart v-if="item.chart" :option="item.chart" class="message-chart" />

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
        <div v-if="loading" class="thinking">
          <span class="thinking-dot" />
          正在理解问题并查询数据…
        </div>
      </div>

      <div class="assistant-tools">
        <div class="examples">
          <button
            v-for="prompt in quickPrompts.slice(0, 4)"
            :key="prompt"
            type="button"
            @click="ask(prompt)"
          >
            {{ prompt }}
          </button>
        </div>
      </div>

      <div class="composer">
        <a-textarea
          v-model:value="input"
          :rows="2"
          :placeholder="composerPlaceholder"
          class="composer-input"
          @press-enter.ctrl="send"
        />
        <div class="composer-actions">
          <a-input
            v-model:value="email"
            placeholder="发报告时填邮箱（可选）"
            class="email-input"
            allow-clear
          />
          <a-button type="primary" class="send-btn" :loading="loading" @click="send">
            分析
          </a-button>
        </div>
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
  type AgentDataStatus,
  type AgentPermission
} from '@/services/agentAnalytics'

type ChatItem = {
  id: number
  role: 'user' | 'assistant'
  content: string
  intent?: string
  agent?: string
  tool?: string
  table?: Record<string, unknown>[]
  chart?: Record<string, unknown> | null
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

/** Hide internal data-quality / synthetic caveats from the UI. */
const NOISY_WARNING_PATTERNS = [
  /完整率/,
  /结构化字段/,
  /模拟数据/,
  /生产经营/,
  /实际消费字段/,
  /完整行程内容/,
  /不能直接作为/,
  /仅使用计划预算/,
  /预测结果不可用/,
  /时间覆盖不足一年/,
  /样本或月份覆盖不足/
]

const input = ref('')
const email = ref('')
const loading = ref(false)
const contextLoading = ref(false)
const chatStreamRef = ref<HTMLElement | null>(null)
const capabilities = ref<AgentCapabilities | null>(null)
const dataStatus = ref<AgentDataStatus | null>(null)
const requestEpoch = ref(0)
const messages = ref<ChatItem[]>([
  {
    id: 1,
    role: 'assistant',
    content: '直接问目的地热度、预算趋势或个人画像。我会在你的权限范围内查询并给出图表与结论。'
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
  if (props.user?.role === 'admin') return '全站汇总与非敏感明细'
  if (props.user?.role === 'manager') return '全站匿名汇总'
  return '仅当前账号的旅行计划'
})

const fallbackPrompts: Record<string, string[]> = {
  user: ['分析我的旅行兴趣画像', '统计我本季度最常去的目的地', '展示我的月度预算趋势'],
  manager: ['统计本季度最热门的旅行目的地', '对比本月和去年同期的目的地热度', '预测下个月热门目的地'],
  admin: ['统计本季度所有人的旅行去向', '查看最近的智能分析审计日志', '统计本月目的地热度']
}

const quickPrompts = computed(() =>
  capabilities.value?.quick_prompts?.length
    ? capabilities.value.quick_prompts
    : fallbackPrompts[props.user?.role || 'user']
)

const coverageLabel = computed(() => {
  const range = dataStatus.value?.date_range
  if (!range?.min || !range?.max) return '暂无数据'
  return `${range.min} ~ ${range.max}`
})

const displaySources = computed(() =>
  (dataStatus.value?.sources || []).slice(0, 4)
)

const friendlyEmptyHint = computed(() => {
  const warnings = dataStatus.value?.warnings || []
  return warnings.find((item) => isFriendlyStatusHint(item)) || ''
})

const composerPlaceholder = computed(
  () => `以${roleLabel.value}身份提问，例如：${quickPrompts.value?.[0] || '统计旅行目的地'}`
)

function isNoisyWarning(text: string) {
  return NOISY_WARNING_PATTERNS.some((pattern) => pattern.test(text))
}

function isFriendlyStatusHint(text: string) {
  return !isNoisyWarning(text)
}

function cleanMessageContent(content: string) {
  if (!content) return content
  return content
    .split(/(?<=[。！？\n])/)
    .filter((part) => part.trim() && !isNoisyWarning(part))
    .join('')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function friendlySource(source: string) {
  const raw = String(source || '').toLowerCase()
  if (raw.includes('synthetic') || raw.includes('seed')) return '示例数据'
  if (raw.includes('generated') || raw.includes('primary')) return '生成计划'
  if (raw.includes('migrat')) return '迁移数据'
  if (!raw || raw === 'unknown') return '其他'
  return source
}

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
      content: '已切换账号会话。直接提问即可，我会按当前权限查询。'
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
      chart: response.chart,
      permission: response.permission,
      analysis: response.extra?.analysis
        ? {
            ...response.extra.analysis,
            warnings: (response.extra.analysis.warnings || []).filter(
              (item) => !isNoisyWarning(item)
            )
          }
        : undefined
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
  return `${period.label} · ${period.start} ~ ${period.end}`
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
.modal-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-right: 28px;
}

.modal-title__copy {
  min-width: 0;
}

.modal-kicker {
  display: inline-block;
  margin-bottom: 4px;
  color: #0f766e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.1em;
}

.modal-title strong {
  display: block;
  color: #0f172a;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.modal-title p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
}

.role-tag {
  margin-inline-end: 0;
  border-radius: 999px;
}

.login-required {
  min-height: 360px;
  display: grid;
  place-items: center;
  padding: 24px 20px 8px;
}

.login-card {
  width: min(440px, 100%);
  padding: 28px 24px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  border-radius: 22px;
  background:
    linear-gradient(155deg, rgba(255, 255, 255, 0.98), rgba(240, 253, 250, 0.88));
  text-align: center;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.06);
}

.login-badge {
  display: inline-flex;
  margin-bottom: 12px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.login-card h3 {
  margin: 0;
  color: #0f172a;
  font-size: 20px;
  font-weight: 800;
}

.login-card p {
  margin: 10px 0 18px;
  color: #64748b;
  font-size: 13px;
  line-height: 1.65;
}

.login-btn {
  min-width: 140px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(115deg, #0f766e, #0d9488);
  border: 0;
}

.assistant-layout {
  display: grid;
  gap: 14px;
  padding: 0 20px;
}

.context-panel {
  padding: 16px 18px;
  border: 1px solid rgba(15, 118, 110, 0.1);
  border-radius: 20px;
  background:
    linear-gradient(145deg, rgba(240, 253, 250, 0.95), rgba(248, 250, 252, 0.9));
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.04);
}

.scope-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.scope-row small {
  display: block;
  margin-bottom: 3px;
  color: #64748b;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.scope-row strong {
  color: #0f766e;
  font-size: 15px;
  font-weight: 800;
}

.refresh-button {
  flex: 0 0 auto;
  padding: 6px 10px;
  border: 1px solid rgba(15, 118, 110, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.8);
  color: #0f766e;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
}

.refresh-button:hover {
  background: #fff;
  border-color: rgba(15, 118, 110, 0.28);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(180px, 1.4fr);
  gap: 10px;
  margin-top: 14px;
}

.status-item {
  padding: 12px 12px 10px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.82);
}

.status-item span {
  display: block;
  margin-bottom: 4px;
  color: #94a3b8;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.03em;
}

.status-item strong {
  color: #0f172a;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.15;
}

.status-wide strong {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
  line-height: 1.45;
  color: #334155;
}

.source-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
}

.source-label {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 700;
}

.source-chip {
  display: inline-flex;
  padding: 3px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.16);
  color: #475569;
  font-size: 11px;
  font-weight: 600;
}

.soft-hint {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid rgba(15, 118, 110, 0.1);
  color: #0f766e;
  font-size: 12px;
  line-height: 1.55;
}

.chat-area {
  min-height: 300px;
  max-height: 46vh;
  overflow: auto;
  padding: 16px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 20px;
  background:
    linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(241, 245, 249, 0.88));
}

.message {
  max-width: 92%;
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.03);
}

.message.user {
  margin-left: auto;
  border-color: rgba(37, 99, 235, 0.14);
  background: linear-gradient(145deg, #eff6ff, #f8fbff);
}

.message-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.message-head strong {
  color: #0f172a;
  font-size: 13px;
  font-weight: 800;
}

.message-head span {
  color: #94a3b8;
  font-size: 11px;
  font-weight: 600;
}

.message-content {
  margin: 8px 0 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.deny-note {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(254, 243, 199, 0.65);
  color: #92400e;
  font-size: 12px;
  line-height: 1.5;
}

.analysis-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.meta-chip {
  display: inline-flex;
  padding: 3px 8px;
  border-radius: 999px;
  background: #f1f5f9;
  color: #475569;
  font-size: 11px;
  font-weight: 700;
}

.meta-chip--accent {
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
}

.message-chart {
  margin-top: 14px;
  border-radius: 16px;
  overflow: hidden;
}

.message-table {
  margin-top: 12px;
}

.thinking {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #0f766e;
  font-size: 13px;
  font-weight: 600;
}

.thinking-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #0f766e;
  animation: pulse 1.1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1); }
}

.assistant-tools {
  padding: 0 2px;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.examples button {
  padding: 7px 12px;
  border: 1px solid rgba(15, 118, 110, 0.14);
  border-radius: 999px;
  background: rgba(240, 253, 250, 0.9);
  color: #0f766e;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.examples button:hover {
  background: #fff;
  border-color: rgba(15, 118, 110, 0.28);
}

.composer {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.04);
}

.composer-input {
  border-radius: 12px;
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.email-input {
  flex: 1;
  min-width: 0;
}

.send-btn {
  min-width: 96px;
  height: 36px;
  border: 0;
  border-radius: 11px;
  background: linear-gradient(115deg, #0f766e, #0d9488);
  font-weight: 700;
}

@media (max-width: 760px) {
  .assistant-layout {
    padding: 0 14px;
  }

  .status-grid {
    grid-template-columns: 1fr 1fr;
  }

  .status-wide {
    grid-column: 1 / -1;
  }

  .composer-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .send-btn {
    width: 100%;
  }
}
</style>

<style>
/* Modal shell polish (unscoped, limited to ant modal class) */
.agent-modal .ant-modal-content {
  border-radius: 24px;
  overflow: hidden;
  box-shadow: 0 28px 80px rgba(15, 23, 42, 0.14);
}

.agent-modal .ant-modal-header {
  margin: 0;
  padding: 18px 22px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
  background:
    linear-gradient(180deg, rgba(240, 253, 250, 0.55), rgba(255, 255, 255, 0.95));
}

.agent-modal .ant-modal-body {
  background: #f8fafc;
}
</style>
