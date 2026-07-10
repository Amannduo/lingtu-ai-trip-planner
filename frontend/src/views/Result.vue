<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        <ArrowLeftOutlined />
        <span>返回首页</span>
      </a-button>
      <a-space size="middle">
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

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
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
            <a-menu-item key="agent-audit" v-if="tripPlan.agent_audit">
              <span>✅ 审核检查</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
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
            <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
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
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item.name, index)"
                          :alt="item.name"
                          class="attraction-image"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

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
                    <span class="route-mode">{{ getRouteTypeLabel(route.route_type) }}</span>
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
                  {{ meal.name }}
                  <span v-if="meal.description"> - {{ meal.description }}</span>
                </a-descriptions-item>
              </a-descriptions>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" title="天气信息" style="margin-top: 20px" :bordered="false">
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

        <a-card
          id="agent-audit"
          v-if="tripPlan.agent_audit"
          title="✅ 审核检查"
          :bordered="false"
          class="agent-audit-card"
        >
          <a-alert
            :type="auditAlertType"
            :message="auditStatusText"
            :description="`来源: ${tripPlan.agent_audit.source || 'unknown'}`"
            show-icon
          />
          <div class="audit-grid">
            <div>
              <div class="audit-heading">已检查</div>
              <ul class="audit-list">
                <li v-for="item in tripPlan.agent_audit.checked_items" :key="item">{{ item }}</li>
              </ul>
            </div>
            <div v-if="tripPlan.agent_audit.issues.length || tripPlan.agent_audit.suggestions.length">
              <div class="audit-heading">发现与建议</div>
              <ul class="audit-list">
                <li v-for="issue in tripPlan.agent_audit.issues" :key="issue">{{ issue }}</li>
                <li v-for="suggestion in tripPlan.agent_audit.suggestions" :key="suggestion">{{ suggestion }}</li>
              </ul>
            </div>
          </div>
        </a-card>
      </div>
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
import { computed, ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  CloseOutlined,
  DownOutlined,
  EditOutlined,
  ExportOutlined,
  SaveOutlined
} from '@ant-design/icons-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import apiClient, { PHOTO_REQUEST_TIMEOUT_MS } from '@/services/api'
import type { TripPlan } from '@/types'

const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const editMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
let map: any = null

const isMobileViewport = ref(false)

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
const displayWebGuide = computed(() => {
  const text = tripPlan.value?.web_guide?.trim() ?? ''
  return text
    .replace(/\n+#{0,6}\s*资料来源[:：]?[\s\S]*$/u, '')
    .replace(/\n+#{0,6}\s*审核检查[:：]?[\s\S]*$/u, '')
    .trim()
})
const renderedWebGuide = computed(() => renderMarkdown(displayWebGuide.value))
const totalBudgetText = computed(() => {
  const total = tripPlan.value?.budget?.total
  return typeof total === 'number' ? `¥${total}` : '待估算'
})
const weatherReviewText = computed(() => {
  if (!tripPlan.value) return '暂未获取到可展示的天气信息。'
  return `${tripPlan.value.city}${tripPlan.value.start_date} 至 ${tripPlan.value.end_date} 的逐日天气暂未获取到可靠预报。当前天气源可能只覆盖近期预报，请在出发前3-7天复核每日天气、温差和降雨。`
})
const auditAlertType = computed(() => {
  const status = tripPlan.value?.agent_audit?.status
  if (status === 'passed') return 'success'
  if (status === 'failed') return 'error'
  return 'warning'
})
const auditStatusText = computed(() => {
  const status = tripPlan.value?.agent_audit?.status
  if (status === 'passed') return '审核通过'
  if (status === 'failed') return '审核未通过'
  return '需要复核'
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

const normalizeTripPlan = (raw: any): TripPlan | null => {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const days = Array.isArray(raw.days)
    ? raw.days.map((day: any) => ({
        ...day,
        attractions: Array.isArray(day?.attractions) ? day.attractions : [],
        routes: Array.isArray(day?.routes) ? day.routes : [],
        meals: Array.isArray(day?.meals) ? day.meals : []
      }))
    : []

  const budget = raw.budget && typeof raw.budget === 'object'
    ? {
        ...raw.budget,
        budget_notes: Array.isArray(raw.budget.budget_notes) ? raw.budget.budget_notes : []
      }
    : undefined

  const agentAudit = raw.agent_audit && typeof raw.agent_audit === 'object'
    ? {
        ...raw.agent_audit,
        checked_items: Array.isArray(raw.agent_audit.checked_items) ? raw.agent_audit.checked_items : [],
        issues: Array.isArray(raw.agent_audit.issues) ? raw.agent_audit.issues : [],
        suggestions: Array.isArray(raw.agent_audit.suggestions) ? raw.agent_audit.suggestions : []
      }
    : undefined

  return {
    ...raw,
    days,
    weather_info: Array.isArray(raw.weather_info) ? raw.weather_info : [],
    budget,
    web_guide: typeof raw.web_guide === 'string' ? raw.web_guide : null,
    web_references: Array.isArray(raw.web_references) ? raw.web_references : [],
    agent_audit: agentAudit
  } as TripPlan
}

onMounted(async () => {
  isMobileViewport.value = window.matchMedia('(max-width: 768px)').matches
  const data = sessionStorage.getItem('tripPlan')
  if (!data) {
    console.warn('[result] no trip plan found in sessionStorage')
    return
  }

  try {
    const parsed = JSON.parse(data)
    const normalized = normalizeTripPlan(parsed)
    if (!normalized) {
      throw new Error('Invalid trip plan payload')
    }

    tripPlan.value = normalized
    console.info('[result] trip plan loaded', {
      city: normalized.city,
      days: normalized.days.length,
      hasBudget: Boolean(normalized.budget)
    })
    await nextTick()
    if (!isMobileViewport.value) {
      initMap()
    }
    loadAttractionPhotos()
  } catch (error) {
    console.error('[result] failed to load trip plan', error)
    sessionStorage.removeItem('tripPlan')
    message.error('结果页数据无效，请重新生成行程')
  }
})

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
const saveChanges = () => {
  editMode.value = false
  // 更新sessionStorage
  if (tripPlan.value) {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  message.success('修改已保存')

  // 重新初始化地图以反映更改
  if (map) {
    map.destroy()
  }
  nextTick(() => {
    if (!isMobileViewport.value) {
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
const getAttractionImage = (name: string, index: number): string => {
  // 如果已加载真实图片,返回真实图片
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }

  // 返回一个纯色占位图(避免跨域问题)
  const colors = [
    { start: '#0f766e', end: '#2563eb' },
    { start: '#f97316', end: '#f59e0b' },
    { start: '#0891b2', end: '#22c55e' },
    { start: '#7c3aed', end: '#2563eb' },
    { start: '#db2777', end: '#f97316' }
  ]
  const colorIndex = index % colors.length
  const { start, end } = colors[colorIndex]

  // 使用base64编码避免中文问题
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
        <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#grad${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">${name}</text>
  </svg>`

  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`
}

// 图片加载失败时的处理
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  // 使用灰色占位图
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}



// 导出为图片
const expandAllDaysForExport = async () => {
  if (!tripPlan.value) return

  activeDays.value = tripPlan.value.days.map((_, index) => index)
  await nextTick()
  await new Promise(resolve => window.setTimeout(resolve, 350))
}

const exportAsImage = async () => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })

    await expandAllDaysForExport()
    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = '' // 移除所有类
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#0f766e')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#2563eb')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#0f766e')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    // 转换为图片并下载
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })

    await expandAllDaysForExport()
    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#0f766e')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#2563eb')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#0f766e')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 如果内容高度超过一页,分页处理
    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}

// 初始化地图
const initMap = async () => {
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

    const AMap = await AMapLoader.load(loadConfig)

    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527],
      viewMode: '3D'
    })

    await nextTick()
    window.setTimeout(() => {
      try {
        map.resize?.()
        window.requestAnimationFrame(() => {
          addAttractionMarkers(AMap)
        })
      } catch (markerError) {
        console.warn('Map markers skipped:', markerError)
      }
    }, 300)

    message.success('地图加载成功')
  } catch (error) {
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
          <h4 style="margin: 0 0 8px 0;">${attraction.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${attraction.address}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${attraction.visit_duration}分钟</p>
          <p style="margin: 4px 0;"><strong>描述:</strong> ${attraction.description}</p>
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

  drawRoutes(AMap, allAttractions)
}

// 绘制路线
const drawRoutes = (AMap: any, attractions: any[]) => {
  if (attractions.length < 2) return

  const dayGroups: any = {}
  attractions.forEach(attr => {
    if (!dayGroups[attr.dayIndex]) {
      dayGroups[attr.dayIndex] = []
    }
    dayGroups[attr.dayIndex].push(attr)
  })

  Object.values(dayGroups).forEach((dayAttractions: any) => {
    if (dayAttractions.length < 2) return

    const path = dayAttractions
      .map((attr: any) => normalizeMapCoordinate(attr.location))
      .filter(Boolean)
      .map((coordinate: any) => [coordinate.longitude, coordinate.latitude])

    if (path.length < 2) return

    const polyline = new AMap.Polyline({
      path: path,
      strokeColor: '#1890ff',
      strokeWeight: 4,
      strokeOpacity: 0.8,
      strokeStyle: 'solid',
      showDir: true
    })

    map.add(polyline)
  })
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background:
    linear-gradient(120deg, rgba(15, 118, 110, 0.07), rgba(37, 99, 235, 0.06)),
    #f7faf9;
  padding: 28px 24px 56px;
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

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  border-radius: 8px;
  overflow: hidden;
}

.attraction-image {
  width: 100%;
  height: 200px;
  object-fit: cover;
  transition: transform 0.3s ease;
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
</style>
