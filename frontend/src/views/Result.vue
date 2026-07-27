<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        <ArrowLeftOutlined />
        <span>返回首页</span>
      </a-button>
      <a-space size="middle" wrap>
        <div v-if="tripPlan" class="draft-retention">
          <span>草稿保留</span>
          <a-select v-model:value="cacheRetention" size="small" @change="changeCacheRetention">
            <a-select-option :value="5">5分钟</a-select-option>
            <a-select-option :value="10">10分钟</a-select-option>
            <a-select-option :value="60">1小时</a-select-option>
            <a-select-option :value="0">长期</a-select-option>
          </a-select>
        </div>
        <a-button v-if="!editMode" @click="toggleEditMode" type="default">
          <EditOutlined />
          <span>编辑行程</span>
        </a-button>
        <a-button v-else @click="saveChanges" type="primary">
          <SaveOutlined />
          <span>保存修改</span>
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          <CloseOutlined />
          <span>取消编辑</span>
        </a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            <ExportOutlined />
            <span>导出行程</span>
            <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="tripPlan" class="result-hero">
      <div>
        <div class="hero-kicker">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</div>
        <h1>{{ tripPlan.city }}旅行计划</h1>
      </div>
      <div class="hero-metrics">
        <div class="metric-item">
          <span>天数</span>
          <strong>{{ totalDays }}</strong>
        </div>
        <div class="metric-item">
          <span>景点</span>
          <strong>{{ totalAttractions }}</strong>
        </div>
        <div class="metric-item">
          <span>预算</span>
          <strong>{{ totalBudgetText }}</strong>
        </div>
      </div>
    </div>

    <section
      v-if="tripPlan"
      class="trip-readiness"
      :class="'is-' + readinessTone"
      aria-labelledby="readiness-title"
    >
      <div class="readiness-copy">
        <span class="readiness-kicker">AI EXECUTIVE BRIEF · {{ generationModeLabel }}</span>
        <div class="readiness-title-row">
          <h2 id="readiness-title">{{ readinessTitle }}</h2>
          <span class="readiness-badge">{{ readinessBadge }}</span>
        </div>
        <p>{{ tripPlan.overall_suggestions }}</p>
      </div>
      <div class="readiness-metrics">
        <div><span>已验证事实</span><strong>{{ verifiedFactsDisplay }}</strong></div>
        <div><span>可靠路线</span><strong>{{ verifiedRouteCount }}</strong></div>
        <div><span>需留意</span><strong>{{ qualityIssues.length }}</strong></div>
      </div>
      <div v-if="topQualityIssues.length" class="readiness-focus">
        <div class="focus-head">
          <span>出发前优先确认</span>
          <button type="button" @click="scrollToSection({ key: 'travel-reminders' })">查看全部</button>
        </div>
        <ul>
          <li v-for="issue in topQualityIssues" :key="issue.code + issue.title">
            <i :class="'is-' + issue.severity"></i>
            <span>{{ issue.title }}</span>
          </li>
        </ul>
      </div>
      <div v-else-if="isServerBackedPlan && tripPlan.quality" class="readiness-clear">
        <span>✓</span>当前未发现结构性问题；票务、开放状态和实时天气仍以出发前官方信息为准
      </div>
      <div v-else class="readiness-unchecked">
        <span>!</span>当前内容来自浏览器缓存，天气、预算、地图、餐饮和评分均不作为服务端验证结论
      </div>
    </section>

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="travel-reminders" v-if="qualityIssuesList.length">
              <span>🔔 出发前提醒</span>
            </a-menu-item>
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
            <a-menu-item key="web-guide" v-if="tripPlan.web_guide">
              <span>🌐 联网攻略</span>
            </a-menu-item>
            <a-menu-item key="web-meta" v-if="normalizedWebReferences.length">
              <span>🔎 资料来源</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 面向旅行者的出发前提醒 -->
        <a-card
          id="travel-reminders"
          v-if="qualityIssuesList.length > 0"
          class="quality-advisory-card"
          :bordered="false"
        >
            <div class="quality-advisory-header">
              <div class="quality-advisory-title">
                <span class="advisory-icon">💡</span>
                <div class="advisory-heading-copy">
                  <span class="advisory-text-title">出发前提醒</span>
                  <span class="advisory-count-badge">{{ qualityIssuesList.length }} 项信息建议提前确认</span>
                </div>
              </div>
              <a-button
                v-if="qualityIssuesList.length > 3"
                type="link"
                size="small"
                @click="toggleQualityExpand"
              >
                {{ isQualityExpanded ? '收起提醒' : `查看全部（${qualityIssuesList.length}）` }}
              </a-button>
            </div>
            <div class="quality-advisory-body">
            <div
              v-for="(item, idx) in visibleQualityIssues"
              :key="idx"
              class="quality-issue-item"
              :class="item.severity || 'warning'"
            >
              <div class="issue-main">
                <span class="issue-category">{{ item.category }}</span>
                <span class="issue-message">{{ item.title }}</span>
              </div>
              <div v-if="item.action" class="issue-suggestion">
                <span>建议</span>
                {{ item.action }}
              </div>
            </div>
          </div>
        </a-card>

        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期:</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 建议:</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                </div>
              </div>
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="📍 高德交互地图" :bordered="false" class="map-card interactive-map-card">
              <div v-if="isMobileViewport" class="mobile-map-summary">
                <div v-for="item in mapSummaryAttractions" :key="`${item.dayIndex}-${item.name}`" class="mobile-map-item">
                  <span>{{ item.dayIndex + 1 }}</span>
                  <div>
                    <strong>{{ item.name }}</strong>
                    <p>{{ item.address }}</p>
                  </div>
                </div>
              </div>
              <div v-else id="amap-container" style="width: 100%; height: 100%"></div>
            </a-card>
          </div>
        </div>

        <TripMapPoster :plan="tripPlan" />

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays">
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <span class="day-date">{{ day.date }}</span>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
              </div>

              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card :title="item.name" size="small" class="attraction-card">
                      <!-- 编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1"
                          >
                            ↓
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            🗑️
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper" :class="{ 'no-photo': !getAttractionImage(item) }">
                        <img
                          v-if="getAttractionImage(item)"
                          :src="getAttractionImage(item)"
                          :alt="item.name"
                          class="attraction-image"
                          @error="handleImageError(item.name)"
                        />
                        <div v-else class="attraction-no-photo">
                          <span>第{{ day.day_index + 1 }}天 · 景点{{ index + 1 }}</span>
                          <strong>{{ item.name }}</strong>
                          <small>{{ item.address }}</small>
                        </div>
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <a-tag color="green" style="margin-bottom: 8px">地图事实不可直接修改</a-tag>

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p><strong>描述:</strong> {{ item.description }}</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                        <a-tag v-if="isServerBackedPlan && item.coordinate_source === 'amap_poi'" color="green">高德 POI 坐标已校准</a-tag>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- 路线规划 -->
              <a-divider v-if="day.routes && day.routes.length" orientation="left">🧭 路线规划</a-divider>
              <div v-if="day.routes && day.routes.length" class="route-list">
                <div v-for="route in day.routes" :key="`${route.from_name}-${route.to_name}`" class="route-segment">
                  <div class="route-head">
                    <span class="route-title">{{ route.from_name }} → {{ route.to_name }}</span>
                    <span class="route-mode">
                      {{ getRouteTypeLabel(route.route_type) }}
                      · {{ isRouteServerVerified(route) ? '高德路线已校验' : '路线摘要（需复核）' }}
                    </span>
                  </div>
                  <div class="route-meta">
                    <span v-if="route.distance">{{ formatRouteDistance(route.distance) }}</span>
                    <span v-if="route.duration">{{ formatRouteDuration(route.duration) }}</span>
                  </div>
                  <p class="route-desc">{{ route.description }}</p>
                </div>
              </div>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span class="hotel-title">{{ day.hotel.name }}</span>
                </template>
                <a-descriptions :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                  <a-descriptions-item v-if="day.hotel.selection_reason" label="选址依据" :span="2">
                    {{ day.hotel.selection_reason }}
                  </a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
              <a-descriptions :column="1" bordered size="small">
                <a-descriptions-item
                  v-for="meal in day.meals"
                  :key="meal.type"
                  :label="getMealLabel(meal.type)"
                >
                  <div class="meal-detail">
                    <div class="meal-heading">
                      <strong>{{ meal.name }}</strong>
                      <span v-if="meal.estimated_cost" class="meal-cost">参考 ¥{{ meal.estimated_cost }}</span>
                    </div>
                    <div v-if="meal.address" class="meal-address">地址：{{ meal.address }}</div>
                    <p v-if="meal.description" class="meal-description">{{ meal.description }}</p>
                  </div>
                </a-descriptions-item>
              </a-descriptions>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" :title="weatherSectionTitle" style="margin-top: 20px" :bordered="false">
          <a-list
            v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0"
            :data-source="tripPlan.weather_info"
            :grid="{ gutter: 16, xs: 1, sm: 2, md: 3, lg: 3, xl: 3 }"
          >
            <template #renderItem="{ item }">
              <a-list-item>
                <a-card size="small" class="weather-card">
                  <div class="weather-date">{{ item.date }}</div>
                  <div class="weather-info-row">
                    <span class="weather-icon">☀️</span>
                    <div>
                      <div class="weather-label">白天</div>
                      <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                    </div>
                  </div>
                  <div class="weather-info-row">
                    <span class="weather-icon">🌙</span>
                    <div>
                      <div class="weather-label">夜间</div>
                      <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                    </div>
                  </div>
                  <div class="weather-wind">
                    💨 {{ item.wind_direction }} {{ item.wind_power }}
                  </div>
                </a-card>
              </a-list-item>
            </template>
          </a-list>
          <div v-else class="weather-empty">
            {{ weatherReviewText }}
          </div>
        </a-card>

        <a-card
          id="web-guide"
          v-if="tripPlan.web_guide"
          title="🌐 联网攻略"
          :bordered="false"
          class="web-guide-card"
        >
          <div class="web-guide-content" v-html="renderedWebGuide"></div>
        </a-card>

        <a-card
          id="web-meta"
          v-if="normalizedWebReferences.length"
          title="🔎 资料来源"
          :bordered="false"
          class="web-meta-card"
        >
          <div class="reference-list">
            <a
              v-for="reference in normalizedWebReferences"
              :key="reference.url || reference.title"
              :href="reference.url || undefined"
              target="_blank"
              rel="noopener noreferrer"
              class="reference-item"
            >
              <span class="reference-name">{{ reference.title || reference.site_name || '未命名来源' }}</span>
              <span v-if="reference.site_name" class="reference-site">{{ reference.site_name }}</span>
            </a>
          </div>
        </a-card>

      </div>
    </div>

    <div v-else-if="isPlanLoading" class="result-loading">
      <a-spin size="large" :tip="planLoadingText" />
    </div>
    <a-empty v-else description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  CloseOutlined,
  DownOutlined,
  EditOutlined,
  ExportOutlined,
  SaveOutlined
} from '@ant-design/icons-vue'
import TripMapPoster from '@/components/TripMapPoster.vue'
import apiClient, {
  fetchMapContext,
  fetchTripPlan,
  PHOTO_REQUEST_TIMEOUT_MS,
  updateTripPlan
} from '@/services/api'
import { getCurrentUser } from '@/services/auth'
import {
  getCacheRetention,
  loadTripCache,
  loadTripSession,
  saveTripCache,
  saveTripSession,
  setCacheRetention,
  type CacheRetentionMinutes
} from '@/services/tripCache'
import type { Attraction, TripPlan } from '@/types'

const route = useRoute()
const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const isPlanLoading = ref(true)
const planLoadingText = '\u6b63\u5728\u6062\u590d\u65c5\u884c\u8ba1\u5212...'
type PlanSource = 'server' | 'local'

const currentPlanNo = ref<string | null>(null)
const planSource = ref<PlanSource>('local')
const isServerBackedPlan = computed(() => planSource.value === 'server')
const cacheRetention = ref<CacheRetentionMinutes>(getCacheRetention())
const editMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const brokenAttractionImages = ref<Set<string>>(new Set())
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
const isQualityExpanded = ref(false)
let map: any = null

const isMobileViewport = ref(false)
let draftTimer: number | undefined
let mapResizeTimer: number | undefined
let activationRevision = 0
let viewDisposed = false
let authContextInvalidated = false
const viewOwnerUserId = getCurrentUser()?.user_id ?? null

const isViewOwnerActive = () => (
  !viewDisposed
  && !authContextInvalidated
  && (getCurrentUser()?.user_id ?? null) === viewOwnerUserId
)

const handleAuthContextChange = () => {
  if (authContextInvalidated || (getCurrentUser()?.user_id ?? null) === viewOwnerUserId) return
  authContextInvalidated = true
  activationRevision += 1
  window.clearTimeout(draftTimer)
  window.clearTimeout(mapResizeTimer)
  tripPlan.value = null
  currentPlanNo.value = null
  if (map) {
    map.destroy()
    map = null
  }
  message.info('\u8d26\u53f7\u5df2\u5207\u6362\uff0c\u4e0a\u4e00\u8d26\u53f7\u7684\u884c\u7a0b\u5df2\u5173\u95ed\u3002')
  void router.replace('/')
}

const totalDays = computed(() => tripPlan.value?.days.length ?? 0)
const totalAttractions = computed(() => {
  return tripPlan.value?.days.reduce((sum, day) => sum + day.attractions.length, 0) ?? 0
})
const mapSummaryAttractions = computed(() => {
  return (tripPlan.value?.days ?? [])
    .flatMap(day =>
      day.attractions.map(attraction => ({
        dayIndex: day.day_index,
        name: attraction.name,
        address: attraction.address
      }))
    )
    .slice(0, 8)
})
const normalizedWebReferences = computed(() => tripPlan.value?.web_references ?? [])
type UserFacingQualityIssue = {
  code: string
  severity: string
  category: string
  title: string
  action: string
}

const HIDDEN_TRAVELER_ISSUE_CODES = new Set([
  'MODEL_OUTPUT_REPAIRED',
  'FACT_COVERAGE_INCOMPLETE',
  'WEB_AUDIT_MISSING',
  'WEB_AUDIT_FAILED',
  'WEB_AUDIT_WARNING',
  'WEB_AUDIT_NO_REFERENCES',
  'WEB_AUDIT_FORMAT_ONLY'
])

const INTERNAL_BUDGET_ISSUE_CODES = new Set([
  'BUDGET_HOTEL_MULTIPLIER',
  'BUDGET_HOTEL_NIGHTS_MISMATCH',
  'BUDGET_HOTEL_ROOMS_MISMATCH',
  'BUDGET_MEAL_MULTIPLIER',
  'BUDGET_NEGATIVE_COMPONENT',
  'BUDGET_SUM_MISMATCH',
  'BUDGET_TICKET_MULTIPLIER',
  'BUDGET_TRANSPORT_BREAKDOWN_MISMATCH',
  'BUDGET_TRANSPORT_MULTIPLIER'
])

const normalizeTravelerCopy = (value: string): string =>
  value
    .replace(/地图\s*POI/gi, '地图地点')
    .replace(/\bPOI\b/gi, '地点')
    .replace(/语义事实验证/g, '信息确认')
    .replace(/事实验证/g, '信息确认')
    .replace(/语义核对/g, '逐项确认')
    .replace(/兜底估算/g, '参考估算')
    .replace(/审核检查/g, '核查详情')
    .replace(/校验/g, '核对')
    .replace(/审核/g, '检查')
    .trim()

const issueCategory = (code: string): string => {
  if (/DAY_|SCHEDULE|PACE|VISIT_TIME|ATTRACTION_TYPE|TOO_MANY/.test(code)) return '行程节奏'
  if (/TICKET/.test(code)) return '门票预约'
  if (/HOTEL|ACCOMMODATION/.test(code)) return '住宿'
  if (/BUDGET|PRICE/.test(code)) return '费用预算'
  if (/ROUTE|TRANSPORT/.test(code)) return '交通路线'
  if (/WEATHER/.test(code)) return '天气'
  if (/MEAL|RESTAURANT/.test(code)) return '餐饮'
  if (/WEB_|FACT_|REFERENCE/.test(code)) return '信息时效'
  if (/POI|COORDINATE|CITY|DESTINATION/.test(code)) return '地点信息'
  return '出行准备'
}

const presentQualityIssue = (
  code: string,
  severity: string,
  message: string,
  suggestion: string
): UserFacingQualityIssue => {
  const normalizedCode = code || 'TRAVEL_REMINDER'
  let category = issueCategory(normalizedCode)
  let title = normalizeTravelerCopy(message)
  let action = normalizeTravelerCopy(suggestion)

  if (normalizedCode === 'DAY_SCHEDULE_OVERLOAD' || normalizedCode === 'DAY_SCHEDULE_IMPOSSIBLE') {
    const day = message.match(/第(\d+)天/)?.[1]
    const hours = message.match(/约([\d.]+)小时/)?.[1]
    category = '行程节奏'
    title = `${day ? `第${day}天` : '当天'}安排较满${hours ? `，预计活动约 ${hours} 小时` : ''}`
    action = '优先保留最想去的景点，减少折返，并为用餐和休息预留时间。'
  } else if (normalizedCode === 'TICKET_PRICE_UNAVAILABLE') {
    const attractionNames = message.match(/：([^。]+)/)?.[1]
    category = '门票预约'
    title = attractionNames
      ? `${attractionNames}的门票价格尚未计入预算`
      : '部分景点门票价格尚未计入预算'
    action = '出发前通过景区官方渠道确认票价、优惠政策和预约要求。'
  } else if (normalizedCode === 'HOTEL_PRICE_UNVERIFIED') {
    category = '住宿预算'
    title = '当前住宿费用为参考估算'
    action = '预订前在酒店官方渠道或可信平台查看实际房价。'
  } else if (INTERNAL_BUDGET_ISSUE_CODES.has(normalizedCode)) {
    category = '费用预算'
    title = '预算明细需要重新计算'
    action = '建议重新生成一次计划，并以实际预订价格为准。'
  } else if (normalizedCode.startsWith('SEMANTIC_')) {
    category = '需求确认'
    title = '生成结果与已确认的旅行需求存在差异'
    action = '返回首页核对目的地、日期、人数、预算和偏好后重新生成。'
  } else if (normalizedCode === 'UNVERIFIED_ROUTE') {
    category = '交通路线'
    title = normalizeTravelerCopy(message).replace('只有路线摘要，尚未取得完整折线', '的详细路线将在导航时生成')
    action = '实际出行时打开地图导航，并以实时路况为准。'
  }

  return {
    code: normalizedCode,
    severity: severity || 'warning',
    category,
    title,
    action
  }
}

const qualityIssuesList = computed<UserFacingQualityIssue[]>(() => {
  if (!tripPlan.value) return []
  const items: UserFacingQualityIssue[] = []
  const scheduleIssues: Array<{ severity: string; message: string }> = []
  if (tripPlan.value.quality && Array.isArray(tripPlan.value.quality.issues)) {
    for (const issue of tripPlan.value.quality.issues) {
      if (issue && issue.message) {
        const code = issue.code || ''
        if (HIDDEN_TRAVELER_ISSUE_CODES.has(code) || code.startsWith('WEB_AUDIT_')) {
          continue
        }
        if (code === 'DAY_SCHEDULE_OVERLOAD' || code === 'DAY_SCHEDULE_IMPOSSIBLE') {
          scheduleIssues.push({
            severity: issue.severity || 'warning',
            message: issue.message
          })
          continue
        }
        items.push(presentQualityIssue(
          code,
          issue.severity || 'warning',
          issue.message,
          issue.suggestion || ''
        ))
      }
    }
  }

  if (scheduleIssues.length) {
    const days = Array.from(new Set(
      scheduleIssues
        .map(issue => issue.message.match(/第(\d+)天/)?.[1])
        .filter((day): day is string => Boolean(day))
    )).sort((left, right) => Number(left) - Number(right))
    const hasImpossibleDay = scheduleIssues.some(issue => issue.severity === 'error')
    const dayLabel = days.length
      ? `第${days.join('、')}天`
      : `有 ${scheduleIssues.length} 天`
    items.unshift({
      code: 'DAY_SCHEDULE_SUMMARY',
      severity: hasImpossibleDay ? 'error' : 'warning',
      category: '行程节奏',
      title: `${dayLabel}${hasImpossibleDay ? '的安排可能无法按时完成' : '安排较满'}`,
      action: '优先保留最想去的景点，减少折返，并为用餐和休息预留时间。'
    })
  }

  const seen = new Set<string>()
  return items.filter(item => {
    const key = `${item.category}\u0000${item.title}\u0000${item.action}`
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
})
const visibleQualityIssues = computed(() => {
  if (isQualityExpanded.value) return qualityIssuesList.value
  return qualityIssuesList.value.slice(0, 3)
})
function toggleQualityExpand() {
  isQualityExpanded.value = !isQualityExpanded.value
}
const displayWebGuide = computed(() => {
  const text = tripPlan.value?.web_guide?.trim() ?? ''
  return text
    .replace(/\n+#{0,6}\s*资料来源[:：]?[\s\S]*$/u, '')
    .replace(/\n+#{0,6}\s*审核检查[:：]?[\s\S]*$/u, '')
    .trim()
})
const renderedWebGuide = computed(() => renderMarkdown(displayWebGuide.value))
const totalBudgetText = computed(() => {
  const budget = tripPlan.value?.budget
  const total = budget?.known_total ?? budget?.total
  if (typeof total !== 'number') return '待估算'
  const pendingCount = budget?.pending_ticket_items?.length ?? 0
  const suffix = pendingCount > 0 ? ` + ${pendingCount}项门票待核实` : ''
  return isServerBackedPlan.value ? `已知 ¥${total}${suffix}` : `缓存参考 ¥${total}${suffix}`
})
const weatherSectionTitle = computed(() =>
  isServerBackedPlan.value ? '天气信息' : '天气信息（浏览器缓存，需重新核验）'
)
const weatherReviewText = computed(() => {
  if (!tripPlan.value) return '暂未获取到可展示的天气信息。'
  return `${tripPlan.value.city}${tripPlan.value.start_date} 至 ${tripPlan.value.end_date} 的逐日天气暂未获取到可靠预报。当前天气源可能只覆盖近期预报，请在出发前3-7天复核每日天气、温差和降雨。`
})
const escapeHtml = (value: string): string => {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

const renderInlineMarkdown = (value: string): string => {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    )
}

const isGuideHeading = (line: string, lineIndex: number): boolean => {
  if (lineIndex === 0 && line.length <= 24) {
    return true
  }
  if (!/[：:]$/.test(line)) {
    return false
  }
  return line.length <= 18 && !/[。；;，,]/.test(line)
}

const renderMarkdown = (source: string): string => {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let listType: 'ol' | 'ul' | null = null

  const closeParagraph = () => {
    if (!paragraph.length) return
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`)
    paragraph = []
  }

  const closeList = () => {
    if (!listType) return
    html.push(`</${listType}>`)
    listType = null
  }

  const openList = (type: 'ol' | 'ul') => {
    if (listType === type) return
    closeList()
    html.push(`<${type}>`)
    listType = type
  }

  lines.forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line) {
      closeParagraph()
      closeList()
      return
    }

    const markdownHeading = line.match(/^(#{1,4})\s+(.+)$/)
    if (markdownHeading) {
      closeParagraph()
      closeList()
      const level = Math.min(Math.max(markdownHeading[1].length, 2), 4)
      html.push(`<h${level}>${renderInlineMarkdown(markdownHeading[2].trim())}</h${level}>`)
      return
    }

    const ordered = line.match(/^\d+[.)]\s+(.+)$/)
    if (ordered) {
      closeParagraph()
      openList('ol')
      html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`)
      return
    }

    const unordered = line.match(/^[-*]\s+(.+)$/)
    if (unordered) {
      closeParagraph()
      openList('ul')
      html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`)
      return
    }

    if (isGuideHeading(line, index)) {
      closeParagraph()
      closeList()
      html.push(`<h3>${renderInlineMarkdown(line.replace(/[：:]$/, ''))}</h3>`)
      return
    }

    closeList()
    paragraph.push(line)
  })

  closeParagraph()
  closeList()
  return html.join('')
}

const isRecord = (value: unknown): value is Record<string, any> => (
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)
)

const boundedText = (value: unknown, maxLength: number, fallback = ''): string => (
  typeof value === 'string' ? value.slice(0, maxLength) : fallback
)

const boundedNumber = (value: unknown, fallback = 0): number => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const safeHttpUrl = (value: unknown): string | undefined => {
  if (typeof value !== 'string' || !value || value.length > 2048) return undefined
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : undefined
  } catch {
    return undefined
  }
}

const normalizeLocation = (value: unknown) => {
  if (!isRecord(value)) return null
  const longitude = boundedNumber(value.longitude, Number.NaN)
  const latitude = boundedNumber(value.latitude, Number.NaN)
  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return null
  if (Math.abs(longitude) > 180 || Math.abs(latitude) > 90) return null
  return { longitude, latitude }
}

const normalizeTripPlan = (raw: any): TripPlan | null => {
  if (!isRecord(raw)) return null
  const city = boundedText(raw.city, 64).trim()
  const startDate = boundedText(raw.start_date, 10)
  const endDate = boundedText(raw.end_date, 10)
  if (!city || !startDate || !endDate) return null

  const days = (Array.isArray(raw.days) ? raw.days : [])
    .slice(0, 30)
    .filter(isRecord)
    .map((day: Record<string, any>, dayIndex: number) => {
      const attractions = (Array.isArray(day.attractions) ? day.attractions : [])
        .slice(0, 10)
        .filter(isRecord)
        .map((item: Record<string, any>) => {
          const location = normalizeLocation(item.location)
          const name = boundedText(item.name, 200).trim()
          if (!location || !name) return null
          return {
            name,
            address: boundedText(item.address, 500),
            location,
            visit_duration: Math.min(1440, Math.max(0, boundedNumber(item.visit_duration))),
            description: boundedText(item.description, 4000),
            category: boundedText(item.category, 200, '景点'),
            rating: item.rating == null ? undefined : boundedNumber(item.rating),
            photos: (Array.isArray(item.photos) ? item.photos : [])
              .slice(0, 10)
              .map(safeHttpUrl)
              .filter((url): url is string => Boolean(url)),
            image_url: safeHttpUrl(item.image_url),
            poi_id: boundedText(item.poi_id, 200),
            coordinate_source: boundedText(item.coordinate_source, 100),
            ticket_price: Math.max(0, boundedNumber(item.ticket_price))
          }
        })
        .filter(Boolean)

      const routes = (Array.isArray(day.routes) ? day.routes : [])
        .slice(0, 20)
        .filter(isRecord)
        .map((route: Record<string, any>) => ({
          from_name: boundedText(route.from_name, 200),
          to_name: boundedText(route.to_name, 200),
          origin_address: boundedText(route.origin_address, 500),
          destination_address: boundedText(route.destination_address, 500),
          route_type: boundedText(route.route_type, 32, 'walking'),
          distance: Math.max(0, boundedNumber(route.distance)),
          duration: Math.max(0, boundedNumber(route.duration)),
          description: boundedText(route.description, 2000),
          path: (Array.isArray(route.path) ? route.path : [])
            .slice(0, 5000)
            .map(normalizeLocation)
            .filter(Boolean),
          source: boundedText(route.source, 100),
          verified: route.verified === true
        }))

      const meals = (Array.isArray(day.meals) ? day.meals : [])
        .slice(0, 10)
        .filter(isRecord)
        .map((meal: Record<string, any>) => ({
          type: ['breakfast', 'lunch', 'dinner', 'snack'].includes(meal.type) ? meal.type : 'snack',
          name: boundedText(meal.name, 200, '餐饮待确认'),
          address: boundedText(meal.address, 500) || undefined,
          location: normalizeLocation(meal.location) || undefined,
          description: boundedText(meal.description, 2000) || undefined,
          estimated_cost: Math.max(0, boundedNumber(meal.estimated_cost)),
          poi_id: boundedText(meal.poi_id, 200),
          coordinate_source: boundedText(meal.coordinate_source, 100)
        }))

      const hotel = isRecord(day.hotel)
        ? {
            name: boundedText(day.hotel.name, 200, '住宿待确认'),
            address: boundedText(day.hotel.address, 500),
            location: normalizeLocation(day.hotel.location) || undefined,
            price_range: boundedText(day.hotel.price_range, 200),
            rating: boundedText(day.hotel.rating, 100),
            distance: boundedText(day.hotel.distance, 500),
            type: boundedText(day.hotel.type, 200),
            estimated_cost: Math.max(0, boundedNumber(day.hotel.estimated_cost)),
            poi_id: boundedText(day.hotel.poi_id, 200),
            selection_reason: boundedText(day.hotel.selection_reason, 2000)
          }
        : undefined

      return {
        date: boundedText(day.date, 10),
        day_index: Math.max(0, boundedNumber(day.day_index, dayIndex)),
        description: boundedText(day.description, 4000),
        transportation: boundedText(day.transportation, 200),
        accommodation: boundedText(day.accommodation, 200),
        hotel,
        attractions,
        routes,
        meals
      }
    })

  const budget = isRecord(raw.budget)
    ? {
        ...raw.budget,
        total_attractions: Math.max(0, boundedNumber(raw.budget.total_attractions)),
        total_hotels: Math.max(0, boundedNumber(raw.budget.total_hotels)),
        total_meals: Math.max(0, boundedNumber(raw.budget.total_meals)),
        total_transportation: Math.max(0, boundedNumber(raw.budget.total_transportation)),
        total: Math.max(0, boundedNumber(raw.budget.total)),
        known_total: Math.max(
          0,
          boundedNumber(raw.budget.known_total ?? raw.budget.total)
        ),
        pending_ticket_items: (
          Array.isArray(raw.budget.pending_ticket_items)
            ? raw.budget.pending_ticket_items
            : []
        ).map((item: unknown) => String(item)).filter(Boolean).slice(0, 50),
        hotel_nights: Math.max(0, boundedNumber(raw.budget.hotel_nights)),
        hotel_rooms: Math.max(0, boundedNumber(raw.budget.hotel_rooms, 1)),
        hotel_unit_price: Math.max(0, boundedNumber(raw.budget.hotel_unit_price)),
        intercity_transportation: Math.max(0, boundedNumber(raw.budget.intercity_transportation)),
        local_transportation: Math.max(0, boundedNumber(raw.budget.local_transportation)),
        transport_unit_price: Math.max(0, boundedNumber(raw.budget.transport_unit_price)),
        budget_source: boundedText(raw.budget.budget_source, 500),
        hotel_reference: boundedText(raw.budget.hotel_reference, 2000) || null,
        transport_reference: boundedText(raw.budget.transport_reference, 2000) || null,
        budget_notes: (Array.isArray(raw.budget.budget_notes) ? raw.budget.budget_notes : [])
          .slice(0, 50)
          .map((item: unknown) => boundedText(item, 1000))
      }
    : undefined

  const rawQuality = isRecord(raw.quality) ? raw.quality : null
  const quality = rawQuality
    ? {
        status: ['passed', 'warning', 'failed'].includes(rawQuality.status) ? rawQuality.status : 'warning',
        score: Math.min(100, Math.max(0, boundedNumber(rawQuality.score))),
        verified_facts: Math.max(0, boundedNumber(rawQuality.verified_facts)),
        generated_at: boundedText(rawQuality.generated_at, 100),
        checked_items: (Array.isArray(rawQuality.checked_items) ? rawQuality.checked_items : [])
          .slice(0, 100)
          .map((item: unknown) => boundedText(item, 1000)),
        issues: (Array.isArray(rawQuality.issues) ? rawQuality.issues : [])
          .slice(0, 100)
          .filter(isRecord)
          .map((issue: Record<string, any>) => ({
            code: boundedText(issue.code, 100),
            severity: ['info', 'warning', 'error'].includes(issue.severity) ? issue.severity : 'warning',
            path: boundedText(issue.path, 500),
            message: boundedText(issue.message, 2000),
            suggestion: boundedText(issue.suggestion, 2000),
            auto_repaired: issue.auto_repaired === true
          }))
      }
    : undefined

  const rawAudit = isRecord(raw.agent_audit) ? raw.agent_audit : null
  const agentAudit = rawAudit
    ? {
        status: ['passed', 'warning', 'failed'].includes(rawAudit.status) ? rawAudit.status : 'warning',
        source: boundedText(rawAudit.source, 200),
        checked_items: (Array.isArray(rawAudit.checked_items) ? rawAudit.checked_items : []).slice(0, 100).map((item: unknown) => boundedText(item, 1000)),
        issues: (Array.isArray(rawAudit.issues) ? rawAudit.issues : []).slice(0, 100).map((item: unknown) => boundedText(item, 2000)),
        suggestions: (Array.isArray(rawAudit.suggestions) ? rawAudit.suggestions : []).slice(0, 100).map((item: unknown) => boundedText(item, 2000))
      }
    : undefined

  const generationMode: TripPlan['generation_mode'] = (
    raw.generation_mode === 'repaired' || raw.generation_mode === 'map_fallback'
  ) ? raw.generation_mode : 'primary'

  return {
    city,
    start_date: startDate,
    end_date: endDate,
    generation_mode: generationMode,
    days: days as TripPlan['days'],
    weather_info: (Array.isArray(raw.weather_info) ? raw.weather_info : [])
      .slice(0, 30)
      .filter(isRecord)
      .map((weather: Record<string, any>) => ({
        date: boundedText(weather.date, 10),
        day_weather: boundedText(weather.day_weather, 100),
        night_weather: boundedText(weather.night_weather, 100),
        day_temp: boundedNumber(weather.day_temp),
        night_temp: boundedNumber(weather.night_temp),
        wind_direction: boundedText(weather.wind_direction, 100),
        wind_power: boundedText(weather.wind_power, 100)
      })),
    overall_suggestions: boundedText(raw.overall_suggestions, 20_000),
    budget: budget as TripPlan['budget'],
    web_guide: typeof raw.web_guide === 'string' ? raw.web_guide.slice(0, 50_000) : null,
    web_references: (Array.isArray(raw.web_references) ? raw.web_references : [])
      .slice(0, 50)
      .filter(isRecord)
      .map((reference: Record<string, any>) => ({
        title: boundedText(reference.title, 500),
        url: safeHttpUrl(reference.url) || '',
        site_name: boundedText(reference.site_name, 200),
        source_type: boundedText(reference.source_type, 100),
        publish_time: reference.publish_time == null ? null : boundedNumber(reference.publish_time)
      })),
    agent_audit: agentAudit,
    quality: quality as TripPlan['quality'],
    map_context: (Array.isArray(raw.map_context) ? raw.map_context : [])
      .slice(0, 100)
      .filter(isRecord)
      .map((poi: Record<string, any>) => {
        const location = normalizeLocation(poi.location)
        if (!location) return null
        return {
          name: boundedText(poi.name, 200),
          category: boundedText(poi.category, 100),
          address: boundedText(poi.address, 500),
          location,
          poi_id: boundedText(poi.poi_id, 200),
          source: boundedText(poi.source, 100)
        }
      })
      .filter((poi): poi is NonNullable<typeof poi> => Boolean(poi))
  }
}


const activatePlan = async (
  raw: unknown,
  planNo?: string | null,
  source: PlanSource = 'local'
): Promise<boolean> => {
  if (!isViewOwnerActive()) return false
  const currentActivation = ++activationRevision
  const normalized = normalizeTripPlan(raw)
  if (!normalized) throw new Error('旅行计划数据格式无效')
  currentPlanNo.value = planNo || null
  planSource.value = source
  tripPlan.value = normalized
  activeSection.value = (
    normalized.quality?.issues?.length
    || normalized.agent_audit?.issues?.length
  ) ? 'travel-reminders' : 'overview'
  saveTripCache(normalized, currentPlanNo.value, cacheRetention.value)
  saveTripSession(normalized)
  if (!normalized.map_context?.length) {
    const mapContext = await fetchMapContext(normalized)
    if (!isViewOwnerActive() || currentActivation !== activationRevision) return false
    if (mapContext.length && tripPlan.value) {
      tripPlan.value.map_context = mapContext
      saveTripCache(tripPlan.value, currentPlanNo.value, cacheRetention.value)
      saveTripSession(tripPlan.value)
      // Map context is useful for this view, but remains server-owned and is
      // never persisted by sending a client-supplied full plan back automatically.
    }
  }
  await nextTick()
  if (!isViewOwnerActive() || currentActivation !== activationRevision) return false
  if (!isMobileViewport.value) initMap()
  loadAttractionPhotos()
  return true
}

onMounted(async () => {
  window.addEventListener('lingtu-auth-change', handleAuthContextChange)
  isMobileViewport.value = window.matchMedia('(max-width: 768px)').matches
  const queryPlanNo = typeof route.query.plan === 'string' ? route.query.plan : ''
  try {
    if (queryPlanNo) {
      try {
        const response = await fetchTripPlan(queryPlanNo)
        if (response.data) {
          if (await activatePlan(response.data, response.plan_no || queryPlanNo, 'server')) return
        }
      } catch (error) {
        console.warn('[result] historical plan unavailable, using local draft', error)
      }
      if (!isViewOwnerActive()) return
    }

    const cached = loadTripCache()
    if (cached && (!queryPlanNo || cached.planNo === queryPlanNo)) {
      if (await activatePlan(cached.plan, cached.planNo, 'local')) return
    }

    const sessionDraft = queryPlanNo ? null : loadTripSession()
    if (sessionDraft) {
      if (await activatePlan(sessionDraft, null, 'local')) return
    }
    console.warn('[result] no cached or historical trip plan found')
  } catch (error: any) {
    console.error('[result] failed to load trip plan', error)
    message.error(error.message || '结果页数据无效，请重新生成行程')
  } finally {
    isPlanLoading.value = false
  }
})

onUnmounted(() => {
  viewDisposed = true
  activationRevision += 1
  window.removeEventListener('lingtu-auth-change', handleAuthContextChange)
  window.clearTimeout(draftTimer)
  window.clearTimeout(mapResizeTimer)
  if (map) {
    map.destroy()
    map = null
  }
})

watch(
  tripPlan,
  value => {
    if (!value || !isViewOwnerActive()) return
    window.clearTimeout(draftTimer)
    draftTimer = window.setTimeout(() => {
      if (isViewOwnerActive() && tripPlan.value === value) {
        saveTripCache(value, currentPlanNo.value, cacheRetention.value)
      }
    }, 250)
  },
  { deep: true }
)

const changeCacheRetention = (value: CacheRetentionMinutes) => {
  if (!isViewOwnerActive()) return
  cacheRetention.value = value
  setCacheRetention(value)
  if (tripPlan.value) saveTripCache(tripPlan.value, currentPlanNo.value, value)
  message.success(value === 0 ? '当前草稿将长期保留' : `当前草稿保留${value}分钟`)
}

const goBack = () => {
  router.push('/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 切换编辑模式
const toggleEditMode = () => {
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

// 保存修改
const saveChanges = async () => {
  if (!tripPlan.value || !isViewOwnerActive()) return

  if (currentPlanNo.value) {
    try {
      const response = await updateTripPlan(currentPlanNo.value, tripPlan.value)
      if (!isViewOwnerActive()) return
      if (!response.data) throw new Error('服务端未返回更新后的行程')
      tripPlan.value = normalizeTripPlan(response.data)
      if (!tripPlan.value) throw new Error('服务端返回的行程格式无效')
      planSource.value = 'server'
      message.success(response.message || '修改已重新检查并保存')
    } catch (error: any) {
      message.error(error.message || '修改后的行程未通过检查')
      return
    }
  } else {
    tripPlan.value.quality = null
    if (tripPlan.value.agent_audit) {
      const localEditWarning = '本地草稿已编辑，联网审核结论需要重新复核。'
      tripPlan.value.agent_audit = {
        ...tripPlan.value.agent_audit,
        status: 'warning',
        issues: tripPlan.value.agent_audit.issues.includes(localEditWarning)
          ? tripPlan.value.agent_audit.issues
          : [...tripPlan.value.agent_audit.issues, localEditWarning]
      }
    }
    message.warning('修改已保存为本地草稿，质量状态将在重新生成后更新')
  }

  if (!isViewOwnerActive()) return
  editMode.value = false
  saveTripSession(tripPlan.value)
  saveTripCache(tripPlan.value, currentPlanNo.value, cacheRetention.value)

  if (map) {
    window.clearTimeout(mapResizeTimer)
    map.destroy()
    map = null
  }
  nextTick(() => {
    if (isViewOwnerActive() && !isMobileViewport.value) {
      initMap()
    }
  })
}

// 取消编辑
const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  message.info('已取消编辑')
}

// 删除景点
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }

  day.attractions.splice(attrIndex, 1)
  message.success('景点已删除')
}

// 移动景点顺序
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

const getRouteTypeLabel = (type: string): string => {
  const labels: Record<string, string> = {
    walking: '步行',
    driving: '驾车',
    transit: '公共交通'
  }
  return labels[type] || type
}

const formatRouteDistance = (distance: number): string => {
  if (distance >= 1000) {
    return `${(distance / 1000).toFixed(1)}公里`
  }
  return `${Math.round(distance)}米`
}

const formatRouteDuration = (duration: number): string => {
  const minutes = Math.max(1, Math.round(duration / 60))
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    const rest = minutes % 60
    return rest ? `${hours}小时${rest}分钟` : `${hours}小时`
  }
  return `${minutes}分钟`
}

// 加载所有景点图片
const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return

  const promises: Promise<void>[] = []

  tripPlan.value.days.forEach(day => {
    day.attractions.forEach(attraction => {
      const primaryImage = attraction.image_url || attraction.photos?.[0]
      const fallbackImage = attractionPhotos.value[attraction.name]
      const primaryImageFailed = brokenAttractionImages.value.has(attraction.name)
      if (fallbackImage || (primaryImage && !primaryImageFailed)) return

      const promise = apiClient.get('/api/poi/photo', {
        params: { name: attraction.name },
        timeout: PHOTO_REQUEST_TIMEOUT_MS
      })
        .then(({ data }) => {
          if (data.success && data.data?.photo_url) {
            attractionPhotos.value[attraction.name] = data.data.photo_url
          }
        })
        .catch(err => {
          console.error(`获取${attraction.name}图片失败:`, err)
        })

      promises.push(promise)
    })
  })

  await Promise.allSettled(promises)
}

// 获取景点图片
const getAttractionImage = (attraction: Attraction): string | undefined => {
  const fallbackImage = attractionPhotos.value[attraction.name]
  if (brokenAttractionImages.value.has(attraction.name)) return fallbackImage

  return attraction.image_url
    || attraction.photos?.[0]
    || fallbackImage
    || undefined
}

const handleImageError = (name: string) => {
  brokenAttractionImages.value = new Set([...brokenAttractionImages.value, name])
}



// 导出为图片
const expandAllDaysForExport = async () => {
  if (!tripPlan.value) return

  activeDays.value = tripPlan.value.days.map((_, index) => index)
  await loadAttractionPhotos()
  await nextTick()
  await new Promise(resolve => window.setTimeout(resolve, 350))
}

const waitForExportImages = async (root: HTMLElement) => {
  const images = Array.from(root.querySelectorAll('img'))
  await Promise.all(images.map(image => {
    if (image.complete) return Promise.resolve()
    return new Promise<void>(resolve => {
      const finish = () => resolve()
      image.addEventListener('load', finish, { once: true })
      image.addEventListener('error', finish, { once: true })
      window.setTimeout(finish, 5000)
    })
  }))
}

const buildExportSummary = () => {
  const summary = document.createElement('section')
  summary.className = 'export-summary pdf-break-unit'
  summary.innerHTML = `
    <div class="export-summary__kicker">LINGTU TRIP DOSSIER</div>
    <div class="export-summary__headline">
      <div>
        <h1>${escapeHtml(tripPlan.value?.city || '旅行计划')}</h1>
        <p>${escapeHtml(tripPlan.value?.start_date || '')} 至 ${escapeHtml(tripPlan.value?.end_date || '')}</p>
      </div>
      <div class="export-summary__metrics">
        <div><span>天数</span><strong>${totalDays.value}</strong></div>
        <div><span>景点</span><strong>${totalAttractions.value}</strong></div>
        <div><span>预算</span><strong>${escapeHtml(totalBudgetText.value)}</strong></div>
      </div>
    </div>
  `
  return summary
}

const createExportContainer = async () => {
  const element = document.querySelector('.main-content') as HTMLElement | null
  if (!element) throw new Error('未找到导出内容区域')

  const container = element.cloneNode(true) as HTMLElement
  container.classList.add('export-render')
  container.style.width = '1120px'
  container.style.padding = '24px'
  container.style.background = '#ffffff'
  container.style.position = 'fixed'
  container.style.left = '-12000px'
  container.style.top = '0'
  container.style.zIndex = '-1'

  const printStyle = document.createElement('style')
  printStyle.textContent = `
    .export-render {
      color: #101828 !important;
      background: #ffffff !important;
      font-family: Arial, "Microsoft YaHei", "Noto Sans CJK SC", sans-serif !important;
      line-height: 1.6 !important;
    }
    .export-render .ant-card,
    .export-render .ant-collapse,
    .export-render .ant-collapse-item,
    .export-render .ant-collapse-content,
    .export-render .ant-card-body,
    .export-render .poster-section,
    .export-render .route-segment,
    .export-render .hotel-card,
    .export-render .weather-card,
    .export-render .web-guide-content,
    .export-render .reference-list,
    .export-render .audit-grid {
      color: #101828 !important;
      background: #ffffff !important;
      box-shadow: none !important;
    }
    .export-render .ant-card,
    .export-render .ant-collapse,
    .export-render .poster-section,
    .export-render .export-summary {
      border: 1px solid #cbd5d1 !important;
    }
    .export-render .ant-card-head {
      color: #101828 !important;
      background: #ffffff !important;
      border-bottom: 2px solid #0f766e !important;
    }
    .export-render .ant-card-head-title,
    .export-render .ant-collapse-header,
    .export-render strong,
    .export-render h1,
    .export-render h2,
    .export-render h3,
    .export-render h4 {
      color: #101828 !important;
      opacity: 1 !important;
    }
    .export-render p,
    .export-render li,
    .export-render td,
    .export-render th,
    .export-render .info-value,
    .export-render .value,
    .export-render .ant-descriptions-item-content,
    .export-render .ant-descriptions-item-label {
      color: #1f2937 !important;
      opacity: 1 !important;
    }
    .export-render .attraction-no-photo,
    .export-render .budget-item,
    .export-render .day-info,
    .export-render .export-summary__metrics > div {
      background: #f8fafc !important;
    }
    .export-render .attraction-card {
      page-break-inside: avoid;
      break-inside: avoid;
    }
    .export-render .attraction-image-wrapper {
      min-height: 260px !important;
      border: 1px solid #dbe4e1 !important;
      border-radius: 12px !important;
      overflow: hidden !important;
      background: #eef6f3 !important;
    }
    .export-render .attraction-image {
      width: 100% !important;
      height: 260px !important;
      object-fit: cover !important;
      object-position: center center !important;
      display: block !important;
      background: #eef6f3 !important;
    }
    .export-render .attraction-no-photo {
      min-height: 260px !important;
      padding: 18px !important;
      display: flex !important;
      flex-direction: column !important;
      justify-content: flex-end !important;
    }
    .export-render .export-summary {
      margin-bottom: 20px;
      padding: 22px 24px;
      border-radius: 8px;
    }
    .export-render .export-summary__kicker {
      color: #0f766e !important;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      margin-bottom: 10px;
    }
    .export-render .export-summary__headline {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
    }
    .export-render .export-summary__headline h1 {
      margin: 0 0 6px;
      font-size: 30px;
      line-height: 1.15;
    }
    .export-render .export-summary__headline p {
      margin: 0;
      font-size: 14px;
      color: #475467 !important;
    }
    .export-render .export-summary__metrics {
      display: grid;
      grid-template-columns: repeat(3, minmax(120px, 1fr));
      gap: 10px;
    }
    .export-render .export-summary__metrics > div {
      min-height: 68px;
      padding: 10px 12px;
      border: 1px solid #dbe4e1;
      border-radius: 8px;
    }
    .export-render .export-summary__metrics span {
      display: block;
      margin-bottom: 6px;
      color: #667085 !important;
      font-size: 12px;
    }
    .export-render .export-summary__metrics strong {
      display: block;
      font-size: 20px;
      line-height: 1.2;
    }
  `
  container.prepend(printStyle)
  container.prepend(buildExportSummary())
  container.querySelectorAll('.interactive-map-card, .export-hidden').forEach(node => node.remove())
  const topInfo = container.querySelector('.top-info-section') as HTMLElement | null
  if (topInfo) {
    topInfo.style.display = 'block'
  }
  container.querySelectorAll('.left-info').forEach(node => {
    (node as HTMLElement).style.width = '100%'
  })
  container.querySelectorAll('.ant-collapse-content').forEach(node => {
    const content = node as HTMLElement
    content.style.display = 'block'
    content.style.height = 'auto'
  })

  document.body.appendChild(container)
  if (document.fonts?.ready) await document.fonts.ready
  await waitForExportImages(container)
  await new Promise(resolve => window.setTimeout(resolve, 220))
  return container
}

const renderExportCanvas = async (container: HTMLElement, preferredScale: number) => {
  const { default: html2canvas } = await import('html2canvas')
  const safeHeight = Math.max(1, container.scrollHeight)
  const safeWidth = Math.max(1, container.scrollWidth)
  const maxDimensionScale = Math.min(30000 / safeHeight, 16000 / safeWidth)
  const maxAreaScale = Math.sqrt(268000000 / (safeWidth * safeHeight))
  const scale = Math.max(1, Math.min(preferredScale, maxDimensionScale, maxAreaScale))
  return html2canvas(container, {
    backgroundColor: '#ffffff',
    scale,
    logging: false,
    useCORS: true,
    allowTaint: false,
    imageTimeout: 10000,
    windowWidth: safeWidth,
    windowHeight: safeHeight,
    scrollX: 0,
    scrollY: 0
  })
}
const assertExportContext = () => {
  if (!isViewOwnerActive() || !tripPlan.value) {
    throw new Error('\u8d26\u53f7\u5df2\u5207\u6362\uff0c\u5df2\u53d6\u6d88\u5bfc\u51fa\u4e0a\u4e00\u8d26\u53f7\u7684\u884c\u7a0b\u3002')
  }
}

const downloadCanvas = async (canvas: HTMLCanvasElement, filename: string) => {
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      value => value ? resolve(value) : reject(new Error('图片压缩失败')),
      'image/jpeg',
      0.92
    )
  })
  assertExportContext()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.download = filename
  link.href = url
  link.click()
  URL.revokeObjectURL(url)
}

const exportAsImage = async () => {
  if (!isViewOwnerActive() || !tripPlan.value) return
  let container: HTMLElement | null = null
  try {
    message.loading({ content: '正在生成高清图片...', key: 'export', duration: 0 })
    await expandAllDaysForExport()
    assertExportContext()
    container = await createExportContainer()
    assertExportContext()
    const canvas = await renderExportCanvas(container, 2.3)
    assertExportContext()
    await downloadCanvas(
      canvas,
      `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.jpg`
    )
    message.success({ content: '图片导出成功', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  } finally {
    container?.remove()
  }
}

type PdfUnit = { top: number; bottom: number }

const calculatePdfSlices = (
  container: HTMLElement,
  canvas: HTMLCanvasElement,
  pageHeight: number
) => {
  const rect = container.getBoundingClientRect()
  const scale = canvas.width / rect.width
  const units: PdfUnit[] = Array.from(container.querySelectorAll(
    '.pdf-break-unit, .ant-collapse-item, .attraction-card, .route-segment, .hotel-card, .weather-card, .web-guide-card, .web-meta-card, .agent-audit-card, .reference-list, .web-guide-content, .audit-grid'
  )).map(node => {
    const itemRect = (node as HTMLElement).getBoundingClientRect()
    return {
      top: Math.max(0, (itemRect.top - rect.top) * scale),
      bottom: Math.min(canvas.height, (itemRect.bottom - rect.top) * scale)
    }
  })

  const slices: Array<{ start: number; end: number }> = []
  let start = 0
  while (start < canvas.height - 2) {
    const ideal = Math.min(canvas.height, start + pageHeight)
    if (ideal >= canvas.height) {
      slices.push({ start, end: canvas.height })
      break
    }

    const crossing = units
      .filter(unit =>
        unit.top > start + pageHeight * 0.22
        && unit.top < ideal
        && unit.bottom > ideal
      )
      .sort((a, b) => a.top - b.top)[0]

    let end = crossing ? crossing.top - 8 * scale : ideal
    if (end - start < pageHeight * 0.45) end = ideal
    slices.push({ start, end: Math.round(end) })
    start = Math.round(end)
  }
  return slices
}

const exportAsPDF = async () => {
  if (!isViewOwnerActive() || !tripPlan.value) return
  let container: HTMLElement | null = null
  try {
    message.loading({ content: '正在生成高清 PDF...', key: 'export', duration: 0 })
    await expandAllDaysForExport()
    assertExportContext()
    container = await createExportContainer()
    assertExportContext()
    const canvas = await renderExportCanvas(container, 2.15)
    assertExportContext()
    const { default: jsPDF } = await import('jspdf')
    assertExportContext()
    const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4', compress: true })
    const pageWidth = 194
    const pageHeightMm = 277
    const pageHeightPx = canvas.width * pageHeightMm / pageWidth
    const slices = calculatePdfSlices(container, canvas, pageHeightPx)

    slices.forEach((slice, index) => {
      if (index > 0) pdf.addPage()
      const height = Math.max(1, slice.end - slice.start)
      const pageCanvas = document.createElement('canvas')
      pageCanvas.width = canvas.width
      pageCanvas.height = height
      const context = pageCanvas.getContext('2d')
      if (!context) throw new Error('PDF 分页画布创建失败')
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, pageCanvas.width, pageCanvas.height)
      context.drawImage(
        canvas,
        0, slice.start, canvas.width, height,
        0, 0, pageCanvas.width, height
      )
      const imageData = pageCanvas.toDataURL('image/jpeg', 0.86)
      const renderedHeight = Math.min(pageHeightMm, height * pageWidth / canvas.width)
      pdf.addImage(imageData, 'JPEG', 8, 10, pageWidth, renderedHeight, undefined, 'FAST')
    })

    assertExportContext()
    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)
    message.success({ content: `PDF 导出成功，共 ${slices.length} 页`, key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  } finally {
    container?.remove()
  }
}
// 初始化地图
const initMap = async () => {
  if (!isViewOwnerActive() || !tripPlan.value) return
  try {
    const loadConfig: {
      key: string
      version: string
      plugins: string[]
      securityJsCode?: string
    } = {
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    }
    const securityJsCode = import.meta.env.VITE_AMAP_SECURITY_JS_CODE
    if (securityJsCode) {
      loadConfig.securityJsCode = securityJsCode
    }

    const { default: AMapLoader } = await import('@amap/amap-jsapi-loader')
    const AMap = await AMapLoader.load(loadConfig)
    if (!isViewOwnerActive() || !tripPlan.value || !document.getElementById('amap-container')) return

    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527],
      viewMode: '3D'
    })

    await nextTick()
    if (!isViewOwnerActive() || !tripPlan.value) {
      map.destroy()
      map = null
      return
    }
    window.clearTimeout(mapResizeTimer)
    mapResizeTimer = window.setTimeout(() => {
      if (!isViewOwnerActive() || !map) return
      try {
        map.resize?.()
        window.requestAnimationFrame(() => {
          if (isViewOwnerActive() && map) addAttractionMarkers(AMap)
        })
      } catch (markerError) {
        console.warn('Map markers skipped:', markerError)
      }
    }, 300)

    if (!isViewOwnerActive()) return
    message.success('地图加载成功')
  } catch (error) {
    if (!isViewOwnerActive()) return
    console.error('地图加载失败:', error)
    message.error('地图加载失败')
  }
}

// 添加景点标记
const normalizeMapCoordinate = (location?: { longitude?: unknown; latitude?: unknown } | null) => {
  const longitude = Number(location?.longitude)
  const latitude = Number(location?.latitude)

  if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
    return null
  }

  if (Math.abs(longitude) > 180 || Math.abs(latitude) > 90) {
    return null
  }

  return { longitude, latitude }
}

const addAttractionMarkers = (AMap: any) => {
  if (!tripPlan.value) return

  const markers: any[] = []
  const allAttractions: any[] = []

  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      const coordinate = normalizeMapCoordinate(attraction.location)
      if (coordinate) {
        allAttractions.push({
          ...attraction,
          location: coordinate,
          dayIndex,
          attrIndex
        })
      }
    })
  })

  const isCompactMap = window.matchMedia('(max-width: 768px)').matches
  if (isCompactMap) {
    const firstLocation = allAttractions[0]?.location
    if (firstLocation) {
      try {
        map.setZoomAndCenter(12, [firstLocation.longitude, firstLocation.latitude])
      } catch (error) {
        console.warn('Map mobile center skipped:', error)
      }
    }
    return
  }

  allAttractions.forEach((attraction, index) => {
    const marker = new AMap.Marker({
      position: [attraction.location.longitude, attraction.location.latitude],
      title: attraction.name,
      label: {
        content: `<div style="background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${index + 1}</div>`,
        offset: new AMap.Pixel(0, -30)
      }
    })

    const infoWindow = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">${escapeHtml(String(attraction.name || ''))}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${escapeHtml(String(attraction.address || ''))}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${escapeHtml(String(attraction.visit_duration || 0))}分钟</p>
          <p style="margin: 4px 0;"><strong>描述:</strong> ${escapeHtml(String(attraction.description || ''))}</p>
          <p style="margin: 4px 0; color: #1890ff;"><strong>第${attraction.dayIndex + 1}天 景点${attraction.attrIndex + 1}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition())
    })

    markers.push(marker)
  })

  const hotel = tripPlan.value.days
    .map(day => day.hotel)
    .find(item => normalizeMapCoordinate(item?.location))
  const hotelCoordinate = normalizeMapCoordinate(hotel?.location)
  if (hotel && hotelCoordinate) {
    const hotelMarker = new AMap.Marker({
      position: [hotelCoordinate.longitude, hotelCoordinate.latitude],
      title: hotel.name,
      label: {
        content: '<div style="background:#2563eb;color:#fff;padding:5px 8px;border-radius:4px;font-size:12px;font-weight:700">酒店 H</div>',
        offset: new AMap.Pixel(0, -30)
      }
    })
    const hotelInfo = new AMap.InfoWindow({
      content: `<div style="padding:10px"><h4 style="margin:0 0 8px">${escapeHtml(String(hotel.name || ''))}</h4><p>${escapeHtml(String(hotel.address || ''))}</p><p>${escapeHtml(String(hotel.distance || ''))}</p></div>`,
      offset: new AMap.Pixel(0, -30)
    })
    hotelMarker.on('click', () => hotelInfo.open(map, hotelMarker.getPosition()))
    markers.push(hotelMarker)
  }

  if (markers.length > 0) {
    map.add(markers)

    window.setTimeout(() => {
      try {
        map.resize?.()
        map.setFitView(markers)
      } catch (error) {
        console.warn('Map fit view skipped:', error)
      }
    }, 250)
  }

  drawRoutes(AMap)
}

// 绘制路线：优先使用后端返回的高德真实折线，旧数据使用虚线示意。
const drawRoutes = (AMap: any) => {
  if (!tripPlan.value) return
  const colors = ['#0f766e', '#dc2626', '#2563eb', '#d97706', '#15803d', '#7c3aed']

  tripPlan.value.days.forEach((day, dayIndex) => {
    const color = colors[dayIndex % colors.length]
    let hasVerifiedRoute = false

    day.routes?.forEach(route => {
      const path = (route.path || [])
        .map(point => normalizeMapCoordinate(point))
        .filter(Boolean)
        .map(point => [point!.longitude, point!.latitude])
      if (path.length < 2) return
      hasVerifiedRoute = hasVerifiedRoute || Boolean(route.verified)
      map.add(new AMap.Polyline({
        path,
        strokeColor: color,
        strokeWeight: 5,
        strokeOpacity: 0.84,
        strokeStyle: route.verified ? 'solid' : 'dashed',
        showDir: true
      }))
    })

    if (hasVerifiedRoute) return
    const fallbackPath = day.attractions
      .map(attraction => normalizeMapCoordinate(attraction.location))
      .filter(Boolean)
      .map(point => [point!.longitude, point!.latitude])
    if (fallbackPath.length >= 2) {
      map.add(new AMap.Polyline({
        path: fallbackPath,
        strokeColor: color,
        strokeWeight: 3,
        strokeOpacity: 0.65,
        strokeStyle: 'dashed',
        showDir: true
      }))
    }
  })
}
/** Quality-status display helpers added by semantic-contract merge. */
const readinessTone = computed(() => {
  const q = tripPlan.value?.quality
  if (!q) return 'neutral'
  if (q.status === 'failed' || !q.publishable) return 'fail'
  if (q.review_required || q.status === 'warning') return 'warn'
  return 'ok'
})
const generationModeLabel = computed(() => {
  const mode = tripPlan.value?.generation_mode
  return mode === 'repaired' ? '已修复' : mode === 'map_fallback' ? '地图备选' : '主规划'
})
const readinessTitle = computed(() => {
  const q = tripPlan.value?.quality
  if (!q) return '待评估'
  if (q.status === 'failed') return '质量未通过'
  if (q.review_required) return '方案可用，建议复核'
  return '方案已就绪'
})
const readinessBadge = computed(() => {
  const q = tripPlan.value?.quality
  if (!q) return '未评估'
  if (q.status === 'failed') return '需重新生成'
  if (q.review_required) return '建议复核'
  return '可出发'
})
const verifiedFactsDisplay = computed(() =>
  tripPlan.value?.quality?.verified_facts || '—'
)
const verifiedRouteCount = computed(() => {
  let count = 0
  for (const day of tripPlan.value?.days || []) {
    for (const r of day.routes || []) {
      if (r.verified || r.source) count++
    }
  }
  return count
})
const qualityIssues = computed(() =>
  tripPlan.value?.quality?.issues || []
)
const topQualityIssues = computed(() =>
  qualityIssuesList.value
    .filter(issue => issue.severity === 'warning' || issue.severity === 'error')
    .slice(0, 3)
)
const isRouteServerVerified = (route: any) =>
  !!route?.source && route.source !== 'client-estimate'


</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background:
    linear-gradient(120deg, rgba(15, 118, 110, 0.07), rgba(37, 99, 235, 0.06)),
    #f7faf9;
  padding: 28px 24px 56px;
}

.result-loading {
  min-height: 50vh;
  display: grid;
  place-items: center;
}

.draft-retention {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px 5px 10px;
  color: #475467;
  background: #fff;
  border: 1px solid #d8e2df;
  border-radius: 6px;
  font-size: 12px;
}

.draft-retention .ant-select {
  width: 82px;
}

.page-header {
  max-width: 1400px;
  margin: 0 auto 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

.page-header :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 8px;
}

.result-hero {
  max-width: 1400px;
  margin: 0 auto 22px;
  padding: 24px;
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.07);
}

.hero-kicker {
  color: #0f766e;
  font-size: 14px;
  font-weight: 700;
}

.result-hero h1 {
  margin: 8px 0 0;
  color: #172033;
  font-size: 34px;
  line-height: 1.15;
  font-weight: 800;
  letter-spacing: 0;
}

.hero-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(104px, 1fr));
  gap: 10px;
}

.metric-item {
  min-height: 70px;
  padding: 12px 14px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #f8fbfb;
}

.metric-item span {
  display: block;
  margin-bottom: 6px;
  color: #667085;
  font-size: 13px;
}

.metric-item strong {
  display: block;
  color: #172033;
  font-size: 22px;
  line-height: 1.2;
}

/* 行程状态摘要 */
.trip-readiness {
  --readiness-accent: #0f766e;
  --readiness-soft: #edf8f6;
  max-width: 1400px;
  margin: 0 auto 22px;
  padding: 20px 22px;
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(220px, 0.55fr) minmax(300px, 0.8fr);
  align-items: stretch;
  gap: 18px;
  border: 1px solid #dce8e4;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.055);
}

.trip-readiness.is-warn {
  --readiness-accent: #b45309;
  --readiness-soft: #fff8eb;
}

.trip-readiness.is-fail {
  --readiness-accent: #b42318;
  --readiness-soft: #fff3f1;
}

.trip-readiness.is-neutral {
  --readiness-accent: #475467;
  --readiness-soft: #f4f6f7;
}

.readiness-copy {
  min-width: 0;
  padding: 2px 6px 2px 0;
}

.readiness-kicker {
  display: block;
  margin-bottom: 8px;
  color: var(--readiness-accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.readiness-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.readiness-title-row h2 {
  margin: 0;
  color: #172033;
  font-size: 22px;
  line-height: 1.3;
  font-weight: 750;
}

.readiness-badge {
  padding: 3px 9px;
  border: 1px solid color-mix(in srgb, var(--readiness-accent) 25%, white);
  border-radius: 999px;
  color: var(--readiness-accent);
  background: var(--readiness-soft);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
}

.readiness-copy p {
  margin: 9px 0 0;
  color: #475467;
  font-size: 13px;
  line-height: 1.75;
}

.readiness-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid #e3ebe8;
  border-radius: 10px;
  background: #f8fbfa;
  overflow: hidden;
}

.readiness-metrics > div {
  min-width: 0;
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.readiness-metrics > div + div {
  border-left: 1px solid #e3ebe8;
}

.readiness-metrics span {
  margin-bottom: 5px;
  color: #667085;
  font-size: 11px;
  white-space: nowrap;
}

.readiness-metrics strong {
  color: #172033;
  font-size: 20px;
  line-height: 1.2;
}

.readiness-focus,
.readiness-clear,
.readiness-unchecked {
  min-width: 0;
  padding: 13px 15px;
  border: 1px solid color-mix(in srgb, var(--readiness-accent) 16%, white);
  border-radius: 10px;
  background: var(--readiness-soft);
}

.focus-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: #344054;
  font-size: 12px;
  font-weight: 700;
}

.focus-head button {
  padding: 0;
  border: 0;
  color: var(--readiness-accent);
  background: transparent;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.focus-head button:hover {
  text-decoration: underline;
}

.readiness-focus ul {
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
  list-style: none;
}

.readiness-focus li {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: #475467;
  font-size: 12px;
  line-height: 1.5;
}

.readiness-focus li i {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: #d97706;
}

.readiness-focus li i.is-error {
  background: #d92d20;
}

.readiness-clear,
.readiness-unchecked {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #475467;
  font-size: 12px;
  line-height: 1.6;
}

.readiness-clear > span,
.readiness-unchecked > span {
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: var(--readiness-accent);
  font-size: 12px;
  font-weight: 700;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 8px;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.07);
  background: white;
  border: 1px solid #e4ebe8;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: #0f766e;
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(15, 118, 110, 0.09);
}

.main-content {
  flex: 1;
  min-width: 0;
}

.export-render {
  color: #101828;
  background: #ffffff;
}

.export-render :deep(.ant-card),
.export-render :deep(.ant-collapse-item),
.export-render .poster-section {
  break-inside: avoid;
}

.export-render :deep(.ant-card) {
  box-shadow: none !important;
}

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  min-height: 132px;
  border: 1px solid #dfe8e5;
  border-radius: 6px;
  overflow: hidden;
  background: #f4f7f6;
}

.attraction-image {
  width: 100%;
  height: 168px;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.attraction-no-photo {
  min-height: 132px;
  padding: 42px 18px 16px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  background: #f5f7f6;
}

.attraction-no-photo span,
.attraction-no-photo small {
  color: #667085;
  font-size: 12px;
}

.attraction-no-photo strong {
  margin: 6px 0 3px;
  color: #172033;
  font-size: 18px;
}

.attraction-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: #0f766e;
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.badge-number {
  font-size: 18px;
}

.price-tag {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 8px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

.weather-empty {
  padding: 18px 20px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #f8fbfb;
  color: #475467;
  font-size: 14px;
  line-height: 1.7;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: #0f766e;
  color: white;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: #eff6ff;
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: #2563eb;
}

.hotel-title {
  color: white !important;
  font-weight: 600;

.meal-detail {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.meal-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.meal-heading strong {
  color: #172033;
  font-size: 14px;
}

.meal-cost {
  flex: 0 0 auto;
  color: #0f766e;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.meal-address,
.meal-description {
  margin: 0;
  color: #475467;
  font-size: 13px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.meal-description {
  margin-top: 2px;
}
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #0f766e;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: #0f766e;
  border-radius: 8px;
  color: white;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

.budget-meta {
  margin-top: 14px;
  padding: 14px 16px;
  border-radius: 8px;
  background: #f8fbfb;
  border: 1px solid #dce8e4;
}

.budget-source,
.budget-reference {
  margin: 0 0 8px;
  color: #475467;
  font-size: 13px;
  line-height: 1.6;
}

.budget-notes {
  margin: 0;
  padding-left: 18px;
  color: #667085;
  font-size: 13px;
  line-height: 1.7;
}

.web-guide-card,
.web-meta-card,
.agent-audit-card {
  margin-bottom: 20px;
}

.web-guide-content {
  padding: 18px 20px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #fbfdfc;
  color: #243042;
  font-size: 14px;
  line-height: 1.8;
  word-break: break-word;
}

.web-guide-content :deep(h2),
.web-guide-content :deep(h3),
.web-guide-content :deep(h4) {
  margin: 20px 0 10px;
  color: #172033;
  font-weight: 800;
  line-height: 1.35;
}

.web-guide-content :deep(h2:first-child),
.web-guide-content :deep(h3:first-child),
.web-guide-content :deep(h4:first-child) {
  margin-top: 0;
}

.web-guide-content :deep(h2) {
  font-size: 20px;
}

.web-guide-content :deep(h3) {
  padding-left: 10px;
  border-left: 3px solid #0f766e;
  font-size: 16px;
}

.web-guide-content :deep(h4) {
  font-size: 15px;
}

.web-guide-content :deep(p) {
  margin: 0 0 12px;
}

.web-guide-content :deep(ol),
.web-guide-content :deep(ul) {
  margin: 0 0 14px;
  padding-left: 22px;
}

.web-guide-content :deep(li) {
  margin: 4px 0;
}

.web-guide-content :deep(strong) {
  color: #172033;
  font-weight: 700;
}

.web-guide-content :deep(a) {
  color: #0f766e;
}

.web-guide-content :deep(code) {
  padding: 2px 5px;
  border-radius: 4px;
  background: #eef6f4;
  color: #0f766e;
}

.reference-list {
  padding: 14px 16px;
  border-radius: 8px;
  background: #f8fbfb;
  border: 1px solid #dce8e4;
}

.reference-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  color: #0f766e;
  border-top: 1px solid #e4ebe8;
}

.reference-item:first-of-type {
  border-top: 0;
}

.reference-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-site {
  flex-shrink: 0;
  color: #667085;
  font-size: 12px;
}

.empty-reference {
  color: #667085;
  font-size: 13px;
  line-height: 1.6;
}

.audit-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}

.audit-heading {
  margin-bottom: 8px;
  color: #172033;
  font-size: 14px;
  font-weight: 700;
}

.audit-list {
  margin: 0;
  padding-left: 18px;
  color: #475467;
  font-size: 13px;
  line-height: 1.8;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

.mobile-map-summary {
  min-height: 100%;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: #fbfdfc;
}

.mobile-map-item {
  display: flex;
  gap: 10px;
  padding: 10px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
}

.mobile-map-item > span {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: #0f766e;
  color: #ffffff;
  font-size: 13px;
  font-weight: 800;
}

.mobile-map-item strong {
  display: block;
  color: #172033;
  font-size: 14px;
  line-height: 1.35;
}

.mobile-map-item p {
  margin: 4px 0 0;
  color: #667085;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

.route-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 18px;
}

.route-segment {
  padding: 14px 16px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #fbfdfc;
}

.route-head,
.route-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.route-title {
  color: #172033;
  font-size: 14px;
  font-weight: 700;
}

.route-mode {
  flex-shrink: 0;
  padding: 3px 8px;
  border-radius: 6px;
  background: #edf7f5;
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
}

.route-meta {
  justify-content: flex-start;
  margin-top: 8px;
  color: #667085;
  font-size: 13px;
}

.route-desc {
  margin: 8px 0 0;
  color: #475467;
  font-size: 13px;
  line-height: 1.7;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: #0f766e;
  color: white !important;
  border-radius: 8px 8px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #0f766e;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: translateY(-2px);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 1100px) {
  .trip-readiness {
    grid-template-columns: minmax(0, 1fr) minmax(220px, 0.45fr);
  }

  .readiness-focus,
  .readiness-clear,
  .readiness-unchecked {
    grid-column: 1 / -1;
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 16px 10px 40px;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    margin-bottom: 14px;
  }

  .back-button {
    width: 100%;
    justify-content: center;
  }

  .page-header :deep(.ant-space) {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
    gap: 8px !important;
  }

  .page-header :deep(.ant-space-item),
  .page-header :deep(.ant-btn) {
    width: 100%;
  }

  .page-header :deep(.ant-dropdown-trigger) {
    justify-content: center;
  }

  .result-hero {
    padding: 16px;
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
    margin-bottom: 16px;
  }

  .hero-kicker {
    font-size: 13px;
    overflow-wrap: anywhere;
  }

  .result-hero h1 {
    font-size: 24px;
    overflow-wrap: anywhere;
  }

  .hero-metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .metric-item {
    min-height: 62px;
    padding: 9px 8px;
  }

  .metric-item span {
    font-size: 12px;
  }

  .metric-item strong {
    font-size: 17px;
    overflow-wrap: anywhere;
  }

  .trip-readiness {
    margin-bottom: 16px;
    padding: 16px;
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .readiness-title-row h2 {
    font-size: 20px;
  }

  .readiness-copy {
    padding: 0;
  }

  .readiness-focus,
  .readiness-clear,
  .readiness-unchecked {
    grid-column: auto;
  }

  .content-wrapper {
    display: block;
  }

  .side-nav {
    display: none;
  }

  .top-info-section {
    flex-direction: column;
    gap: 14px;
    margin-bottom: 14px;
  }

  .left-info {
    flex: auto;
  }

  .right-map {
    min-width: 0;
  }

  .map-card {
    min-height: 0;
  }

  .main-content :deep(.ant-card) {
    margin-bottom: 14px;
  }

  .main-content :deep(.ant-card-body) {
    padding: 16px;
  }

  .map-card :deep(.ant-card-body) {
    padding: 0;
  }

  :deep(.ant-card-head-title) {
    font-size: 16px;
    white-space: normal;
  }

  :deep(.ant-collapse-header) {
    padding: 12px 14px !important;
  }

  :deep(.ant-collapse-content-box) {
    padding: 14px;
  }

  .day-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .day-title {
    font-size: 16px;
  }

  .day-info {
    padding: 14px;
  }

  .info-row {
    flex-direction: column;
    gap: 4px;
  }

  .info-row .label {
    min-width: 0;
  }

  .budget-grid {
    grid-template-columns: 1fr;
  }

  .budget-total {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .total-value {
    font-size: 23px;
  }

  .attraction-image {
    height: 170px;
  }

  .route-head,
  .route-meta {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }

  .route-mode {
    align-self: flex-start;
  }

  .hotel-card :deep(.ant-descriptions-view) {
    overflow-x: auto;
  }

  :deep(.ant-descriptions-item-label),
  :deep(.ant-descriptions-item-content) {
    padding: 8px !important;
  }

  .web-guide-content {
    padding: 14px;
    font-size: 13px;
    line-height: 1.7;
  }

  .reference-list {
    padding: 10px 12px;
  }

  .audit-grid {
    grid-template-columns: 1fr;
  }

  .reference-item {
    align-items: flex-start;
    flex-direction: column;
  }

  .reference-name {
    white-space: normal;
  }
}

@media (max-width: 430px) {
  .page-header :deep(.ant-space) {
    grid-template-columns: 1fr;
  }

  .metric-item strong {
    font-size: 16px;
  }

  .map-card {
    min-height: 300px;
  }

  .weather-info-row {
    gap: 10px;
  }
}
.quality-advisory-card {
  margin-bottom: 20px;
  border: 1px solid #dce8e4;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.055);
  overflow: hidden;
}

.quality-advisory-card :deep(.ant-card-body) {
  padding: 0;
}

.quality-advisory-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  min-height: 58px;
  padding: 13px 18px;
  border-bottom: 1px solid #e6ecea;
  background: linear-gradient(90deg, #f3faf8 0%, #ffffff 58%);
}

.quality-advisory-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #172033;
}

.advisory-icon {
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  border-radius: 8px;
  color: #0f766e;
  background: #e4f4f1;
  font-size: 0;
}

.advisory-icon::before {
  content: "i";
  font-size: 14px;
  font-weight: 800;
  font-family: Georgia, serif;
}

.advisory-heading-copy {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.advisory-text-title {
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}

.advisory-count-badge {
  font-size: 12px;
  color: #667085;
  font-weight: 400;
  white-space: nowrap;
}

.quality-advisory-header :deep(.ant-btn-link) {
  height: 30px;
  padding-inline: 8px;
  color: #0f766e;
  font-weight: 600;
}

.quality-advisory-body {
  padding: 14px 18px 18px;
  display: grid;
  gap: 9px;
  background: #fbfcfc;
}

.quality-issue-item {
  position: relative;
  padding: 12px 14px 12px 16px;
  background: #ffffff;
  border: 1px solid #e5eae8;
  border-radius: 9px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.025);
}

.quality-issue-item::before {
  content: "";
  position: absolute;
  top: 12px;
  bottom: 12px;
  left: 0;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: #d97706;
}

.quality-issue-item.info {
  background: #fbfdff;
}

.quality-issue-item.info::before {
  background: #2e7da8;
}

.issue-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.issue-category {
  flex: 0 0 auto;
  padding: 3px 8px;
  border-radius: 999px;
  color: #9a4f08;
  background: #fff5e8;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.45;
}

.quality-issue-item.info .issue-category {
  color: #245d7a;
  background: #eaf5fb;
}

.issue-message {
  color: #344054;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.55;
}

.issue-suggestion {
  margin-top: 6px;
  padding-left: 1px;
  color: #667085;
  font-size: 12px;
  line-height: 1.55;
}

.issue-suggestion > span {
  margin-right: 5px;
  color: #475467;
  font-weight: 600;
}

@media (max-width: 768px) {
  .quality-advisory-header {
    align-items: flex-start;
    padding: 13px 14px;
  }

  .quality-advisory-title {
    flex-wrap: wrap;
  }

  .advisory-count-badge {
    width: 100%;
    white-space: normal;
  }

  .quality-advisory-body {
    padding: 12px;
  }
}
</style>
