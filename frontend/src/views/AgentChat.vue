<template>
  <div class="agent-page">
    <section class="agent-header">
      <div>
        <span class="eyebrow">Multi-Agent Analytics</span>
        <h1>旅行画像智能分析</h1>
        <p>基于旅行计划数据，完成权限判断、SQL 查询、图表分析、画像推荐、预测和报告发送。支持上传文件进行智能分析。</p>
      </div>
      <div class="identity-panel">
        <a-input v-model:value="userId" addon-before="用户" />
        <a-select v-model:value="role" class="role-select">
          <a-select-option value="guest">guest</a-select-option>
          <a-select-option value="user">user</a-select-option>
          <a-select-option value="manager">manager</a-select-option>
          <a-select-option value="admin">admin</a-select-option>
        </a-select>
      </div>
    </section>

    <section class="agent-layout">
      <aside class="prompt-panel">
        <div class="panel-title">示例问题</div>
        <button v-for="item in quickPrompts" :key="item" type="button" @click="ask(item)">
          {{ item }}
        </button>
      </aside>

      <main class="chat-panel">
        <div class="chat-stream" ref="chatStreamRef">
          <div v-for="item in messages" :key="item.id" class="message" :class="item.role">
            <div class="message-meta">
              <strong>{{ item.role === 'user' ? '你' : item.agent || '灵途 Agent' }}</strong>
              <span v-if="item.intent">{{ item.intent }} · {{ item.tool }}</span>
              <span v-if="item.fileName" class="file-badge">📎 {{ item.fileName }}</span>
            </div>
            <p>{{ item.content }}</p>

            <a-alert
              v-if="item.permission && !item.permission.allowed"
              type="warning"
              show-icon
              :message="item.permission.reason"
            />

            <!-- File analysis suggestions -->
            <div v-if="item.suggestions && item.suggestions.length" class="suggestions-box">
              <strong>💡 改进建议</strong>
              <ul>
                <li v-for="(s, idx) in item.suggestions" :key="idx">{{ s }}</li>
              </ul>
            </div>

            <!-- Extracted info -->
            <a-descriptions
              v-if="item.extracted_info && Object.keys(item.extracted_info).length"
              size="small"
              bordered
              :column="2"
              class="extracted-info"
            >
              <a-descriptions-item
                v-for="(val, key) in item.extracted_info"
                :key="key"
                :label="String(key)"
              >
                {{ Array.isArray(val) ? val.join('、') || '—' : val || '—' }}
              </a-descriptions-item>
            </a-descriptions>

            <AgentChart v-if="item.chart" :option="item.chart" class="message-chart" />

            <a-table
              v-if="item.table && item.table.length"
              :columns="columnsFor(item.table)"
              :data-source="tableRows(item.table)"
              :pagination="{ pageSize: 6 }"
              size="small"
              class="message-table"
            />
          </div>
        </div>

        <div class="composer">
          <!-- File upload area -->
          <div class="upload-row">
            <a-upload
              :before-upload="handleBeforeUpload"
              :show-upload-list="false"
              accept=".txt,.md,.pdf,.docx,.xlsx,.xls"
            >
              <a-button size="small" :loading="uploading">
                <UploadOutlined />
                <span>{{ uploading ? '分析中...' : '上传文件分析' }}</span>
              </a-button>
            </a-upload>
            <span v-if="uploadFile" class="upload-hint">{{ uploadFile.name }}</span>
          </div>
          <a-input
            v-model:value="email"
            class="email-input"
            placeholder="发送邮件时填写收件人，可选"
          />
          <a-textarea
            v-model:value="input"
            :rows="3"
            placeholder="例如：和我相似的用户最喜欢去哪些城市？"
            @press-enter.ctrl="send"
          />
          <a-button type="primary" :loading="loading" @click="send">发送</a-button>
        </div>
      </main>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { message as toast } from 'ant-design-vue'
import { UploadOutlined } from '@ant-design/icons-vue'
import AgentChart from '@/components/AgentChart.vue'
import { chatAgentAnalysis, analyzeFile } from '@/services/api'
import type { AgentChatResponse, AgentPermission } from '@/types'

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
  suggestions?: string[]
  extracted_info?: Record<string, any>
  fileName?: string
}

const userId = ref('u_0001')
const role = ref<'guest' | 'user' | 'manager' | 'admin'>('user')
const input = ref('')
const email = ref('')
const loading = ref(false)
const uploading = ref(false)
const uploadFile = ref<File | null>(null)
const chatStreamRef = ref<HTMLElement | null>(null)
const messages = ref<ChatItem[]>([
  {
    id: 1,
    role: 'assistant',
    content: '可以问我热门目的地、预算趋势、你的旅行画像、相似用户推荐、预测和邮件报告。也可以上传旅行文件（PDF/DOCX/XLSX/TXT）让我分析。'
  }
])

const quickPrompts = [
  '统计最热门旅游城市',
  '分析我的旅行兴趣画像',
  '和我相似的用户最喜欢去哪些城市',
  '统计不同城市的平均预算',
  '预测下个月最热门的旅游城市',
  '查询所有用户手机号',
  '把我的画像报告发送到邮箱'
]

const ask = (text: string) => {
  input.value = text
  send()
}

const send = async () => {
  const text = input.value.trim()
  if (!text || loading.value) return
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: text
  })
  input.value = ''
  loading.value = true
  try {
    const response: AgentChatResponse = await chatAgentAnalysis({
      user_id: userId.value.trim() || 'u_current',
      role: role.value,
      message: text,
      email: email.value.trim() || null
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
    toast.error(error.message || '多智能体分析失败')
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

// ── File upload handler ────────────────────────────────────────────────

const handleBeforeUpload = async (file: File) => {
  uploadFile.value = file
  uploading.value = true

  // Show user message
  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: `上传文件「${file.name}」进行分析`,
    fileName: file.name
  })

  try {
    const result = await analyzeFile(
      file,
      input.value.trim() || '',
      userId.value.trim() || 'u_current',
      role.value
    )
    messages.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: result.summary,
      agent: 'FileAnalysisAgent',
      tool: 'file_analysis_tool',
      intent: 'file_analysis',
      table: result.table,
      suggestions: result.suggestions,
      extracted_info: result.extracted_info,
      fileName: file.name
    })
    input.value = ''
  } catch (error: any) {
    toast.error(error.message || '文件分析失败')
  } finally {
    uploading.value = false
    uploadFile.value = null
    scrollToBottom()
  }

  // Prevent default upload behavior
  return false
}

const scrollToBottom = () => {
  if (chatStreamRef.value) {
    chatStreamRef.value.scrollTop = chatStreamRef.value.scrollHeight
  }
}

const columnsFor = (rows: Record<string, any>[]) => {
  if (!rows.length) return []
  return Object.keys(rows[0]).map(key => ({
    title: key,
    dataIndex: key,
    key,
    ellipsis: true
  }))
}

const tableRows = (rows: Record<string, any>[]) => {
  return rows.map((row, index) => ({ ...row, key: index }))
}
</script>

<style scoped>
.agent-page {
  min-height: calc(100vh - 112px);
  padding: 32px 24px 52px;
  background: #f7faf9;
}

.agent-header {
  width: min(1280px, 100%);
  margin: 0 auto 20px;
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: end;
}

.eyebrow {
  color: #0f766e;
  font-size: 13px;
  font-weight: 800;
}

.agent-header h1 {
  margin: 8px 0;
  color: #172033;
  font-size: 34px;
  line-height: 1.15;
  letter-spacing: 0;
}

.agent-header p {
  margin: 0;
  color: #667085;
}

.identity-panel {
  width: 360px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px;
  gap: 10px;
}

.role-select {
  width: 120px;
}

.agent-layout {
  width: min(1280px, 100%);
  margin: 0 auto;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 18px;
}

.prompt-panel,
.chat-panel {
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.07);
}

.prompt-panel {
  padding: 18px;
}

.panel-title {
  margin-bottom: 12px;
  color: #172033;
  font-weight: 800;
}

.prompt-panel button {
  width: 100%;
  margin-bottom: 8px;
  padding: 9px 10px;
  border: 1px solid #d9e2df;
  border-radius: 8px;
  background: #ffffff;
  color: #475467;
  text-align: left;
  cursor: pointer;
}

.prompt-panel button:hover {
  border-color: #0f766e;
  color: #0f766e;
  background: #f0fdfa;
}

.chat-panel {
  min-height: 680px;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  overflow: hidden;
}

.chat-stream {
  padding: 18px;
  overflow: auto;
}

.message {
  max-width: 92%;
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #fbfdfc;
}

.message.user {
  margin-left: auto;
  background: #0f766e;
  color: #ffffff;
}

.message-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.message-meta span {
  color: #667085;
}

.file-badge {
  background: rgba(15, 118, 110, 0.12);
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.message.user .message-meta span {
  color: rgba(255, 255, 255, 0.75);
}

.message p {
  margin: 0 0 10px;
  line-height: 1.65;
}

.suggestions-box {
  margin: 10px 0;
  padding: 10px 12px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
}

.suggestions-box ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.suggestions-box li {
  margin-bottom: 4px;
  color: #92400e;
}

.extracted-info {
  margin: 10px 0;
}

.message-chart,
.message-table {
  margin-top: 12px;
}

.composer {
  padding: 14px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px;
  gap: 10px;
  border-top: 1px solid #dce8e4;
  background: #ffffff;
}

.upload-row {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 2px;
}

.upload-hint {
  font-size: 13px;
  color: #667085;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.email-input {
  grid-column: 1 / -1;
}

.composer button {
  height: 78px;
  border-radius: 8px;
  font-weight: 700;
  background: #0f766e;
  border-color: #0f766e;
}

@media (max-width: 820px) {
  .agent-page {
    padding: 22px 12px 40px;
  }

  .agent-header,
  .agent-layout {
    grid-template-columns: 1fr;
  }

  .agent-header {
    flex-direction: column;
    align-items: stretch;
  }

  .identity-panel {
    width: 100%;
    grid-template-columns: 1fr;
  }

  .role-select {
    width: 100%;
  }

  .chat-panel {
    min-height: 600px;
  }

  .message {
    max-width: 100%;
  }
}
</style>
