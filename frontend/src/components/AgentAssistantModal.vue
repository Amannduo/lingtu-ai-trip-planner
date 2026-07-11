<template>
  <a-modal
    :open="open"
    width="920px"
    :footer="null"
    class="agent-modal"
    @cancel="$emit('close')"
  >
    <template #title>
      <div class="modal-title">
        <span>智能旅行分析</span>
        <small v-if="user">{{ user.username }} · {{ roleLabel }}</small>
      </div>
    </template>

    <div v-if="!user" class="login-required">
      <h3>登录后使用你的旅行画像</h3>
      <p>系统会结合你的角色和历史旅行记录，返回表格、图表和中文结论。</p>
      <a-button type="primary" @click="$emit('request-login')">去登录</a-button>
    </div>

    <div v-else class="assistant-layout">
      <div ref="chatStreamRef" class="chat-area">
        <div v-for="item in messages" :key="item.id" class="message" :class="item.role">
          <div class="message-head">
            <strong>{{ item.role === 'user' ? '你' : item.agent || '灵途助手' }}</strong>
            <span v-if="item.intent">{{ item.intent }} · {{ item.tool }}</span>
          </div>
          <p>{{ item.content }}</p>

          <a-alert
            v-if="item.permission && !item.permission.allowed"
            type="warning"
            show-icon
            :message="item.permission.reason"
          />

          <AgentChart v-if="item.chart" :option="item.chart" class="message-chart" />

          <a-table
            v-if="item.table && item.table.length"
            :columns="columnsFor(item.table)"
            :data-source="tableRows(item.table)"
            :pagination="{ pageSize: 5 }"
            size="small"
            class="message-table"
          />
        </div>
      </div>

      <div class="assistant-tools">
        <button type="button" class="example-toggle" @click="showExamples = !showExamples">
          {{ showExamples ? '收起示例问题' : '需要一点灵感？' }}
        </button>
        <div v-if="showExamples" class="examples">
          <button v-for="item in quickPrompts" :key="item" type="button" @click="ask(item)">
            {{ item }}
          </button>
        </div>
      </div>

      <div class="composer">
        <a-input
          v-model:value="email"
          placeholder="发送报告时填写邮箱，可选"
          class="email-input"
        />
        <a-textarea
          v-model:value="input"
          :rows="3"
          placeholder="问我：分析我的旅行兴趣画像，或统计最热门旅游城市"
          @press-enter.ctrl="send"
        />
        <a-button type="primary" :loading="loading" @click="send">发送</a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { message as toast } from 'ant-design-vue'
import AgentChart from '@/components/AgentChart.vue'
import { chatAgentAnalysis } from '@/services/api'
import type { AgentChatResponse, AgentPermission } from '@/types'
import type { LocalUser } from '@/services/auth'

type ChatItem = {
  id: number
  role: 'user' | 'assistant'
  content: string
  intent?: string
  agent?: string
  tool?: string
  table?: Record<string, any>[]
  chart?: Record<string, any> | null
  permission?: AgentPermission
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
const showExamples = ref(false)
const chatStreamRef = ref<HTMLElement | null>(null)
const messages = ref<ChatItem[]>([
  {
    id: 1,
    role: 'assistant',
    content: '我可以帮你分析旅行画像、热门目的地、预算趋势、相似用户推荐和预测结果。'
  }
])

const roleLabel = computed(() => {
  const labels = {
    guest: '访客',
    user: '普通用户',
    manager: '经理',
    admin: '管理员'
  }
  return props.user ? labels[props.user.role] : ''
})

const quickPrompts = [
  '统计最热门旅游城市',
  '分析我的旅行兴趣画像',
  '和我相似的用户最喜欢去哪些城市',
  '统计不同城市的平均预算',
  '预测下个月最热门的旅游城市',
  '查询所有用户手机号',
  '把我的画像报告发送到邮箱'
]

watch(() => props.open, value => {
  if (value) {
    nextTick(scrollToBottom)
  }
})

function ask(prompt: string) {
  input.value = prompt
  send()
}

async function send() {
  if (!props.user) {
    toast.warning('请先登录')
    return
  }
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: text
  })
  input.value = ''
  loading.value = true
  await nextTick(scrollToBottom)

  try {
    const response: AgentChatResponse = await chatAgentAnalysis({
      user_id: props.user.user_id,
      role: props.user.role,
      message: text,
      email: email.value.trim() || undefined
    })

    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: response.result,
      intent: response.intent,
      agent: response.agent,
      tool: response.tool,
      table: response.table,
      chart: response.chart,
      permission: response.permission
    })
  } catch (error: any) {
    toast.error(error.message || '智能分析失败')
  } finally {
    loading.value = false
    await nextTick(scrollToBottom)
  }
}

function columnsFor(rows: Record<string, any>[]) {
  return Object.keys(rows[0] || {}).map(key => ({
    title: key,
    dataIndex: key,
    key,
    ellipsis: true
  }))
}

function tableRows(rows: Record<string, any>[]) {
  return rows.map((row, index) => ({ ...row, key: index }))
}

function scrollToBottom() {
  const el = chatStreamRef.value
  if (el) el.scrollTop = el.scrollHeight
}
</script>

<style scoped>
.modal-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.modal-title span {
  font-size: 18px;
  font-weight: 700;
  color: #172033;
}

.modal-title small {
  font-size: 13px;
  color: #667085;
}

.login-required {
  min-height: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
}

.login-required h3 {
  margin: 0;
  font-size: 22px;
  color: #172033;
}

.login-required p {
  max-width: 420px;
  margin: 0 0 8px;
  color: #667085;
}

.assistant-layout {
  display: grid;
  grid-template-rows: minmax(320px, 52vh) auto auto;
  gap: 14px;
}

.chat-area {
  overflow: auto;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #f8fafc;
}

.message {
  max-width: 92%;
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #ffffff;
}

.message.user {
  margin-left: auto;
  border-color: #bedbff;
  background: #eff6ff;
}

.message-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.message-head strong {
  color: #172033;
}

.message-head span {
  font-size: 12px;
  color: #667085;
}

.message p {
  margin: 0 0 10px;
  line-height: 1.7;
  color: #344054;
}

.message-chart,
.message-table {
  margin-top: 12px;
}

.assistant-tools {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.example-toggle {
  align-self: flex-start;
  border: 0;
  background: transparent;
  padding: 0;
  color: #2563eb;
  cursor: pointer;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.examples button {
  border: 1px solid #d0d5dd;
  border-radius: 999px;
  background: #ffffff;
  color: #344054;
  padding: 7px 12px;
  cursor: pointer;
}

.examples button:hover {
  border-color: #2563eb;
  color: #2563eb;
}

.composer {
  display: grid;
  grid-template-columns: minmax(160px, 220px) 1fr auto;
  gap: 10px;
  align-items: stretch;
}

.email-input {
  align-self: start;
}

@media (max-width: 760px) {
  .assistant-layout {
    grid-template-rows: minmax(300px, 55vh) auto auto;
  }

  .composer {
    grid-template-columns: 1fr;
  }

  .message {
    max-width: 100%;
  }
}
</style>
