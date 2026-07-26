<template>
  <div class="home-container">
    <section class="planner-shell">
      <div class="planner-intro">
        <div class="intro-copy">
          <div class="eyebrow">
            <CompassOutlined />
            <span>灵途 AI</span>
          </div>
          <h1>说一句，AI 帮你决定去哪</h1>
          <p>不用先研究目的地和攻略，描述你想要的旅行感觉就够了。</p>
        </div>

      </div>

      <div class="planning-grid">
        <a-card v-if="formCardVisible" class="form-card" :bordered="false">
          <div class="form-card-heading">
            <div>
              <span class="step-kicker">最后确认</span>
              <h2>确认必要信息</h2>
              <p>
                {{
                  hasFormInsight
                    ? 'AI 发现几处还需你点头的细节，核对后即可生成。'
                    : '偏好已整理好，确认目的地和日期就能出发规划。'
                }}
              </p>
            </div>
            <span
              class="completion-state"
              :class="{
                ready: planningReady && !hasFormInsight,
                warn: hasFormInsight
              }"
            >
              {{ formStatusLabel }}
            </span>
          </div>

          <div
            v-if="hasFormInsight"
            class="insight-panel insight-panel--form"
            role="status"
          >
            <div class="insight-panel__head">
              <span class="insight-panel__badge">智能核对</span>
              <span class="insight-panel__title">生成前再确认一下</span>
            </div>

            <div v-if="unresolvedPendingLabels.length" class="insight-block">
              <span class="insight-label">待确认</span>
              <div class="insight-chips">
                <span
                  v-for="label in unresolvedPendingLabels"
                  :key="label"
                  class="insight-chip"
                >{{ label }}</span>
              </div>
            </div>

            <div v-if="activeConflictMessages.length" class="insight-block">
              <span class="insight-label">需对齐</span>
              <p class="insight-text">{{ activeConflictMessages[0] }}</p>
              <span
                v-if="activeConflictMessages.length > 1"
                class="insight-more"
              >另有 {{ activeConflictMessages.length - 1 }} 条</span>
            </div>

            <div
              v-for="(issue, index) in serverGateIssues.slice(0, 3)"
              :key="`${issue.code || 'issue'}-${index}`"
              class="insight-block insight-block--server"
            >
              <span class="insight-label">{{ serverIssueTitle(issue) }}</span>
              <p class="insight-text">{{ issue.message }}</p>
              <p v-if="issue.suggestion" class="insight-hint">{{ issue.suggestion }}</p>
            </div>

            <p class="insight-foot">
              {{
                serverGateIssues.length
                  ? '请修改上方标记的字段以匹配你的意图，或确认无误后点击「生成我的可执行行程」。'
                  : '修改后会自动刷新校验结果；留有空缺提交时 AI 会再向你确认一次。'
              }}
            </p>
          </div>

          <a-form :model="formData" layout="vertical" @finish="handleSubmit">
          <div class="form-section">
            <div class="section-header">
              <EnvironmentOutlined />
              <span>行程基本信息</span>
            </div>

            <a-row :gutter="[16, 16]">
              <a-col :xs="24" :md="8">
                <a-form-item name="origin_city">
                  <template #label>
                    <span class="form-label">出发城市 <em>可选</em></span>
                  </template>
                  <a-input
                    v-model:value="formData.origin_city"
                    placeholder="例如: 上海"
                    size="large"
                    class="custom-input"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :md="8">
                <a-form-item name="city" :rules="[{ required: true, message: '请输入目的地城市' }]">
                  <template #label>
                    <span class="form-label">目的地城市</span>
                  </template>
                  <a-input
                    v-model:value="formData.city"
                    placeholder="例如: 北京"
                    size="large"
                    class="custom-input"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :md="6">
                <a-form-item name="start_date" :rules="[{ required: true, message: '请选择开始日期' }]">
                  <template #label>
                    <span class="form-label">开始日期</span>
                  </template>
                  <a-date-picker
                    v-model:value="formData.start_date"
                    style="width: 100%"
                    size="large"
                    class="custom-input"
                    :disabled-date="disabledStartDate"
                    placeholder="选择日期"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :md="6">
                <a-form-item name="end_date" :rules="[{ required: true, message: '请选择结束日期' }]">
                  <template #label>
                    <span class="form-label">结束日期</span>
                  </template>
                  <a-date-picker
                    v-model:value="formData.end_date"
                    style="width: 100%"
                    size="large"
                    class="custom-input"
                    :disabled-date="disabledEndDate"
                    placeholder="选择日期"
                  />
                </a-form-item>
              </a-col>

              <a-col v-if="weekendDateHint" :xs="24" :md="24">
                <p class="date-pending-hint">{{ weekendDateHint }}</p>
              </a-col>

              <a-col :xs="24" :md="4">
                <a-form-item>
                  <template #label>
                    <span class="form-label">旅行天数</span>
                  </template>
                  <div class="days-display">
                    <strong>{{ formData.travel_days }}</strong>
                    <span>天</span>
                  </div>
                </a-form-item>
              </a-col>
            </a-row>
          </div>

          <button type="button" class="advanced-toggle" @click="advancedOpen = !advancedOpen">
            <span>
              <SettingOutlined />
              {{ advancedOpen ? '收起高级设置' : '调整预算、交通与特殊需求' }}
            </span>
            <DownOutlined :class="{ rotated: advancedOpen }" />
          </button>

          <div v-show="advancedOpen" class="advanced-content">
          <div class="form-section">
            <div class="section-header">
              <SettingOutlined />
              <span>偏好设置</span>
            </div>

            <a-row :gutter="[16, 16]">
              <a-col :xs="24" :lg="8">
                <a-form-item name="transportation">
                  <template #label>
                    <span class="form-label">交通方式</span>
                  </template>
                  <a-segmented
                    v-model:value="formData.transportation"
                    :options="transportationOptions"
                    block
                    size="large"
                    class="mode-control"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :lg="5">
                <a-form-item name="intercity_transportation">
                  <template #label>
                    <span class="form-label">城际交通</span>
                  </template>
                  <a-select v-model:value="formData.intercity_transportation" size="large" class="custom-select">
                    <a-select-option v-for="item in intercityTransportationOptions" :key="item" :value="item">
                      {{ item }}
                    </a-select-option>
                  </a-select>
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :lg="5">
                <a-form-item name="accommodation">
                  <template #label>
                    <span class="form-label">住宿偏好</span>
                  </template>
                  <a-select v-model:value="formData.accommodation" size="large" class="custom-select">
                    <a-select-option v-for="item in accommodationOptions" :key="item" :value="item">
                      {{ item }}
                    </a-select-option>
                  </a-select>
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :lg="5">
                <a-form-item name="budget">
                  <template #label>
                    <span class="form-label">总预算(元)</span>
                  </template>
                  <a-input-number
                    v-model:value="formData.budget"
                    :min="0"
                    :step="500"
                    :precision="0"
                    placeholder="例如: 3000"
                    size="large"
                    class="budget-input"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :sm="12" :lg="4">
                <a-form-item name="travelers">
                  <template #label>
                    <span class="form-label">出行人数</span>
                  </template>
                  <a-input-number
                    v-model:value="formData.travelers"
                    :min="1"
                    :max="20"
                    :precision="0"
                    size="large"
                    class="budget-input"
                    @change="markTravelersConfirmed"
                  />
                </a-form-item>
              </a-col>

              <a-col :xs="24" :lg="6">
                <a-form-item name="preferences">
                  <template #label>
                    <span class="form-label">旅行偏好</span>
                  </template>
                  <a-checkbox-group v-model:value="formData.preferences" class="preference-grid">
                    <a-checkbox value="历史文化">历史文化</a-checkbox>
                    <a-checkbox value="自然风光">自然风光</a-checkbox>
                    <a-checkbox value="美食">美食</a-checkbox>
                    <a-checkbox value="购物">购物</a-checkbox>
                    <a-checkbox value="艺术">艺术</a-checkbox>
                    <a-checkbox value="休闲">休闲</a-checkbox>
                  </a-checkbox-group>
                </a-form-item>
              </a-col>
            </a-row>
          </div>

          <div class="form-section compact">
            <div class="section-header">
              <MessageOutlined />
              <span>额外要求</span>
            </div>

            <a-form-item name="free_text_input">
              <a-textarea
                v-model:value="formData.free_text_input"
                placeholder="例如: 想去看升旗、需要无障碍设施、对海鲜过敏等"
                :auto-size="{ minRows: 4, maxRows: 12 }"
                :maxlength="2000"
                size="large"
                class="custom-textarea"
              />
            </a-form-item>
          </div>

          <div class="delivery-settings">
            <div class="delivery-channel">
              <div class="delivery-toggle">
                <span>
                  <MailOutlined />
                  生成后发送邮件
                </span>
                <a-switch v-model:checked="emailOnCompletion" :disabled="!currentUser" />
              </div>
              <a-input
                v-if="emailOnCompletion"
                v-model:value="deliveryEmail"
                type="email"
                autocomplete="email"
                :placeholder="currentUser?.email || '收件邮箱'"
                class="delivery-email"
              />
            </div>

            <div class="delivery-channel">
              <div class="delivery-toggle">
                <span>
                  <BellOutlined />
                  桌面提醒
                </span>
                <a-switch
                  :checked="desktopNotification"
                  :loading="pushBusy"
                  :disabled="pushBusy || !currentUser || !pushSupported || notificationPermission === 'denied'"
                  @change="handleDesktopNotificationChange"
                />
              </div>
              <div class="delivery-status" :class="`is-${notificationStatusTone}`">
                <span class="delivery-status-dot"></span>
                {{ notificationStatusText }}
              </div>
            </div>
          </div>
          </div>

          <div class="action-row">
            <a-button
              type="primary"
              html-type="submit"
              :loading="loading"
              size="large"
              class="submit-button"
            >
              <template v-if="!loading">
                <RocketOutlined />
                <span>生成我的可执行行程</span>
              </template>
              <template v-else>
                <span>正在生成</span>
              </template>
            </a-button>
          </div>

          <!-- 旅行历史 -->
          <a-alert
            v-if="currentUser && historyLoadError"
            class="history-error"
            type="warning"
            show-icon
            :message="historyLoadError"
          />
          <div v-if="currentUser && history.stats.total_trips > 0" class="history-section">
            <div class="section-header">
              <HistoryOutlined />
              <span>我的旅行历史</span>
              <a-tag color="green">{{ history.stats.total_trips }} 次行程</a-tag>
              <a-tag color="blue">均预算 ¥{{ history.stats.avg_budget }}</a-tag>
              <a-tag color="orange">{{ history.stats.total_days }} 天</a-tag>
              <span v-if="history.fav_cities.length" style="font-size:13px;color:#667085;margin-left:8px">
                常去: {{ history.fav_cities.map(c => c.city).join('、') }}
              </span>
            </div>
            <div class="history-list">
              <button
                v-for="trip in history.trips.slice(0, 6)"
                :key="trip.plan_no"
                type="button"
                class="history-card"
                :disabled="!trip.has_detail"
                @click="openHistoryTrip(trip.plan_no)"
              >
                <strong>{{ trip.destination }}</strong>
                <span>{{ trip.start_date }} ~ {{ trip.end_date }}</span>
                <span>{{ trip.travel_days }}天 · {{ trip.transportation }}</span>
                <span v-if="trip.budget">¥{{ trip.budget }}</span>
                <span class="history-open">{{ trip.has_detail ? '查看计划 →' : '旧记录仅摘要' }}</span>
              </button>
            </div>
          </div>

          <section v-if="loading" class="agent-workflow" role="status" aria-live="polite">
            <div class="workflow-head">
              <div class="workflow-orb" aria-hidden="true"><span></span></div>
              <div>
                <span class="workflow-kicker">LINGTU AGENT WORKFLOW</span>
                <h3>{{ loadingStatus }}</h3>
                <p>{{ loadingDetail || '正在建立本次旅行的约束与决策上下文。' }}</p>
              </div>
              <strong>{{ loadingProgress }}%</strong>
            </div>
            <a-progress
              :percent="loadingProgress"
              :show-info="false"
              status="active"
              :stroke-color="{ '0%': '#0f766e', '100%': '#2563eb' }"
              :stroke-width="6"
            />
            <div v-if="loadingEvents.length" class="workflow-trace">
              <div
                v-for="event in loadingEvents.slice(-4)"
                :key="event.stage"
                class="trace-item"
              >
                <CheckOutlined />
                <span>
                  <strong>{{ event.message }}</strong>
                  <small v-if="event.detail">{{ event.detail }}</small>
                </span>
              </div>
            </div>
            <div class="workflow-foot">
              显示的是工具执行与业务校验结果，不展示模型内部思维过程
            </div>
          </section>
          </a-form>
        </a-card>

        <aside class="assistant-panel">
          <div class="assistant-header">
            <div>
              <div class="assistant-title">
                <BulbOutlined />
                <span>先告诉我，你想怎么旅行？</span>
              </div>
              <p>目的地可以不知道，预算也可以不确定。AI 会结合距离、节奏、预算和地图数据给出三个不同方向。</p>
            </div>
          </div>

          <div class="journey-steps" aria-label="规划步骤">
            <span class="active"><b>1</b>描述愿望</span>
            <i></i>
            <span :class="{ active: recommendations.length > 0 || Boolean(formData.city) }"><b>2</b>比较方案</span>
            <i></i>
            <span :class="{ active: planningReady }"><b>3</b>确认行程</span>
          </div>

          <div v-if="latestAssistantReply" class="intent-summary">
            <div class="intent-summary__icon" aria-hidden="true">
              <BulbOutlined />
            </div>
            <div class="intent-summary__body">
              <span class="intent-summary__kicker">AI 理解</span>
              <p>{{ latestAssistantReply }}</p>
            </div>
          </div>

          <!--
            Pending items and conflicts live in one place only: the form card,
            next to the fields that resolve them. A second copy here made the
            same warning look like two separate problems.
          -->
          <p v-if="showInsightPointer" class="insight-pointer">
            {{
              serverGateIssues.length
                ? '服务端拦住了这一步，请在「确认必要信息」卡片中处理标记项。'
                : `还有 ${insightItemCount} 项待确认，见「确认必要信息」卡片。`
            }}
          </p>

          <div class="quick-prompts">
            <button
              v-for="prompt in quickPrompts"
              :key="prompt"
              type="button"
              @click="askQuickPrompt(prompt)"
            >
              {{ prompt }}
            </button>
          </div>

          <a-textarea
            v-model:value="assistantInput"
            placeholder="例如：这个周末两个人从上海出去透透气，不想太累，预算别太高。"
            :rows="4"
            class="assistant-input"
            @keydown.ctrl.enter.prevent="sendAssistantMessage()"
          />

          <a-button
            type="primary"
            class="assistant-send"
            :loading="assistantLoading"
            block
            @click="sendAssistantMessage()"
          >
            <SendOutlined />
            <span>让 AI 给我 3 个方案</span>
          </a-button>

          <div v-if="recommendations.length" class="recommendation-section">
            <div class="recommendation-title-row">
              <div>
                <span class="step-kicker">AI 初选</span>
                <h3>三个方向，各有取舍</h3>
              </div>
              <span>选择后仍可修改</span>
            </div>
            <div class="recommendation-list">
            <div
              v-for="(item, index) in recommendations"
              :key="`${item.city}-${item.schedule_option || 'default'}-${index}`"
              class="recommendation-card"
              :class="{
                selected: selectedCity === item.city && selectedScheduleOption === (item.schedule_option || 'default'),
                'is-friday-early': item.schedule_option === 'friday_early'
              }"
            >
              <span class="option-label">方案 {{ String.fromCharCode(65 + index) }} · {{ item.decision_label }}</span>
              <div class="recommendation-head">
                <span class="city-name">{{ item.city }}</span>
                <span class="budget-fit">{{ item.budget_fit }}</span>
              </div>

              <div class="decision-metrics">
                <span><b>{{ item.suggested_days }}</b> 天</span>
                <span><b>{{ item.pace }}</b> 节奏</span>
                <span v-if="item.estimated_budget"><b>¥{{ item.estimated_budget }}</b> 预计</span>
              </div>

              <p v-if="item.schedule_summary" class="schedule-summary">{{ item.schedule_summary }}</p>
              <p
                v-if="item.early_arrival_hint && item.schedule_option !== 'friday_early'"
                class="early-arrival-hint"
              >{{ item.early_arrival_hint }}</p>

              <p class="recommendation-reason">{{ item.reason }}</p>
              <p v-if="item.tradeoff" class="tradeoff-note">取舍：{{ item.tradeoff }}</p>
              <p v-if="item.origin_note" class="origin-note">{{ item.origin_note }}</p>

              <div v-if="item.highlights.length" class="highlight-row">
                <span v-for="highlight in item.highlights" :key="highlight">{{ highlight }}</span>
              </div>

              <p v-if="item.weather_summary" class="weather-note">{{ item.weather_summary }}</p>

              <a-button class="use-recommendation" :type="selectedCity === item.city && selectedScheduleOption === (item.schedule_option || 'default') ? 'primary' : 'default'" block @click="useRecommendation(item)">
                <CheckOutlined />
                <span>{{
                  selectedCity === item.city && selectedScheduleOption === (item.schedule_option || 'default')
                    ? '已采用这个方案'
                    : (item.schedule_option === 'friday_early' ? '采用周五出发方案' : '采用并继续')
                }}</span>
              </a-button>
            </div>
            </div>
          </div>
        </aside>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref, reactive, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  BellOutlined,
  BulbOutlined,
  CheckOutlined,
  CompassOutlined,
  DownOutlined,
  EnvironmentOutlined,
  HistoryOutlined,
  MailOutlined,
  MessageOutlined,
  RocketOutlined,
  SendOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import {
  ApiClientError,
  chatDestinationRecommendation,
  fetchTripHistory,
  fetchTripPlan,
  generateTripPlanWithProgress,
  type ApiIssue
} from '@/services/api'
import {
  getExistingPushSubscription,
  getPushPermissionState,
  isPushSupported,
  subscribeToPush,
  syncExistingPushSubscription,
  unsubscribeFromPush,
  type PushPermissionState
} from '@/services/pushNotifications'
import { getCurrentUser, type LocalUser } from '@/services/auth'
import { saveTripCache, saveTripSession } from '@/services/tripCache'
import type {
  ChatMessage,
  DestinationRecommendation,
  SemanticTripContract,
  TripFormData
} from '@/types'
import dayjs, { type Dayjs } from 'dayjs'

type PlannerFormData = Omit<TripFormData, 'start_date' | 'end_date'> & {
  start_date: Dayjs | null
  end_date: Dayjs | null
}

const router = useRouter()
const loading = ref(false)
const loadingProgress = ref(0)
const loadingStatus = ref('')
const loadingDetail = ref('')
const loadingEvents = ref<Array<{
  stage: string
  message: string
  detail?: string
}>>([])
const assistantInput = ref('')
const assistantLoading = ref(false)
const recommendationCount = ref(3)
const advancedOpen = ref(false)
const selectedCity = ref('')
const selectedDestinationSource = ref<'manual' | 'recommendation'>('manual')
const currentUser = ref<LocalUser | null>(getCurrentUser())
let assistantRequestVersion = 0
let historyDetailRequestVersion = 0
let generationRequestVersion = 0
const currentOwnerId = () => currentUser.value?.user_id || null
const emailOnCompletion = ref(false)
const pushBusy = ref(false)
const pushSupported = isPushSupported()
const notificationPermission = ref<PushPermissionState>(getPushPermissionState())
const deliveryEmail = ref(currentUser.value?.email || '')
const desktopNotification = ref(false)
const createInitialAssistantMessages = (): ChatMessage[] => [
  {
    role: 'assistant',
    content: '不确定去哪也没关系。告诉我预算、天数和你想要的旅行感觉,我会给你几个可选目的地。'
  }
]
const assistantMessages = ref<ChatMessage[]>(createInitialAssistantMessages())
const notificationStatusText = computed(() => {
  if (!pushSupported) {
    return '\u5f53\u524d\u73af\u5883\u4e0d\u652f\u6301 Web Push\uff08\u751f\u4ea7\u73af\u5883\u9700 HTTPS\uff09'
  }
  if (!currentUser.value) return '\u767b\u5f55\u540e\u53ef\u5f00\u542f\u540e\u53f0\u901a\u77e5'
  if (notificationPermission.value === 'denied') {
    return '\u6743\u9650\uff1a\u5df2\u62d2\u7edd\uff08\u8bf7\u5728\u6d4f\u89c8\u5668\u7f51\u7ad9\u8bbe\u7f6e\u4e2d\u91cd\u65b0\u5141\u8bb8\uff09'
  }
  if (notificationPermission.value === 'default') {
    return '\u6743\u9650\uff1a\u672a\u51b3\u5b9a'
  }
  return desktopNotification.value
    ? '\u6743\u9650\uff1a\u5df2\u5141\u8bb8 \u00b7 \u540e\u53f0\u63a8\u9001\u5df2\u8ba2\u9605'
    : '\u6743\u9650\uff1a\u5df2\u5141\u8bb8 \u00b7 \u5c1a\u672a\u8ba2\u9605'
})

const notificationStatusTone = computed(() => {
  if (desktopNotification.value) return 'success'
  if (notificationPermission.value === 'denied') return 'warning'
  return 'muted'
})

const recommendations = ref<DestinationRecommendation[]>([])
const createEmptyHistory = () => ({
  stats: { total_trips: 0, avg_budget: 0, total_days: 0 },
  fav_cities: [] as { city: string; count: number }[],
  trips: [] as any[]
})

const disabledStartDate = (value: Dayjs): boolean => (
  value.startOf('day').isBefore(dayjs().startOf('day'))
)

const disabledEndDate = (value: Dayjs): boolean => {
  if (disabledStartDate(value)) return true
  const start = formData.start_date
  if (!start) return false
  const offset = value.startOf('day').diff(start.startOf('day'), 'day')
  return offset < 0 || offset >= 30
}

const history = ref(createEmptyHistory())
const historyLoadError = ref('')

const loadHistory = async () => {
  const requestedUserId = currentUser.value?.user_id || null
  if (!requestedUserId) {
    history.value = createEmptyHistory()
    historyLoadError.value = ''
    return
  }
  try {
    const response = await fetchTripHistory()
    if (currentUser.value?.user_id !== requestedUserId) return
    if (response.user_id && response.user_id !== requestedUserId) {
      throw new Error('\u5386\u53f2\u54cd\u5e94\u4e0e\u5f53\u524d\u8d26\u53f7\u4e0d\u5339\u914d')
    }
    history.value = response
    historyLoadError.value = ''
  } catch (error: any) {
    if (currentUser.value?.user_id !== requestedUserId) return
    history.value = createEmptyHistory()
    historyLoadError.value = error?.message || '\u8bfb\u53d6\u65c5\u884c\u5386\u53f2\u5931\u8d25'
  }
}

const refreshPushState = async () => {
  notificationPermission.value = getPushPermissionState()
  if (
    !pushSupported
    || notificationPermission.value !== 'granted'
    || !currentUser.value
  ) {
    desktopNotification.value = false
    return
  }
  try {
    desktopNotification.value = Boolean(await getExistingPushSubscription())
  } catch (error) {
    desktopNotification.value = false
    console.warn('[push] Failed to read browser subscription:', error)
  }
}

const syncAuth = () => {
  const previousUserId = currentOwnerId()
  currentUser.value = getCurrentUser()
  const nextUserId = currentOwnerId()
  if (previousUserId !== nextUserId) {
    assistantRequestVersion += 1
    historyDetailRequestVersion += 1
    generationRequestVersion += 1
    assistantMessages.value = createInitialAssistantMessages()
    assistantInput.value = ''
    assistantLoading.value = false
    recommendations.value = []
    selectedCity.value = ''
    selectedDestinationSource.value = 'manual'
    loading.value = false
    loadingProgress.value = 0
    loadingStatus.value = ''
    loadingDetail.value = ''
    loadingEvents.value = []
  }
  deliveryEmail.value = currentUser.value?.email || ''
  if (!currentUser.value) {
    emailOnCompletion.value = false
    desktopNotification.value = false
    void unsubscribeFromPush(false).catch(error => {
      console.warn('[push] Failed to unsubscribe after logout:', error)
    })
  } else {
    void syncExistingPushSubscription()
      .then(isSubscribed => {
        desktopNotification.value = isSubscribed
      })
      .catch(async error => {
        await unsubscribeFromPush(false).catch(() => undefined)
        desktopNotification.value = false
        console.warn('[push] Failed to restore subscription binding:', error)
      })
  }
  void loadHistory()
}

const handlePushEnvironmentChange = () => {
  if (!document.hidden) void refreshPushState()
}

onMounted(() => {
  window.addEventListener('lingtu-auth-change', syncAuth)
  document.addEventListener('visibilitychange', handlePushEnvironmentChange)
  void loadHistory()
  void refreshPushState()
})

onUnmounted(() => {
  window.removeEventListener('lingtu-auth-change', syncAuth)
  document.removeEventListener('visibilitychange', handlePushEnvironmentChange)
})

// Refresh history after generating a plan
const refreshHistory = async () => {
  await loadHistory()
}

const openHistoryTrip = async (planNo: string) => {
  const requestedUserId = currentOwnerId()
  if (!requestedUserId) {
    message.warning('请先登录再读取历史计划')
    return
  }
  const requestVersion = ++historyDetailRequestVersion
  const requestIsCurrent = () => (
    historyDetailRequestVersion === requestVersion
    && currentOwnerId() === requestedUserId
  )
  try {
    const response = await fetchTripPlan(planNo)
    if (!requestIsCurrent()) return
    if (!response.data) throw new Error('历史计划数据为空')
    saveTripCache(response.data, planNo)
    saveTripSession(response.data)
    await router.push({ path: '/result', query: { plan: planNo } })
  } catch (error: any) {
    if (requestIsCurrent()) {
      message.error(error.message || '历史计划读取失败')
    }
  }
}

const transportationOptions = ['公共交通', '自驾', '步行', '混合']
const intercityTransportationOptions = ['自动选择', '火车/高铁', '飞机', '自驾']
const accommodationOptions = ['经济型酒店', '舒适型酒店', '豪华酒店', '民宿', '亲子酒店']
const quickPrompts = [
  '周末想出去透透气',
  '预算有限但想住得舒服',
  '两个人去吃点好的',
  '带父母，不想走太累',
  '想看自然风景，避开人群'
]

const formData = reactive<PlannerFormData>({
  origin_city: '',
  city: '',
  start_date: null,
  end_date: null,
  travel_days: 1,
  travelers: 1,
  budget: null,
  transportation: '公共交通',
  intercity_transportation: '自动选择',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: ''
})

const scheduleMeta = reactive({
  date_pattern: null as TripFormData['date_pattern'],
  weekend_style: null as TripFormData['weekend_style'],
  early_arrival_hint: null as string | null,
  departure_mode: null as TripFormData['departure_mode']
})

const travelersConfirmed = ref(false)
const selectedScheduleOption = ref('default')
const lastSemanticContract = ref<SemanticTripContract | null>(null)
/** Server-signed session contract token, returned verbatim on generation. */
const lastContractToken = ref<string | null>(null)
/** User explicitly accepted remaining contract issues in the confirm modal. */
const contractRiskAcknowledged = ref(false)
/** Structured issues from the latest backend 422 hard-block. */
const serverGateIssues = ref<ApiIssue[]>([])

const serverIssueTitle = (issue: ApiIssue) => {
  const code = issue.code || ''
  if (code.includes('DIVERGENCE')) return '表单与原文不一致'
  if (code.includes('CONFLICT')) return '服务端冲突'
  if (code.includes('PENDING')) return '服务端待确认'
  if (code) return code
  return '服务端校验'
}

const FIELD_LABELS: Record<string, string> = {
  origin_city: '出发地',
  destination_city: '目的地',
  start_date: '开始日期',
  end_date: '结束日期',
  travel_days: '天数',
  travelers: '人数',
  travel_party: '同行关系',
  budget: '预算',
  pace: '节奏',
  preferences: '偏好',
  transportation: '交通',
  accommodation: '住宿'
}

/**
 * Labels the recommender writes into 额外要求 that record a *decided*
 * constraint. 【理由】/【抵达建议】 are generated prose and an unconfirmed
 * suggestion — reading them as user intent is how a two-day weekend once
 * became a three-day trip. Mirrors `decided_constraint_text` on the backend.
 */
const DECIDED_MACHINE_LABELS = ['时段', '约束', '同行', '范围', '排除', '目的地']
const MACHINE_LABELS = [
  ...DECIDED_MACHINE_LABELS,
  '抵达建议',
  '理由',
  '出发',
  '城际',
  '优先'
]

const decidedConstraintText = (raw: string | null | undefined): string => {
  const text = String(raw || '')
  if (!text.includes('【')) return text
  const kept: string[] = []
  const pattern = new RegExp(`【(${MACHINE_LABELS.join('|')}|原文)】([^【\\n]*)`, 'g')
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    kept.push(text.slice(cursor, match.index))
    cursor = match.index + match[0].length
    if (match[1] === '原文' || DECIDED_MACHINE_LABELS.includes(match[1])) {
      kept.push(match[2])
    }
  }
  kept.push(text.slice(cursor))
  return kept.join(' ').trim()
}

/** Fields that must not silently ship with pending/conflict state. */
const CRITICAL_CONTRACT_FIELDS = new Set([
  'origin_city',
  'destination_city',
  'start_date',
  'end_date',
  'travel_days',
  'travelers',
  'travel_party',
  'budget',
  'pace'
])

const markTravelersConfirmed = () => {
  travelersConfirmed.value = true
}

const planningReady = computed(() =>
  Boolean(formData.city.trim() && formData.start_date && formData.end_date)
)

/**
 * Pending items that the current form has not yet resolved.
 * Filling dates / travelers / budget / origin clears the corresponding pending flag.
 */
const unresolvedPendingKeys = computed(() => {
  const pending = lastSemanticContract.value?.pending_fields || []
  return pending.filter((name) => {
    if (!CRITICAL_CONTRACT_FIELDS.has(name)) return false
    if (name === 'start_date' || name === 'end_date') {
      return !(formData.start_date && formData.end_date)
    }
    if (name === 'travel_days') {
      return !(formData.start_date && formData.end_date && formData.travel_days > 0)
    }
    if (name === 'travelers') {
      return !travelersConfirmed.value
    }
    if (name === 'budget') {
      return formData.budget == null || formData.budget <= 0
    }
    if (name === 'origin_city') {
      return !formData.origin_city?.trim()
    }
    if (name === 'destination_city') {
      return !formData.city.trim()
    }
    if (name === 'travel_party') {
      // Resolved once the user has confirmed a traveler count on the form.
      return !travelersConfirmed.value
    }
    if (name === 'pace') {
      // Pace is soft; free_text or gentle preferences count as acknowledgment.
      // Mirrors the backend: 【理由】/【抵达建议】 are generated copy and must
      // not resolve a pending pace on the user's behalf.
      const text = decidedConstraintText(formData.free_text_input)
      return !(
        formData.preferences.includes('休闲')
        || /轻松|慢|父母|爸妈|避暑|不想太累/.test(text)
      )
    }
    return true
  })
})

const unresolvedPendingLabels = computed(() =>
  unresolvedPendingKeys.value.map((name) => FIELD_LABELS[name] || name)
)

/**
 * Surface only unresolved conflicts. Successful "latest explicit overwrote form"
 * notes are audit history, not blockers.
 *
 * The backend owns this rule and ships the resolved list as
 * `interpreted_context.blocking_conflicts`. The local filter below is only a
 * fallback for older responses — without it the UI would drift and show a
 * "需对齐" banner for notes the server already treats as audit-only.
 */
const serverBlockingConflicts = ref<string[] | null>(null)

const activeConflictMessages = computed(() => {
  if (serverBlockingConflicts.value) {
    return serverBlockingConflicts.value.slice(0, 5)
  }
  const conflicts = lastSemanticContract.value?.conflicts || []
  return conflicts
    .filter((item) => {
      if (item.includes('最新用户明示') && item.includes('覆盖旧值')) return false
      if (item.includes('覆盖规则/默认值')) return false
      if (item.includes('与日期窗口')) return false
      if (/^(start_date|end_date|travel_days)\s*:/.test(item) && item.includes('保留前者')) {
        return false
      }
      return true
    })
    .slice(0, 5)
})

const hasBlockingContractIssues = computed(
  () =>
    unresolvedPendingKeys.value.length > 0
    || activeConflictMessages.value.length > 0
)

const hasFormInsight = computed(
  () => hasBlockingContractIssues.value || serverGateIssues.value.length > 0
)

const formCardVisible = computed(
  () => recommendations.value.length > 0 || Boolean(formData.city)
)

/** How many distinct things the user still has to look at. */
const insightItemCount = computed(
  () =>
    unresolvedPendingLabels.value.length
    + activeConflictMessages.value.length
    + serverGateIssues.value.length
)

/**
 * The assistant column only points at the confirmation card; it never restates
 * its contents, and stays silent when that card is not on screen.
 */
const showInsightPointer = computed(
  () => formCardVisible.value && hasFormInsight.value
)

/** Local pending/conflict or last server gate — either needs explicit confirm. */
const needsContractConfirm = computed(
  () => hasBlockingContractIssues.value || serverGateIssues.value.length > 0
)

const formStatusLabel = computed(() => {
  if (serverGateIssues.value.length) return '需先对齐'
  if (hasBlockingContractIssues.value) return '有待确认'
  if (planningReady.value) return '可以开始规划'
  return '还差目的地和日期'
})

const weekendDateHint = computed(() => {
  if (scheduleMeta.departure_mode === 'evening_before') {
    return '已按周五下午出发：周五—周日（3 天）'
  }
  const pendingFields = lastSemanticContract.value?.pending_fields || []
  const hasPendingWeekendDates = (
    lastSemanticContract.value?.date_pattern?.value === 'weekend'
    && lastSemanticContract.value?.weekend_style?.value === 'sat_sun'
    && (pendingFields.includes('start_date') || pendingFields.includes('end_date'))
  )
  if (
    scheduleMeta.date_pattern === 'weekend'
    && scheduleMeta.weekend_style === 'sat_sun'
    && hasPendingWeekendDates
    && (!formData.start_date || !formData.end_date)
  ) {
    return '日期待确认（默认周六—周日）· 可选周五下午提前抵达'
  }
  const isConfirmedSaturdaySunday = (
    formData.start_date?.day() === 6
    && formData.end_date?.day() === 0
    && formData.end_date.diff(formData.start_date, 'day') === 1
  )
  if (
    scheduleMeta.date_pattern === 'weekend'
    && scheduleMeta.weekend_style === 'sat_sun'
    && isConfirmedSaturdaySunday
    && formData.travel_days === 2
  ) {
    return '默认周末两日；周五下午抵达仅为建议，不会自动加第三天'
  }
  return ''
})

watch(lastSemanticContract, () => {
  contractRiskAcknowledged.value = false
  serverGateIssues.value = []
})

watch(
  () => [
    formData.origin_city,
    formData.city,
    formData.start_date,
    formData.end_date,
    formData.travelers,
    formData.budget,
    formData.free_text_input,
    travelersConfirmed.value
  ],
  () => {
    // Form edits re-open confirmation if new unresolved items remain.
    if (needsContractConfirm.value) {
      contractRiskAcknowledged.value = false
    }
    // Stale server gate messages after the user edits the form.
    if (serverGateIssues.value.length) {
      serverGateIssues.value = []
    }
  }
)

const latestAssistantReply = computed(() => {
  if (assistantMessages.value.length <= 1) return ''
  return [...assistantMessages.value]
    .reverse()
    .find(item => item.role === 'assistant')?.content || ''
})

const APPLY_SAFE_CONTEXT_KEYS = new Set([
  'origin_city',
  'budget',
  'travelers',
  'transportation',
  'accommodation',
  'preferences',
  'start_date',
  'end_date',
  'travel_days',
  'pace',
  'travel_party',
  'destination_city'
])

const applyInterpretedContext = (context: Record<string, unknown>) => {
  // Backend only puts apply-safe (non-pending) values in the flat payload.
  // Ignore audit-only keys so rule guesses never overwrite the form.
  // Schedule semantics are message-scoped. Clear the previous response first
  // so a later non-weekend request cannot inherit a stale weekend banner.
  scheduleMeta.date_pattern = null
  scheduleMeta.weekend_style = null
  scheduleMeta.early_arrival_hint = null
  scheduleMeta.departure_mode = null
  if (typeof context.origin_city === 'string' && APPLY_SAFE_CONTEXT_KEYS.has('origin_city')) {
    formData.origin_city = context.origin_city
  }
  if (typeof context.budget === 'number') formData.budget = context.budget
  if (typeof context.travelers === 'number') {
    formData.travelers = context.travelers
    travelersConfirmed.value = true
  }
  if (typeof context.transportation === 'string') formData.transportation = context.transportation
  if (typeof context.accommodation === 'string') formData.accommodation = context.accommodation
  if (Array.isArray(context.preferences)) {
    const inferredPreferences = context.preferences.filter(
      (item): item is string => typeof item === 'string'
    )
    formData.preferences = [...new Set([
      ...formData.preferences,
      ...inferredPreferences
    ])]
  }
  // Pending weekend dates are omitted by the backend; only confirmed/explicit dates apply.
  if (typeof context.start_date === 'string') formData.start_date = dayjs(context.start_date)
  if (typeof context.end_date === 'string') formData.end_date = dayjs(context.end_date)
  if (typeof context.date_pattern === 'string') {
    scheduleMeta.date_pattern = context.date_pattern as TripFormData['date_pattern']
  }
  if (typeof context.weekend_style === 'string') {
    scheduleMeta.weekend_style = context.weekend_style as TripFormData['weekend_style']
  }
  if (typeof context.early_arrival_hint === 'string') {
    scheduleMeta.early_arrival_hint = context.early_arrival_hint
  }
  if (typeof context.departure_mode === 'string') {
    scheduleMeta.departure_mode = context.departure_mode as TripFormData['departure_mode']
  }
  if (
    typeof context.travel_days === 'number'
    && !formData.start_date
    && !formData.end_date
  ) {
    formData.travel_days = context.travel_days
  }
}

const sendAssistantMessage = async (content?: string) => {
  const text = (content ?? assistantInput.value).trim()
  if (!text) {
    message.warning('先说说你想要的旅行感觉')
    return
  }

  const requestedUserId = currentOwnerId()
  const requestVersion = ++assistantRequestVersion
  const requestIsCurrent = () => (
    assistantRequestVersion === requestVersion
    && currentOwnerId() === requestedUserId
  )
  assistantMessages.value.push({ role: 'user', content: text })
  const requestMessages = assistantMessages.value.map(item => ({ ...item }))
  assistantInput.value = ''
  recommendations.value = []
  selectedCity.value = ''
  selectedDestinationSource.value = 'manual'
  assistantLoading.value = true

  try {
    const response = await chatDestinationRecommendation({
      messages: requestMessages,
      context: {
        origin_city: formData.origin_city?.trim() || null,
        budget: formData.budget,
        travel_days: formData.start_date && formData.end_date ? formData.travel_days : null,
        travelers: travelersConfirmed.value ? formData.travelers : null,
        start_date: formData.start_date?.format('YYYY-MM-DD') || null,
        end_date: formData.end_date?.format('YYYY-MM-DD') || null,
        recommendation_count: recommendationCount.value,
        preferences: formData.preferences,
        transportation: formData.transportation,
        accommodation: formData.accommodation
      }
    })

    if (!requestIsCurrent()) return
    assistantMessages.value.push({
      role: 'assistant',
      content: response.reply || response.message || '我给你整理了几个方向。'
    })
    applyInterpretedContext(response.interpreted_context || {})
    lastSemanticContract.value =
      response.semantic_contract
      || (response.interpreted_context?.semantic_contract as SemanticTripContract | undefined)
      || null
    lastContractToken.value = response.contract_token || null
    const blocking = response.interpreted_context?.blocking_conflicts
    serverBlockingConflicts.value = Array.isArray(blocking)
      ? blocking.map((item) => String(item))
      : null
    recommendations.value = response.recommendations || []
  } catch (error: any) {
    if (requestIsCurrent()) {
      message.error(error.message || '获取推荐失败')
    }
  } finally {
    if (requestIsCurrent()) {
      assistantLoading.value = false
    }
  }
}

const askQuickPrompt = (prompt: string) => {
  const budget = formData.budget ? `预算${formData.budget}元` : '预算先不确定'
  const days = formData.start_date && formData.end_date ? '玩' + formData.travel_days + '天' : '天数未定'
  const currentOrigin = formData.origin_city || ''
  const origin = currentOrigin ? `从${currentOrigin}出发` : '出发地未定'
  sendAssistantMessage(`${prompt}, ${origin}, ${days}, ${budget}, 交通偏好${formData.transportation}`)
}

const clearFridayExpandedState = () => {
  // Drop Fri–Sun dates when leaving friday_early so 3-day state cannot linger.
  if (formData.start_date && formData.end_date) {
    const span = formData.end_date.diff(formData.start_date, 'day') + 1
    const startsFriday = formData.start_date.day() === 5
    if (span === 3 && startsFriday) {
      formData.start_date = null
      formData.end_date = null
    }
  }
  scheduleMeta.departure_mode = null
  if (scheduleMeta.weekend_style === 'fri_sun_optional') {
    scheduleMeta.weekend_style = 'sat_sun'
  }
  formData.travel_days = 2
}

const useRecommendation = (item: DestinationRecommendation) => {
  const patch = item.form_patch
  const scheduleOption = patch.schedule_option || item.schedule_option || 'default_weekend'
  const isFridayEarly = scheduleOption === 'friday_early'

  // Consistency gate: refuse silent day expansion / shrink.
  if (isFridayEarly) {
    if (patch.travel_days != null && patch.travel_days !== 3) {
      message.error('周五出发方案天数异常，未回填。请重试或手动填写日期。')
      return
    }
    if (patch.start_date && patch.end_date) {
      const span = dayjs(patch.end_date).diff(dayjs(patch.start_date), 'day') + 1
      if (span !== 3) {
        message.error('周五出发方案日期跨度与 3 天不一致，未回填。')
        return
      }
    }
  } else if (
    (item.date_pattern === 'weekend' || patch.date_pattern === 'weekend'
      || item.weekend_style === 'sat_sun' || patch.weekend_style === 'sat_sun')
  ) {
    if (patch.travel_days != null && patch.travel_days !== 2) {
      message.error('普通周末方案必须为 2 天，未回填异常天数。')
      return
    }
    if (patch.start_date && patch.end_date) {
      const span = dayjs(patch.end_date).diff(dayjs(patch.start_date), 'day') + 1
      if (span !== 2 && (patch.travel_days === 2 || patch.weekend_style === 'sat_sun')) {
        message.error('普通周末方案日期跨度与 2 天不一致，未回填。')
        return
      }
    }
  }

  // Switching away from Friday-early must wipe 3-day / evening_before residue.
  if (!isFridayEarly && selectedScheduleOption.value === 'friday_early') {
    clearFridayExpandedState()
  }

  selectedCity.value = item.city
  selectedScheduleOption.value = scheduleOption || 'default'
  selectedDestinationSource.value = patch.destination_source || 'recommendation'
  formData.city = patch.city
  if (patch.origin_city) {
    formData.origin_city = patch.origin_city
  }
  if (patch.travelers) {
    formData.travelers = patch.travelers
    travelersConfirmed.value = true
  }
  // Do not adopt estimated budget as user budget when source is recommendation estimate.
  if (patch.budget !== null && patch.budget !== undefined) {
    formData.budget = patch.budget
  }
  if (patch.transportation) {
    formData.transportation = patch.transportation
  }
  if (patch.accommodation) {
    formData.accommodation = patch.accommodation
  }
  if (patch.preferences.length) {
    formData.preferences = patch.preferences
  }
  formData.free_text_input = patch.free_text_input

  scheduleMeta.date_pattern = patch.date_pattern ?? item.date_pattern ?? null
  scheduleMeta.weekend_style = patch.weekend_style ?? item.weekend_style ?? null
  scheduleMeta.early_arrival_hint = patch.early_arrival_hint ?? item.early_arrival_hint ?? null

  if (isFridayEarly) {
    if (patch.start_date) formData.start_date = dayjs(patch.start_date)
    if (patch.end_date) formData.end_date = dayjs(patch.end_date)
    scheduleMeta.departure_mode = 'evening_before'
    scheduleMeta.weekend_style = 'fri_sun_optional'
    if (formData.start_date && formData.end_date) {
      formData.travel_days = formData.end_date.diff(formData.start_date, 'day') + 1
    } else {
      formData.travel_days = 3
    }
    message.success('已按周五下午出发安排')
  } else {
    // Default / non-Friday cards must not inherit Friday concrete dates.
    if (patch.start_date && patch.end_date) {
      formData.start_date = dayjs(patch.start_date)
      formData.end_date = dayjs(patch.end_date)
    } else if (
      scheduleMeta.date_pattern === 'weekend'
      || scheduleMeta.weekend_style === 'sat_sun'
    ) {
      // Pending weekend: leave dates empty for user confirmation.
      formData.start_date = null
      formData.end_date = null
      formData.travel_days = 2
    }
    scheduleMeta.departure_mode = patch.departure_mode ?? item.departure_mode ?? null
    if (scheduleMeta.weekend_style === 'sat_sun' || scheduleMeta.date_pattern === 'weekend') {
      scheduleMeta.departure_mode = null
      if (!formData.start_date || !formData.end_date) {
        formData.travel_days = 2
      }
    }
    message.success(
      scheduleMeta.weekend_style === 'sat_sun' || scheduleMeta.date_pattern === 'weekend'
        ? `已采用${patch.city}（默认周六—周日两日），请确认日期后生成`
        : `已采用${patch.city}方案，确认日期后即可生成行程`
    )
  }
  window.setTimeout(() => {
    document.querySelector('.form-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 120)
}

watch([() => formData.start_date, () => formData.end_date], ([start, end]) => {
  if (!start || !end) {
    const isPendingWeekend = (
      scheduleMeta.date_pattern === 'weekend'
      && scheduleMeta.weekend_style === 'sat_sun'
    )
    formData.travel_days = isPendingWeekend ? 2 : 1
    return
  }

  const days = end.diff(start, 'day') + 1
  if (days > 0 && days <= 30) {
    formData.travel_days = days
  } else if (days > 30) {
    message.warning('旅行天数不能超过30天')
    formData.end_date = null
  } else {
    message.warning('结束日期不能早于开始日期')
    formData.end_date = null
  }
})

const handleDesktopNotificationChange = async (checked: boolean) => {
  if (pushBusy.value) return
  if (!currentUser.value) {
    desktopNotification.value = false
    message.warning('\u8bf7\u5148\u767b\u5f55\u518d\u5f00\u542f\u540e\u53f0\u901a\u77e5')
    return
  }

  pushBusy.value = true
  try {
    if (checked) {
      await subscribeToPush()
      desktopNotification.value = true
      message.success(
        '\u540e\u53f0\u901a\u77e5\u5df2\u5f00\u542f\uff1b\u5173\u95ed\u6d4f\u89c8\u5668\u540e\u80fd\u5426\u9001\u8fbe\u53d6\u51b3\u4e8e\u6d4f\u89c8\u5668\u548c\u7cfb\u7edf\u7b56\u7565'
      )
    } else {
      const result = await unsubscribeFromPush()
      desktopNotification.value = false
      if (result.cleanupError) {
        message.warning(`\u6d4f\u89c8\u5668\u8ba2\u9605\u5df2\u53d6\u6d88\uff1b${result.cleanupError}`)
      } else {
        message.success('\u540e\u53f0\u901a\u77e5\u5df2\u5173\u95ed')
      }
    }
  } catch (error: any) {
    if (checked) {
      await unsubscribeFromPush(false).catch(() => undefined)
    }
    desktopNotification.value = false
    message.error(
      error.message
      || (checked ? '\u5f00\u542f\u540e\u53f0\u901a\u77e5\u5931\u8d25' : '\u5173\u95ed\u540e\u53f0\u901a\u77e5\u5931\u8d25')
    )
  } finally {
    notificationPermission.value = getPushPermissionState()
    pushBusy.value = false
  }
}

const confirmContractRisksIfNeeded = (): Promise<boolean> => {
  if (!needsContractConfirm.value || contractRiskAcknowledged.value) {
    return Promise.resolve(true)
  }

  const pendingText = unresolvedPendingLabels.value.length
    ? `待确认：${unresolvedPendingLabels.value.join('、')}`
    : ''
  const conflictText = activeConflictMessages.value.length
    ? `需对齐：${activeConflictMessages.value.slice(0, 2).join('；')}`
    : ''
  const serverText = serverGateIssues.value[0]?.message
    ? `服务端：${serverGateIssues.value[0].message}`
    : ''

  return new Promise((resolve) => {
    Modal.confirm({
      title: '生成前再确认一下',
      centered: true,
      okText: '已核对，继续生成',
      cancelText: '返回修改',
      content: h('div', { class: 'contract-confirm-modal' }, [
        h(
          'p',
          { style: 'margin:0 0 8px;line-height:1.6;color:#4b5563' },
          '还有细节未完全落定。继续将按当前表单生成；若识别有误，请先改表单。'
        ),
        pendingText
          ? h('p', { style: 'margin:0 0 6px;line-height:1.55;color:#0f766e' }, pendingText)
          : null,
        conflictText
          ? h('p', { style: 'margin:0 0 6px;line-height:1.55;color:#b45309' }, conflictText)
          : null,
        serverText
          ? h('p', { style: 'margin:0;line-height:1.55;color:#1d4ed8' }, serverText)
          : null
      ]),
      onOk: () => {
        contractRiskAcknowledged.value = true
        resolve(true)
      },
      onCancel: () => {
        contractRiskAcknowledged.value = false
        resolve(false)
      }
    })
  })
}

const handleSubmit = async () => {
  if (!formData.city.trim()) {
    message.warning('先告诉 AI 你的旅行想法，或直接填写一个目的地')
    return
  }
  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }
  const startDate = formData.start_date.startOf('day')
  const endDate = formData.end_date.startOf('day')
  if (!startDate.isValid() || !endDate.isValid()) {
    message.error('日期格式无效，请重新选择')
    return
  }
  if (startDate.isBefore(dayjs().startOf('day'))) {
    message.error('出发日期不能早于今天')
    return
  }
  if (endDate.isBefore(startDate)) {
    message.error('结束日期不能早于开始日期')
    return
  }
  const actualTravelDays = endDate.diff(startDate, 'day') + 1
  if (actualTravelDays < 1 || actualTravelDays > 30) {
    message.error('旅行天数必须在1至30天之间')
    return
  }
  formData.travel_days = actualTravelDays

  const recipient = (deliveryEmail.value.trim() || currentUser.value?.email || '').trim()
  if (emailOnCompletion.value && !currentUser.value) {
    message.error('请先登录再发送旅行计划邮件')
    return
  }
  if (emailOnCompletion.value && !recipient) {
    message.error('请填写收件邮箱或先在账号中绑定邮箱')
    return
  }
  if (emailOnCompletion.value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient)) {
    message.error('请输入有效的收件邮箱')
    return
  }

  const accepted = await confirmContractRisksIfNeeded()
  if (!accepted) {
    message.info('已取消生成，请先确认表单中的待确认项')
    return
  }

  const generationOwnerId = currentOwnerId()
  const requestVersion = ++generationRequestVersion
  const requestIsCurrent = () => (
    generationRequestVersion === requestVersion
    && currentOwnerId() === generationOwnerId
  )
  loading.value = true
  loadingProgress.value = 0
  loadingStatus.value = '正在初始化'
  loadingDetail.value = '正在建立本次旅行的约束与决策上下文。'
  loadingEvents.value = []

  try {
    // Stamp acknowledgment into free_text for server-side audit trail.
    let freeText = formData.free_text_input.trim()
    if (
      contractRiskAcknowledged.value
      && !freeText.includes('[用户已确认待核对约束]')
    ) {
      freeText = freeText
        ? `${freeText} [用户已确认待核对约束]`
        : '[用户已确认待核对约束]'
    }

    const requestData: TripFormData = {
      origin_city: formData.origin_city?.trim() || null,
      city: formData.city.trim(),
      destination_source:
        selectedCity.value && selectedCity.value === formData.city.trim()
          ? selectedDestinationSource.value
          : 'manual',
      start_date: formData.start_date.format('YYYY-MM-DD'),
      end_date: formData.end_date.format('YYYY-MM-DD'),
      travel_days: formData.travel_days,
      travelers: formData.travelers,
      budget: formData.budget,
      transportation: formData.transportation,
      intercity_transportation: formData.intercity_transportation || null,
      accommodation: formData.accommodation,
      preferences: formData.preferences,
      free_text_input: freeText,
      email_on_completion: emailOnCompletion.value,
      delivery_email: emailOnCompletion.value ? recipient : null,
      semantic_contract: lastSemanticContract.value,
      semantic_risks_acknowledged: contractRiskAcknowledged.value,
      recommendation_token: lastContractToken.value,
      date_pattern: scheduleMeta.date_pattern,
      weekend_style: scheduleMeta.weekend_style,
      early_arrival_hint: scheduleMeta.early_arrival_hint,
      departure_mode: scheduleMeta.departure_mode
    }

    const response = await generateTripPlanWithProgress(requestData, event => {
      if (!requestIsCurrent()) return
      if (typeof event.progress === 'number') {
        loadingProgress.value = Math.max(loadingProgress.value, event.progress)
      }
      if (event.message) {
        loadingStatus.value = event.message
      }
      if (event.detail) {
        loadingDetail.value = event.detail
      }
      if (event.stage && event.message && event.stage !== 'initialized') {
        const nextEvent = {
          stage: event.stage,
          message: event.message,
          detail: event.detail
        }
        const existingIndex = loadingEvents.value.findIndex(item => item.stage === event.stage)
        if (existingIndex >= 0) {
          loadingEvents.value.splice(existingIndex, 1, nextEvent)
        } else {
          loadingEvents.value.push(nextEvent)
        }
      }
    })
    if (!requestIsCurrent()) return
    console.info('[home] trip plan response received', {
      success: response.success,
      hasData: Boolean(response.data),
      city: response.data?.city
    })
    loadingProgress.value = 100
    loadingStatus.value = '完成'

    if (response.success && response.data) {
      saveTripCache(response.data, response.plan_no)
      saveTripSession(response.data)
      refreshHistory()
      if (response.data.quality?.status === 'failed') {
        message.warning(response.message || '方案存在关键问题，已阻止自动保存')
      } else {
        message.success(response.message || '旅行计划生成成功')
      }
      const delivery = response.email_delivery
      if (delivery?.sent) {
        message.success(`旅行计划已发送至 ${delivery.to}`)
      } else if (delivery?.dry_run) {
        message.warning(delivery.message || 'SMTP 未配置，邮件未真实发送')
      } else if (delivery?.requested) {
        message.warning(delivery.message || '邮件发送失败')
      }
      setTimeout(() => {
        if (!requestIsCurrent()) return
        router.push({
          path: '/result',
          query: response.plan_no ? { plan: response.plan_no } : {}
        })
      }, 500)
    } else {
      message.error(response.message || '生成失败')
    }
  } catch (error: any) {
    if (requestIsCurrent()) {
      if (error instanceof ApiClientError) {
        serverGateIssues.value = error.issues || []
        const primary = error.message || '生成旅行计划失败'
        const extra = (error.issues || [])
          .map((item) => item.message)
          .filter((text): text is string => Boolean(text))
          .filter((text) => !primary.includes(text))
          .slice(0, 2)
        message.error(
          extra.length ? `${primary}（${extra.join('；')}）` : primary,
          6
        )
        // If server still wants acknowledgment, surface the local confirm path again.
        if (
          error.status === 422
          && (error.issues || []).some((item) =>
            String(item.code || '').startsWith('SEMANTIC_')
          )
        ) {
          contractRiskAcknowledged.value = false
          document.querySelector('.form-card')?.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
          })
        }
      } else {
        message.error(error.message || '生成旅行计划失败,请稍后重试')
      }
    }
  } finally {
    setTimeout(() => {
      if (!requestIsCurrent()) return
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
      loadingDetail.value = ''
      loadingEvents.value = []
    }, 900)
  }
}
</script>

<style scoped>
.home-container {
  min-height: calc(100vh - 64px);
  background:
    linear-gradient(120deg, rgba(15, 118, 110, 0.08), rgba(37, 99, 235, 0.08)),
    #f7faf9;
  padding: 48px 24px 64px;
}

.planner-shell {
  width: min(1360px, 100%);
  margin: 0 auto;
}

.planner-intro {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 24px;
  align-items: end;
  margin-bottom: 24px;
}

.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #0f766e;
  font-size: 14px;
  font-weight: 700;
}

.intro-copy h1 {
  margin: 10px 0 8px;
  color: #172033;
  font-size: 48px;
  line-height: 1.05;
  font-weight: 800;
  letter-spacing: 0;
}

.intro-copy p {
  margin: 0;
  color: #667085;
  font-size: 17px;
}

.trip-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(92px, 1fr));
  gap: 10px;
}

.summary-item {
  min-height: 74px;
  padding: 14px 16px;
  border: 1px solid #dde7e4;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.88);
}

.summary-label {
  display: block;
  color: #667085;
  font-size: 13px;
  margin-bottom: 6px;
}

.summary-item strong {
  display: block;
  color: #172033;
  font-size: 22px;
  line-height: 1.2;
  word-break: keep-all;
}

.summary-wide strong {
  font-size: 18px;
}

.planning-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 20px;
  align-items: start;
}

.form-card {
  border-radius: 8px;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
}

.assistant-panel {
  padding: 22px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
}

.assistant-header {
  margin-bottom: 16px;
}

.assistant-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #172033;
  font-size: 18px;
  font-weight: 800;
}

.assistant-title :deep(svg) {
  color: #f59e0b;
}

.assistant-header p {
  margin: 8px 0 0;
  color: #667085;
  font-size: 14px;
  line-height: 1.6;
}

.assistant-controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin-bottom: 14px;
}

.assistant-origin :deep(.ant-input) {
  border-radius: 8px;
  border-color: #d9e2df;
}

.count-control {
  white-space: nowrap;
}

.chat-window {
  max-height: 260px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid #e4ebe8;
  border-radius: 8px;
  background: #f8fbfb;
}

.chat-message {
  max-width: 92%;
  padding: 10px 12px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #e4ebe8;
}

.chat-message.user {
  align-self: flex-end;
  background: #0f766e;
  border-color: #0f766e;
  color: #ffffff;
}

.message-role {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 800;
  opacity: 0.78;
}

.chat-message p {
  margin: 0;
  font-size: 14px;
  line-height: 1.55;
}

.quick-prompts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0;
}

.quick-prompts button {
  padding: 6px 10px;
  border: 1px solid #d9e2df;
  border-radius: 8px;
  background: #ffffff;
  color: #475467;
  cursor: pointer;
  font-size: 13px;
}

.quick-prompts button:hover {
  border-color: #0f766e;
  color: #0f766e;
  background: #f0fdfa;
}

.assistant-input :deep(.ant-input) {
  border-radius: 8px;
  border-color: #d9e2df;
}

.assistant-send {
  height: 42px;
  margin-top: 10px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #2563eb;
  border-color: #2563eb;
  font-weight: 700;
}

.recommendation-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}

.recommendation-card {
  padding: 14px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #fbfdfc;
}

.recommendation-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.city-name {
  display: inline-block;
  margin-right: 8px;
  color: #172033;
  font-size: 18px;
  font-weight: 800;
}

.budget-fit {
  display: inline-block;
  padding: 3px 7px;
  border-radius: 6px;
  background: #edf7f5;
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
}

.recommendation-head strong {
  color: #2563eb;
  white-space: nowrap;
}

.recommendation-reason {
  margin: 0 0 10px;
  color: #475467;
  font-size: 14px;
  line-height: 1.6;
}

.origin-note {
  margin: -2px 0 10px;
  color: #0f766e;
  font-size: 13px;
  font-weight: 700;
}

.highlight-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.highlight-row span {
  padding: 4px 7px;
  border-radius: 6px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
}

.weather-note {
  margin: 0 0 10px;
  color: #667085;
  font-size: 13px;
}

.use-recommendation {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 8px;
}

.form-card :deep(.ant-card-body) {
  padding: 28px;
}

.form-section {
  padding: 22px 0 26px;
  border-bottom: 1px solid #e6ecea;
}

.form-section:first-child {
  padding-top: 0;
}

.form-section.compact {
  border-bottom: 0;
}

.section-header {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
  color: #172033;
  font-size: 17px;
  font-weight: 700;
}

.section-header :deep(svg) {
  color: #0f766e;
}

.form-label {
  color: #475467;
  font-size: 14px;
  font-weight: 600;
}

.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker),
.custom-select :deep(.ant-select-selector),
.custom-textarea :deep(.ant-input) {
  border-radius: 8px !important;
  border-color: #d9e2df !important;
}

/* 随内容自动增高（4~12 行），超出后仍可手动拉高，避免在小框里反复上下翻 */
.custom-textarea,
.custom-textarea :deep(.ant-input) {
  resize: vertical;
  line-height: 1.7;
}

.budget-input {
  width: 100%;
  border-radius: 8px;
  border-color: #d9e2df;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover),
.custom-select:hover :deep(.ant-select-selector),
.custom-textarea :deep(.ant-input:hover),
.budget-input:hover {
  border-color: #0f766e !important;
}

.days-display {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 4px;
  height: 40px;
  border: 1px solid #d9e2df;
  border-radius: 8px;
  background: #edf7f5;
  color: #0f766e;
}

.days-display strong {
  font-size: 24px;
  line-height: 1;
}

.mode-control {
  border-radius: 8px;
}

.preference-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  width: 100%;
}

.preference-grid :deep(.ant-checkbox-wrapper) {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid #d9e2df;
  border-radius: 8px;
  background: #fff;
  color: #475467;
  transition: border-color 0.2s ease, background-color 0.2s ease;
}

.delivery-status {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin-top: 9px;
  color: #667085;
  font-size: 12px;
  line-height: 1.45;
}

.delivery-status-dot {
  width: 7px;
  height: 7px;
  margin-top: 5px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #98a2b3;
}

.delivery-status.is-success {
  color: #067647;
}

.delivery-status.is-success .delivery-status-dot {
  background: #12b76a;
}

.delivery-status.is-warning {
  color: #b54708;
}

.delivery-status.is-warning .delivery-status-dot {
  background: #f79009;
}

.preference-grid :deep(.ant-checkbox-wrapper:hover) {
  border-color: #0f766e;
  background: #f0fdfa;
}

.delivery-settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 18px 0 20px;
  border-top: 1px solid #e6ecea;
}

.delivery-channel {
  min-width: 0;
}

.delivery-toggle {
  min-height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.delivery-toggle > span {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #344054;
  font-weight: 700;
}

.delivery-email {
  margin-top: 10px;
}

.action-row {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}

.submit-button {
  min-width: 180px;
  height: 46px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #0f766e;
  border-color: #0f766e;
  font-weight: 700;
}

.submit-button:hover {
  background: #115e59 !important;
  border-color: #115e59 !important;
}

.loading-container {
  margin-top: 20px;
  padding: 18px;
  border-radius: 8px;
  background: #f8fbfb;
  border: 1px solid #d9e2df;
}

.loading-status {
  margin: 12px 0 0;
  color: #0f766e;
  font-weight: 600;
  text-align: center;
}

.history-error {
  margin-top: 20px;
}

.history-section {
  margin-top: 20px;
  padding: 18px;
  border-top: 1px solid #e6ecea;
}

.history-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.history-card {
  width: 100%;
  border: 0;
  font: inherit;
  text-align: left;
  cursor: pointer;
  padding: 12px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #f8fbfb;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.history-card strong {
  color: #172033;
  font-size: 15px;
}

.history-card:disabled {
  cursor: default;
  opacity: 0.62;
}

.history-card:disabled:hover {
  transform: none;
  box-shadow: none;
}

.history-card:hover {
  transform: translateY(-1px);
  border-color: #8cc8bd;
  box-shadow: 0 6px 16px rgba(15, 118, 110, 0.1);
}

.history-open {
  color: #0f766e !important;
  font-weight: 700;
}

.history-card span {
  color: #667085;
  font-size: 12px;
}

@media (max-width: 900px) {
  .home-container {
    padding: 32px 16px 48px;
  }

  .planner-intro {
    grid-template-columns: 1fr;
    align-items: stretch;
  }

  .intro-copy h1 {
    font-size: 38px;
  }

  .trip-summary {
    grid-template-columns: repeat(3, 1fr);
  }

  .planning-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .home-container {
    padding: 24px 12px 40px;
  }

  .planner-intro {
    gap: 18px;
    margin-bottom: 18px;
  }

  .eyebrow {
    font-size: 13px;
  }

  .intro-copy h1 {
    font-size: 34px;
  }

  .intro-copy p {
    font-size: 15px;
    line-height: 1.5;
  }

  .trip-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .summary-item {
    min-height: 64px;
    padding: 10px 8px;
  }

  .summary-label {
    font-size: 12px;
  }

  .summary-item strong {
    font-size: 18px;
  }

  .summary-wide strong {
    font-size: 16px;
    overflow-wrap: anywhere;
  }

  .form-card :deep(.ant-card-body) {
    padding: 18px;
  }

  .form-section {
    padding: 18px 0 22px;
  }

  .section-header {
    margin-bottom: 14px;
    font-size: 16px;
  }

  .mode-control :deep(.ant-segmented-group) {
    flex-wrap: wrap;
  }

  .mode-control :deep(.ant-segmented-item) {
    flex: 1 1 50%;
    min-width: 0;
  }

  .preference-grid,
  .assistant-controls {
    grid-template-columns: 1fr;
  }

  .assistant-panel {
    padding: 18px;
  }

  .chat-window {
    max-height: 220px;
  }

  .quick-prompts button {
    flex: 1 1 auto;
  }

  .delivery-settings {
    grid-template-columns: 1fr;
  }

  .action-row {
    justify-content: stretch;
  }

  .submit-button {
    width: 100%;
  }
}

@media (max-width: 380px) {
  .intro-copy h1 {
    font-size: 31px;
  }

  .trip-summary {
    grid-template-columns: 1fr;
  }
}

/* 2026 AI-first visual refresh */
.home-container {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 8% 5%, rgba(45, 212, 191, 0.19), transparent 28rem),
    radial-gradient(circle at 92% 12%, rgba(96, 165, 250, 0.2), transparent 30rem),
    linear-gradient(180deg, #f7fffd 0%, #f7f8fc 52%, #fbfcfe 100%);
}

.home-container::before,
.home-container::after {
  position: absolute;
  z-index: 0;
  width: 320px;
  height: 320px;
  border: 1px solid rgba(15, 118, 110, 0.08);
  border-radius: 50%;
  content: '';
  pointer-events: none;
}

.home-container::before {
  top: 120px;
  left: -210px;
  box-shadow: 0 0 0 42px rgba(15, 118, 110, 0.025), 0 0 0 92px rgba(15, 118, 110, 0.018);
}

.home-container::after {
  top: -180px;
  right: -100px;
  box-shadow: 0 0 0 54px rgba(37, 99, 235, 0.025);
}

.planner-shell {
  position: relative;
  z-index: 1;
  width: min(1180px, 100%);
}

.planner-intro {
  align-items: center;
  margin-bottom: 30px;
}

.eyebrow {
  padding: 7px 12px;
  border: 1px solid rgba(15, 118, 110, 0.15);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.62);
  backdrop-filter: blur(14px);
}

.intro-copy h1 {
  max-width: 760px;
  margin-top: 16px;
  font-size: clamp(42px, 6vw, 72px);
  line-height: 0.98;
  letter-spacing: -0.055em;
  background: linear-gradient(112deg, #102a2a 8%, #0f766e 55%, #2563eb 108%);
  background-clip: text;
  -webkit-background-clip: text;
  color: transparent;
}

.intro-copy p {
  max-width: 680px;
  font-size: 18px;
  line-height: 1.7;
}

.trip-summary {
  grid-template-columns: repeat(3, minmax(112px, 1fr));
}

.summary-item {
  min-height: 82px;
  border-color: rgba(148, 163, 184, 0.22);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.68);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
  backdrop-filter: blur(16px);
}

.summary-item strong {
  max-width: 150px;
  overflow: hidden;
  font-size: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.planning-grid {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.assistant-panel {
  position: relative;
  order: -1;
  overflow: hidden;
  padding: clamp(24px, 4vw, 48px);
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 30px;
  background:
    linear-gradient(138deg, rgba(255, 255, 255, 0.9), rgba(240, 253, 250, 0.76)),
    #ffffff;
  box-shadow: 0 28px 80px rgba(15, 118, 110, 0.12);
  backdrop-filter: blur(22px);
}

.assistant-panel::before {
  position: absolute;
  top: -180px;
  right: -120px;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(37, 99, 235, 0.17), rgba(45, 212, 191, 0.04) 56%, transparent 70%);
  content: '';
  pointer-events: none;
}

.assistant-header,
.journey-steps,
.assistant-controls,
.chat-window,
.quick-prompts,
.assistant-input,
.assistant-send,
.recommendation-section {
  position: relative;
  z-index: 1;
}

.assistant-title {
  font-size: clamp(24px, 3vw, 36px);
  letter-spacing: -0.03em;
}

.assistant-title :deep(svg) {
  width: 30px;
  height: 30px;
  color: #0f766e;
}

.assistant-header p {
  max-width: 760px;
  font-size: 15px;
}

.journey-steps {
  display: flex;
  align-items: center;
  max-width: 680px;
  margin: 24px 0;
}

.journey-steps span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #98a2b3;
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}

.journey-steps span b {
  display: inline-flex;
  width: 26px;
  height: 26px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #eef2f6;
  color: #667085;
  font-size: 12px;
}

.journey-steps span.active {
  color: #0f766e;
}

.journey-steps span.active b {
  background: #0f766e;
  box-shadow: 0 6px 16px rgba(15, 118, 110, 0.22);
  color: #ffffff;
}

.journey-steps i {
  width: 54px;
  height: 1px;
  margin: 0 12px;
  background: linear-gradient(90deg, #9dd8cf, #dbe4e8);
}

.assistant-controls {
  grid-template-columns: minmax(220px, 380px) 1fr;
  align-items: center;
  margin-bottom: 12px;
}

.assistant-origin :deep(.ant-input) {
  height: 44px;
  border-color: rgba(15, 118, 110, 0.18);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.82);
}

.privacy-note {
  color: #98a2b3;
  font-size: 12px;
}

.chat-window {
  max-height: 210px;
  border: 0;
  border-radius: 16px;
  background: rgba(238, 247, 246, 0.68);
}

.chat-message {
  border-radius: 14px;
}

.quick-prompts {
  margin: 12px 0;
}

.quick-prompts button {
  padding: 8px 13px;
  border-color: rgba(15, 118, 110, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.74);
  transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.quick-prompts button:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(15, 118, 110, 0.09);
}

.assistant-input :deep(.ant-input) {
  min-height: 116px;
  padding: 18px 20px;
  border: 1px solid rgba(15, 118, 110, 0.2);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: inset 0 1px 0 #ffffff, 0 10px 30px rgba(15, 23, 42, 0.045);
  font-size: 16px;
  line-height: 1.7;
  resize: none;
}

.assistant-input :deep(.ant-input:focus) {
  border-color: #14b8a6;
  box-shadow: 0 0 0 4px rgba(20, 184, 166, 0.1), 0 14px 36px rgba(15, 118, 110, 0.08);
}

.assistant-send {
  width: auto;
  min-width: 240px;
  height: 50px;
  margin-left: auto;
  border: 0;
  border-radius: 15px;
  background: linear-gradient(115deg, #0f766e, #0d9488 58%, #2563eb);
  box-shadow: 0 12px 28px rgba(15, 118, 110, 0.23);
}

.recommendation-section {
  margin-top: 36px;
  padding-top: 28px;
  border-top: 1px solid rgba(15, 118, 110, 0.12);
}

.recommendation-title-row,
.form-card-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}

.step-kicker {
  color: #0f766e;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.recommendation-title-row h3,
.form-card-heading h2 {
  margin: 5px 0 0;
  color: #172033;
  font-size: 25px;
  letter-spacing: -0.025em;
}

.recommendation-title-row > span {
  color: #98a2b3;
  font-size: 12px;
}

.recommendation-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}

/* 第四张为日程决策卡：独占一行，避免被当成第四座城市挤进 3 列 */
.recommendation-card.is-friday-early {
  grid-column: 1 / -1;
}

.recommendation-card {
  position: relative;
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 20px;
  border-color: rgba(148, 163, 184, 0.24);
  border-radius: 20px;
  background: linear-gradient(155deg, #ffffff, #f8fbfb);
  transition: transform 0.24s ease, border-color 0.24s ease, box-shadow 0.24s ease;
}

.recommendation-card:hover {
  transform: translateY(-5px);
  border-color: rgba(15, 118, 110, 0.34);
  box-shadow: 0 18px 42px rgba(15, 118, 110, 0.11);
}

.recommendation-card.selected {
  border-color: #0f766e;
  background: linear-gradient(155deg, #ffffff, #edfffb);
  box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.08), 0 18px 42px rgba(15, 118, 110, 0.12);
}

.option-label {
  margin-bottom: 15px;
  color: #98a2b3;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
}

.schedule-summary {
  margin: 0 0 8px;
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.45;
}

.early-arrival-hint {
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(240, 253, 250, 0.9);
  color: #0f766e;
  font-size: 12px;
  line-height: 1.5;
}

.recommendation-card.is-friday-early {
  border-color: rgba(37, 99, 235, 0.28);
  border-style: dashed;
  background: linear-gradient(160deg, #ffffff, #eef5ff);
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.06);
}

.recommendation-card.is-friday-early .option-label {
  color: #2563eb;
}

.recommendation-card.is-friday-early .city-name::after {
  content: ' · 日程方案';
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
}

.date-pending-hint {
  margin: -4px 0 4px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(255, 251, 235, 0.9);
  color: #b45309;
  font-size: 12px;
  line-height: 1.5;
}

.recommendation-reason {
  flex: 1;
}

.use-recommendation {
  height: 42px;
  margin-top: auto;
  border-radius: 12px;
}

.form-card {
  scroll-margin-top: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 26px;
  box-shadow: 0 22px 64px rgba(15, 23, 42, 0.07);
}

.form-card :deep(.ant-card-body) {
  padding: clamp(24px, 4vw, 42px);
}

.form-card-heading {
  align-items: center;
  padding-bottom: 20px;
  border-bottom: 1px solid #e9eeec;
}

.form-card-heading p {
  margin: 7px 0 0;
  color: #667085;
}

.completion-state {
  flex: 0 0 auto;
  padding: 8px 12px;
  border-radius: 999px;
  background: #fff7ed;
  color: #b54708;
  font-size: 12px;
  font-weight: 800;
}

.completion-state.ready {
  background: #ecfdf3;
  color: #067647;
}

.form-label em {
  margin-left: 4px;
  color: #98a2b3;
  font-size: 11px;
  font-style: normal;
  font-weight: 500;
}

.advanced-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  padding: 15px 16px;
  border: 1px solid #dce8e4;
  border-radius: 14px;
  background: #f8fbfb;
  color: #344054;
  cursor: pointer;
  font: inherit;
  font-weight: 700;
}

.advanced-toggle > span {
  display: inline-flex;
  align-items: center;
  gap: 9px;
}

.advanced-toggle :deep(svg) {
  color: #0f766e;
  transition: transform 0.24s ease;
}

.advanced-toggle :deep(svg.rotated) {
  transform: rotate(180deg);
}

.advanced-content {
  animation: advancedReveal 0.28s ease-out;
}

@keyframes advancedReveal {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

.action-row {
  padding-top: 22px;
}

.submit-button {
  min-width: 260px;
  height: 50px;
  border: 0;
  border-radius: 14px;
  background: linear-gradient(115deg, #0f766e, #0d9488);
  box-shadow: 0 12px 24px rgba(15, 118, 110, 0.18);
}

@media (max-width: 900px) {
  .trip-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .recommendation-list {
    grid-template-columns: 1fr;
  }

  .recommendation-card {
    min-height: 0;
  }
}

@media (max-width: 640px) {
  .intro-copy h1 {
    font-size: 42px;
  }

  .trip-summary {
    grid-template-columns: 1fr;
  }

  .summary-item {
    min-height: 68px;
  }

  .assistant-panel {
    padding: 22px 16px;
    border-radius: 22px;
  }

  .journey-steps {
    align-items: flex-start;
    justify-content: space-between;
  }

  .journey-steps i {
    flex: 1;
    width: auto;
    margin: 13px 6px 0;
  }

  .journey-steps span {
    flex-direction: column;
    gap: 5px;
    font-size: 10px;
  }

  .assistant-controls {
    grid-template-columns: 1fr;
  }

  .chat-window {
    max-height: 180px;
  }

  .assistant-send,
  .submit-button {
    width: 100%;
    min-width: 0;
  }

  .recommendation-title-row,
  .form-card-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .form-card {
    border-radius: 20px;
  }

  .completion-state {
    align-self: flex-start;
  }
}
/* Progressive-disclosure and decision-card refinements */
.planner-intro {
  grid-template-columns: minmax(0, 1fr);
  max-width: 900px;
}

.intent-summary {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 14px 0;
  padding: 14px 16px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  border-radius: 18px;
  background:
    linear-gradient(145deg, rgba(236, 253, 245, 0.92), rgba(240, 249, 255, 0.72));
  color: #28524e;
  box-shadow: 0 10px 28px rgba(15, 118, 110, 0.06);
}

.intent-summary__icon {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
}

.intent-summary__icon :deep(svg) {
  font-size: 15px;
}

.intent-summary__body {
  min-width: 0;
  flex: 1;
}

.intent-summary__kicker {
  display: inline-block;
  margin-bottom: 4px;
  color: #0f766e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.intent-summary p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 5;
  overflow: hidden;
}

/* Unified insight panels: calm, compact, brand-aligned */
.insight-panel {
  position: relative;
  z-index: 1;
  margin: 0 0 14px;
  padding: 14px 16px;
  border: 1px solid rgba(13, 148, 136, 0.16);
  border-radius: 18px;
  background:
    linear-gradient(155deg, rgba(255, 255, 255, 0.96), rgba(240, 253, 250, 0.88));
  color: #334155;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.04);
  animation: insightIn 0.28s ease-out;
}

.insight-panel--form {
  margin: 0 0 18px;
  border-color: rgba(245, 158, 11, 0.22);
  background:
    linear-gradient(155deg, rgba(255, 255, 255, 0.98), rgba(255, 251, 235, 0.78));
}

.insight-pointer {
  margin: 0 0 14px;
  padding: 9px 12px;
  border-radius: 10px;
  background: rgba(37, 99, 235, 0.06);
  color: #1d4ed8;
  font-size: 12.5px;
  line-height: 1.6;
}

.insight-panel__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin-bottom: 10px;
}

.insight-panel__badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.insight-panel--form .insight-panel__badge {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.insight-panel__title {
  color: #0f172a;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.insight-block + .insight-block {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(148, 163, 184, 0.16);
}

.insight-label {
  display: inline-block;
  margin-bottom: 6px;
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.06em;
}

.insight-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.insight-chip {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border: 1px solid rgba(15, 118, 110, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
  line-height: 1.3;
}

.insight-panel--form .insight-chip {
  border-color: rgba(245, 158, 11, 0.22);
  color: #b45309;
  background: rgba(255, 255, 255, 0.9);
}

.insight-text {
  margin: 0;
  color: #334155;
  font-size: 13px;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
}

.insight-hint {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.insight-more {
  display: inline-block;
  margin-top: 4px;
  color: #94a3b8;
  font-size: 11px;
}

.insight-foot {
  margin: 12px 0 0;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;
}

@keyframes insightIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.completion-state.warn {
  color: #b45309;
  background: rgba(255, 247, 237, 0.95);
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.18);
}

.decision-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin: 4px 0 14px;
}

.decision-metrics span {
  min-width: 0;
  padding: 9px 7px;
  border-radius: 11px;
  background: #f3f7f7;
  color: #667085;
  font-size: 10px;
  text-align: center;
}

.decision-metrics b {
  display: block;
  overflow: hidden;
  margin-bottom: 2px;
  color: #172033;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tradeoff-note {
  margin: 0 0 12px;
  padding: 10px 11px;
  border-left: 3px solid #f59e0b;
  border-radius: 0 10px 10px 0;
  background: #fffbeb;
  color: #7c5a13;
  font-size: 12px;
  line-height: 1.55;
}

.recommendation-head {
  align-items: flex-start;
}

.recommendation-head .budget-fit {
  max-width: 58%;
  text-align: right;
}

@media (max-width: 420px) {
  .decision-metrics {
    grid-template-columns: 1fr;
  }

  .decision-metrics span {
    display: flex;
    align-items: center;
    justify-content: space-between;
    text-align: left;
  }

  .decision-metrics b {
    display: inline;
    margin: 0;
  }
}

/* Agent workflow: compact, factual and intentionally free of fake thinking copy */
.agent-workflow {
  margin-top: 22px;
  padding: 22px;
  overflow: hidden;
  border: 1px solid rgba(15, 118, 110, 0.16);
  border-radius: 20px;
  background:
    radial-gradient(circle at 100% 0, rgba(37, 99, 235, 0.09), transparent 42%),
    linear-gradient(145deg, #f7fffd, #f8fafc);
  box-shadow: 0 16px 42px rgba(15, 23, 42, 0.06);
}

.workflow-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
}

.workflow-head h3 {
  margin: 2px 0 3px;
  color: #102a2a;
  font-size: 18px;
  letter-spacing: -0.02em;
}

.workflow-head p {
  margin: 0;
  color: #667085;
  font-size: 13px;
  line-height: 1.55;
}

.workflow-head > strong {
  color: #0f766e;
  font-size: 20px;
  font-variant-numeric: tabular-nums;
}

.workflow-kicker {
  color: #0f766e;
  font-size: 9px;
  font-weight: 900;
  letter-spacing: 0.16em;
}

.workflow-orb {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 14px;
  background: linear-gradient(135deg, #0f766e, #2563eb);
  box-shadow: 0 10px 24px rgba(15, 118, 110, 0.2);
}

.workflow-orb span {
  width: 12px;
  height: 12px;
  border: 2px solid #ffffff;
  border-radius: 50%;
  animation: agentPulse 1.5s ease-in-out infinite;
}

@keyframes agentPulse {
  50% { transform: scale(1.35); opacity: 0.55; }
}

.workflow-trace {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
}

.trace-item {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 11px;
  border: 1px solid rgba(15, 118, 110, 0.1);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.72);
}

.trace-item :deep(svg) {
  flex: 0 0 auto;
  margin-top: 3px;
  color: #0f766e;
}

.trace-item span {
  min-width: 0;
}

.trace-item strong,
.trace-item small {
  display: block;
}

.trace-item strong {
  color: #344054;
  font-size: 12px;
}

.trace-item small {
  margin-top: 3px;
  color: #7b8494;
  font-size: 10px;
  line-height: 1.45;
}

.workflow-foot {
  margin-top: 13px;
  color: #98a2b3;
  font-size: 10px;
  text-align: right;
}

@media (max-width: 640px) {
  .agent-workflow {
    padding: 17px;
  }

  .workflow-head {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .workflow-head > strong {
    display: none;
  }

  .workflow-trace {
    grid-template-columns: 1fr;
  }
}
</style>
