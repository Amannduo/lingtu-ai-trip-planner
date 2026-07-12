<template>
  <div class="home-container">
    <section class="planner-shell">
      <div class="planner-intro">
        <div class="intro-copy">
          <div class="eyebrow">
            <CompassOutlined />
            <span>灵途 AI</span>
          </div>
          <h1>旅行计划</h1>
          <p>城市、日期、交通、住宿与偏好</p>
        </div>

        <div class="trip-summary">
          <div class="summary-item">
            <span class="summary-label">天数</span>
            <strong>{{ formData.travel_days }}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">偏好</span>
            <strong>{{ formData.preferences.length }}</strong>
          </div>
          <div class="summary-item summary-wide">
            <span class="summary-label">预算</span>
            <strong>{{ budgetText }}</strong>
          </div>
        </div>
      </div>

      <div class="planning-grid">
        <a-card class="form-card" :bordered="false">
          <a-form :model="formData" layout="vertical" @finish="handleSubmit">
          <div class="form-section">
            <div class="section-header">
              <EnvironmentOutlined />
              <span>目的地与日期</span>
            </div>

            <a-row :gutter="[16, 16]">
              <a-col :xs="24" :md="8">
                <a-form-item name="origin_city">
                  <template #label>
                    <span class="form-label">出发城市</span>
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
                    placeholder="选择日期"
                  />
                </a-form-item>
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
                    <a-select-option value="经济型酒店">经济型酒店</a-select-option>
                    <a-select-option value="舒适型酒店">舒适型酒店</a-select-option>
                    <a-select-option value="豪华酒店">豪华酒店</a-select-option>
                    <a-select-option value="民宿">民宿</a-select-option>
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
                :rows="3"
                size="large"
                class="custom-textarea"
              />
            </a-form-item>
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
                <span>开始规划</span>
              </template>
              <template v-else>
                <span>正在生成</span>
              </template>
            </a-button>
          </div>

          <!-- 旅行历史 -->
          <div v-if="history.stats.total_trips > 0" class="history-section">
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

          <div v-if="loading" class="loading-container">
            <a-progress
              :percent="loadingProgress"
              status="active"
              :stroke-color="{ '0%': '#0f766e', '100%': '#2563eb' }"
              :stroke-width="8"
            />
            <p class="loading-status">{{ loadingStatus }}</p>
          </div>
          </a-form>
        </a-card>

        <aside class="assistant-panel">
          <div class="assistant-header">
            <div>
              <div class="assistant-title">
                <BulbOutlined />
                <span>不知道去哪？</span>
              </div>
              <p>告诉 AI 你的预算、天数和偏好，它会结合高德地图数据给你几个选择。</p>
            </div>
          </div>

          <div class="assistant-controls">
            <a-input
              v-model:value="assistantOriginCity"
              placeholder="从哪里出发"
              class="assistant-origin"
            />
            <a-segmented
              v-model:value="recommendationCount"
              :options="recommendationCountOptions"
              class="count-control"
            />
          </div>

          <div class="chat-window">
            <div
              v-for="(item, index) in assistantMessages"
              :key="`${item.role}-${index}`"
              class="chat-message"
              :class="item.role"
            >
              <span class="message-role">{{ item.role === 'user' ? '你' : 'AI' }}</span>
              <p>{{ item.content }}</p>
            </div>
          </div>

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
            placeholder="例如: 我想玩3天,预算3000,喜欢美食和历史,不要太累"
            :rows="3"
            class="assistant-input"
          />

          <a-button
            type="primary"
            class="assistant-send"
            :loading="assistantLoading"
            block
            @click="sendAssistantMessage()"
          >
            <SendOutlined />
            <span>获取推荐</span>
          </a-button>

          <div v-if="recommendations.length" class="recommendation-list">
            <div v-for="item in recommendations" :key="item.city" class="recommendation-card">
              <div class="recommendation-head">
                <div>
                  <span class="city-name">{{ item.city }}</span>
                  <span class="budget-fit">{{ item.budget_fit }}</span>
                </div>
                <strong>{{ item.suggested_days }}天</strong>
              </div>

              <p class="recommendation-reason">{{ item.reason }}</p>
              <p v-if="item.origin_note" class="origin-note">{{ item.origin_note }}</p>

              <div v-if="item.highlights.length" class="highlight-row">
                <span v-for="highlight in item.highlights" :key="highlight">{{ highlight }}</span>
              </div>

              <p v-if="item.weather_summary" class="weather-note">{{ item.weather_summary }}</p>

              <a-button class="use-recommendation" block @click="useRecommendation(item)">
                <CheckOutlined />
                <span>选这个</span>
              </a-button>
            </div>
          </div>
        </aside>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  BulbOutlined,
  CheckOutlined,
  CompassOutlined,
  EnvironmentOutlined,
  HistoryOutlined,
  MessageOutlined,
  RocketOutlined,
  SendOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import {
  chatDestinationRecommendation,
  fetchTripHistory,
  fetchTripPlan,
  generateTripPlan
} from '@/services/api'
import { saveTripCache } from '@/services/tripCache'
import type { ChatMessage, DestinationRecommendation, TripFormData } from '@/types'
import type { Dayjs } from 'dayjs'
import { onMounted } from 'vue'

type PlannerFormData = Omit<TripFormData, 'start_date' | 'end_date'> & {
  start_date: Dayjs | null
  end_date: Dayjs | null
}

const router = useRouter()
const loading = ref(false)
const loadingProgress = ref(0)
const loadingStatus = ref('')
const assistantInput = ref('')
const assistantOriginCity = ref('')
const assistantLoading = ref(false)
const recommendationCount = ref(3)
const assistantMessages = ref<ChatMessage[]>([
  {
    role: 'assistant',
    content: '不确定去哪也没关系。告诉我预算、天数和你想要的旅行感觉,我会给你几个可选目的地。'
  }
])
const recommendations = ref<DestinationRecommendation[]>([])
const history = ref({
  stats: { total_trips: 0, avg_budget: 0, total_days: 0 },
  fav_cities: [] as { city: string; count: number }[],
  trips: [] as any[]
})

onMounted(async () => {
  history.value = await fetchTripHistory('u_current')
})

// Refresh history after generating a plan
const refreshHistory = async () => {
  history.value = await fetchTripHistory('u_current')
}

const openHistoryTrip = async (planNo: string) => {
  try {
    const response = await fetchTripPlan(planNo, 'u_current')
    if (!response.data) throw new Error('历史计划数据为空')
    saveTripCache(response.data, planNo)
    sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
    await router.push({ path: '/result', query: { plan: planNo } })
  } catch (error: any) {
    message.error(error.message || '历史计划读取失败')
  }
}

const transportationOptions = ['公共交通', '自驾', '步行', '混合']
const intercityTransportationOptions = ['自动选择', '火车/高铁', '飞机', '自驾']
const quickPrompts = ['周末短途', '预算有限', '美食优先', '自然风光', '历史文化']
const recommendationCountOptions = [
  { label: '1个', value: 1 },
  { label: '2个', value: 2 },
  { label: '3个', value: 3 }
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

const budgetText = computed(() => {
  return formData.budget ? `¥${formData.budget}` : '未设置'
})

const sendAssistantMessage = async (content?: string) => {
  const text = (content ?? assistantInput.value).trim()
  if (!text) {
    message.warning('先说说你想要的旅行感觉')
    return
  }

  assistantMessages.value.push({ role: 'user', content: text })
  assistantInput.value = ''
  assistantLoading.value = true

  try {
    const response = await chatDestinationRecommendation({
      messages: assistantMessages.value,
      context: {
        origin_city: assistantOriginCity.value.trim() || formData.origin_city?.trim() || null,
        budget: formData.budget,
        travel_days: formData.travel_days,
        recommendation_count: recommendationCount.value,
        preferences: formData.preferences,
        transportation: formData.transportation,
        accommodation: formData.accommodation
      }
    })

    assistantMessages.value.push({
      role: 'assistant',
      content: response.reply || response.message || '我给你整理了几个方向。'
    })
    recommendations.value = response.recommendations || []
  } catch (error: any) {
    message.error(error.message || '获取推荐失败')
  } finally {
    assistantLoading.value = false
  }
}

const askQuickPrompt = (prompt: string) => {
  const budget = formData.budget ? `预算${formData.budget}元` : '预算先不确定'
  const days = formData.travel_days ? `玩${formData.travel_days}天` : '天数未定'
  const currentOrigin = assistantOriginCity.value || formData.origin_city || ''
  const origin = currentOrigin ? `从${currentOrigin}出发` : '出发地未定'
  sendAssistantMessage(`${prompt}, ${origin}, ${days}, ${budget}, 交通偏好${formData.transportation}`)
}

const useRecommendation = (item: DestinationRecommendation) => {
  const patch = item.form_patch
  formData.city = patch.city
  if (patch.budget !== null && patch.budget !== undefined) {
    formData.budget = patch.budget
  }
  if (patch.travel_days) {
    formData.travel_days = patch.travel_days
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
  message.success(`已选择${patch.city},并回填到旅行计划`)
}

watch([() => formData.start_date, () => formData.end_date], ([start, end]) => {
  if (!start || !end) {
    formData.travel_days = 1
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

const handleSubmit = async () => {
  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }

  loading.value = true
  loadingProgress.value = 0
  loadingStatus.value = '正在初始化'

  const progressInterval = setInterval(() => {
    if (loadingProgress.value >= 90) return

    loadingProgress.value = Math.min(90, loadingProgress.value + 8)
    if (loadingProgress.value <= 30) {
      loadingStatus.value = '正在搜索景点'
    } else if (loadingProgress.value <= 50) {
      loadingStatus.value = '正在查询天气'
    } else if (loadingProgress.value <= 70) {
      loadingStatus.value = '正在推荐酒店'
    } else {
      loadingStatus.value = '正在生成行程计划'
    }
  }, 500)

  try {
    const requestData: TripFormData = {
      origin_city: formData.origin_city?.trim() || assistantOriginCity.value.trim() || null,
      city: formData.city.trim(),
      start_date: formData.start_date.format('YYYY-MM-DD'),
      end_date: formData.end_date.format('YYYY-MM-DD'),
      travel_days: formData.travel_days,
      travelers: formData.travelers,
      budget: formData.budget,
      transportation: formData.transportation,
      intercity_transportation: formData.intercity_transportation || null,
      accommodation: formData.accommodation,
      preferences: formData.preferences,
      free_text_input: formData.free_text_input.trim()
    }

    const response = await generateTripPlan(requestData)
    console.info('[home] trip plan response received', {
      success: response.success,
      hasData: Boolean(response.data),
      city: response.data?.city
    })
    loadingProgress.value = 100
    loadingStatus.value = '完成'

    if (response.success && response.data) {
      sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
      saveTripCache(response.data, response.plan_no)
      refreshHistory()
      message.success('旅行计划生成成功')
      setTimeout(() => {
        router.push('/result')
      }, 500)
    } else {
      message.error(response.message || '生成失败')
    }
  } catch (error: any) {
    message.error(error.message || '生成旅行计划失败,请稍后重试')
  } finally {
    clearInterval(progressInterval)
    setTimeout(() => {
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
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

.preference-grid :deep(.ant-checkbox-wrapper:hover) {
  border-color: #0f766e;
  background: #f0fdfa;
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
</style>
