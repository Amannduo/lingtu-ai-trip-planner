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
        <a-button
          v-if="!editMode"
          @click="toggleEditMode"
          type="default"
          :disabled="!canEditForHistory"
          :title="!canEditForHistory ? editDisabledReason : undefined"
        >
          <EditOutlined />
          <span>编辑行程</span>
        </a-button>
        <a-button
          v-else
          @click="saveChanges"
          type="primary"
          :disabled="!canEditForHistory"
          :title="!canEditForHistory ? editDisabledReason : undefined"
        >
          <SaveOutlined />
          <span>保存修改</span>
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          <CloseOutlined />
          <span>取消编辑</span>
        </a-button>

        <!-- 导出按钮：查看用途保留；blocked 时仅提示，不伪装为可出发交付 -->
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

    <!-- 质量与可信状态总览：直接使用后端 quality 结构化字段 -->
    <section
      v-if="tripPlan"
      id="trust-status"
      class="trust-status-banner"
      :class="'tone-' + trustTone"
      role="region"
      :aria-label="'方案质量：' + trustTitle"
    >
      <div class="trust-status-main">
        <div class="trust-status-heading">
          <span class="trust-status-kicker">方案质量</span>
          <h2 class="trust-status-title">{{ trustTitle }}</h2>
          <div class="trust-status-tags">
            <span class="trust-tag" :class="'tone-' + trustTone" :aria-label="trustTitle">
              {{ trustBadge }}
            </span>
            <span
              v-if="generationModeTag"
              class="trust-tag tone-neutral"
              :title="generationModeHintText"
            >
              {{ generationModeTag }}
            </span>
            <span v-if="verifiedFactsText" class="trust-tag tone-neutral">
              {{ verifiedFactsText }}
            </span>
          </div>
        </div>
        <p class="trust-status-desc">{{ trustDescription }}</p>
        <p v-if="generationModeHintText && generationModeTag" class="trust-status-mode-hint">
          {{ generationModeHintText }}
        </p>
        <p v-if="isPlanBlocked" class="trust-status-action-hint" role="status">
          保存到历史记录与邮件发送已禁用。可返回首页重新生成，或继续查看下方行程细节。
        </p>
        <p v-else-if="trustStatus === 'needs_review'" class="trust-status-action-hint" role="status">
          可保存与使用，但请先核对下方待确认事项；保存修改前会再次确认。
        </p>
      </div>
      <div class="trust-status-side" aria-hidden="false">
        <div class="trust-stat">
          <span>阻断项</span>
          <strong>{{ blockingIssues.length }}</strong>
        </div>
        <div class="trust-stat">
          <span>待核对</span>
          <strong>{{ advisoryIssues.length }}</strong>
        </div>
        <div class="trust-stat">
          <span>提示</span>
          <strong>{{ infoIssues.length }}</strong>
        </div>
      </div>
    </section>

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
            <a-menu-item key="budget" v-if="tripPlan.budget">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="weather">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
            <a-menu-item key="web-guide" v-if="tripPlan.web_guide">
              <span>🌐 联网攻略</span>
            </a-menu-item>
            <a-menu-item key="web-meta" v-if="safeWebReferences.length">
              <span>🔎 资料来源</span>
            </a-menu-item>
            <a-menu-item key="agent-audit" v-if="tripPlan.agent_audit">
              <span>✅ 来源审核</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 质量问题：blocking 与 advisory 分组，不按中文文案推断 -->
        <a-card
          v-if="qualityIssuesList.length > 0 || trustStatus !== 'unknown'"
          id="quality-gate"
          class="quality-advisory-card"
          :class="'quality-tone-' + trustTone"
          :bordered="false"
        >
          <div class="quality-advisory-header">
            <div class="quality-advisory-title">
              <span class="advisory-icon" aria-hidden="true">{{ isPlanBlocked ? '⛔' : trustStatus === 'needs_review' ? '⚠️' : '✅' }}</span>
              <span class="advisory-text-title">方案质量检查</span>
              <a-tag :color="qualityTagColor" class="advisory-status-tag">
                {{ trustTitle }}
              </a-tag>
              <span class="advisory-count-badge" v-if="qualityIssuesList.length">
                阻断 {{ blockingIssues.length }} · 待核对 {{ advisoryIssues.length }} · 提示 {{ infoIssues.length }}
              </span>
            </div>
            <a-button
              v-if="qualityIssuesList.length > 3"
              type="link"
              size="small"
              @click="toggleQualityExpand"
            >
              {{ isQualityExpanded ? '收起' : `展开全部 ${qualityIssuesList.length} 项` }}
            </a-button>
          </div>

          <div v-if="!qualityIssuesList.length" class="quality-empty-ok" :class="{ 'quality-empty-blocked': isPlanBlocked || trustStatus === 'unknown' }">
            {{ qualityEmptyStateText }}
          </div>

          <div v-else class="quality-advisory-body">
            <div v-if="visibleBlockingIssues.length" class="quality-group" role="group" aria-label="阻断性问题">
              <div class="quality-group-title">
                <span class="sr-only">严重程度：阻断</span>
                阻断性问题（须处理后方可保存/发送）
              </div>
              <div
                v-for="(item, idx) in visibleBlockingIssues"
                :key="'b-' + item.code + '-' + idx"
                class="quality-issue-item blocking"
              >
                <div class="issue-main">
                  <a-tag color="red" class="issue-badge">阻断</a-tag>
                  <span class="issue-message">{{ item.message }}</span>
                  <span v-if="item.code" class="issue-code">{{ item.code }}</span>
                </div>
                <div v-if="item.suggestion" class="issue-suggestion">建议：{{ item.suggestion }}</div>
              </div>
            </div>

            <div v-if="visibleAdvisoryIssues.length" class="quality-group" role="group" aria-label="待核对事项">
              <div class="quality-group-title">
                <span class="sr-only">严重程度：待核对</span>
                待核对事项（可使用，建议出发前确认）
              </div>
              <div
                v-for="(item, idx) in visibleAdvisoryIssues"
                :key="'a-' + item.code + '-' + idx"
                class="quality-issue-item warning"
              >
                <div class="issue-main">
                  <a-tag color="orange" class="issue-badge">待核对</a-tag>
                  <span class="issue-message">{{ item.message }}</span>
                  <span v-if="item.code" class="issue-code">{{ item.code }}</span>
                </div>
                <div v-if="item.suggestion" class="issue-suggestion">建议：{{ item.suggestion }}</div>
              </div>
            </div>

            <div v-if="visibleInfoIssues.length" class="quality-group" role="group" aria-label="提示信息">
              <div class="quality-group-title">
                <span class="sr-only">严重程度：提示</span>
                提示信息
              </div>
              <div
                v-for="(item, idx) in visibleInfoIssues"
                :key="'i-' + item.code + '-' + idx"
                class="quality-issue-item info"
              >
                <div class="issue-main">
                  <a-tag color="blue" class="issue-badge">提示</a-tag>
                  <span class="issue-message">{{ item.message }}</span>
                  <span v-if="item.code" class="issue-code">{{ item.code }}</span>
                </div>
                <div v-if="item.suggestion" class="issue-suggestion">建议：{{ item.suggestion }}</div>
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
                        <a-tag
                          :color="poiTrustTagColor(item.coordinate_source)"
                          class="poi-trust-tag"
                        >
                          {{ poiTrustLabel(item.coordinate_source) }}
                        </a-tag>
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
                      · {{ routeTrustText(route) }}
                    </span>
                  </div>
                  <div v-if="routeShowMetrics(route)" class="route-meta">
                    <span v-if="route.distance">{{ formatRouteDistance(route.distance) }}</span>
                    <span v-if="route.duration">{{ formatRouteDuration(route.duration) }}</span>
                  </div>
                  <div v-else class="route-meta route-meta-pending">距离与时间待地图确认</div>
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

        <!-- 预算：仅展示服务端 breakdown，不在前端二次乘人数 -->
        <a-card
          v-if="tripPlan.budget"
          id="budget"
          title="💰 预算明细"
          style="margin-top: 20px"
          :bordered="false"
          class="budget-card"
        >
          <div class="budget-source-line">
            <a-tag :color="budgetSourceMeta.isFallback ? 'orange' : budgetSourceMeta.isProvider ? 'green' : 'blue'">
              {{ budgetSourceMeta.isFallback ? '兜底估算' : budgetSourceMeta.isProvider ? '含服务端报价参考' : '服务端估算' }}
            </a-tag>
            <span>{{ budgetSourceMeta.label }}</span>
          </div>
          <div class="budget-grid">
            <div class="budget-item">
              <span class="budget-label">门票合计</span>
              <strong class="budget-value">{{ formatBudgetAmount(tripPlan.budget.total_attractions) }}</strong>
            </div>
            <div class="budget-item">
              <span class="budget-label">酒店合计</span>
              <strong class="budget-value">{{ formatBudgetAmount(tripPlan.budget.total_hotels) }}</strong>
            </div>
            <div class="budget-item">
              <span class="budget-label">餐饮合计</span>
              <strong class="budget-value">{{ formatBudgetAmount(tripPlan.budget.total_meals) }}</strong>
            </div>
            <div class="budget-item">
              <span class="budget-label">交通合计</span>
              <strong class="budget-value">{{ formatBudgetAmount(tripPlan.budget.total_transportation) }}</strong>
            </div>
            <div class="budget-item budget-total">
              <span class="budget-label">总预算</span>
              <strong class="budget-value">{{ formatBudgetAmount(tripPlan.budget.total) }}</strong>
            </div>
          </div>
          <div class="budget-meta">
            <div v-if="isFiniteMoney(tripPlan.budget.hotel_unit_price)">
              酒店单晚参考价 {{ formatBudgetAmount(tripPlan.budget.hotel_unit_price) }}
              <span class="budget-unit-hint">{{ hotelUnitHint }}</span>
            </div>
            <div v-if="isFiniteMoney(tripPlan.budget.transport_unit_price)">
              城际单人往返参考价 {{ formatBudgetAmount(tripPlan.budget.transport_unit_price) }}
              <span class="budget-unit-hint">{{ transportUnitHint }}</span>
            </div>
            <div v-if="isFiniteMoney(tripPlan.budget.intercity_transportation)">
              城际交通总额 {{ formatBudgetAmount(tripPlan.budget.intercity_transportation) }}
              （服务端已按人数聚合，前端不再乘人数）
            </div>
            <div v-if="isFiniteMoney(tripPlan.budget.local_transportation)">
              市内交通 {{ formatBudgetAmount(tripPlan.budget.local_transportation) }}
            </div>
            <div v-if="tripPlan.budget.hotel_reference" class="budget-reference">
              酒店参考：{{ tripPlan.budget.hotel_reference }}
            </div>
            <div v-if="tripPlan.budget.transport_reference" class="budget-reference">
              交通参考：{{ tripPlan.budget.transport_reference }}
            </div>
          </div>
          <ul v-if="tripPlan.budget.budget_notes?.length" class="budget-notes">
            <li v-for="(note, idx) in tripPlan.budget.budget_notes" :key="idx">{{ note }}</li>
          </ul>
          <p class="budget-disclaimer">
            金额为服务端规划结果中的估算或参考价，不是实时票价，也不表示已支付/已预订。
          </p>
        </a-card>

        <a-card id="weather" title="天气信息" style="margin-top: 20px" :bordered="false">
          <p class="weather-source-note">{{ weatherCoverageSummary }}</p>
          <p class="weather-source-note secondary">
            来源：服务端行程天气字段（非浏览器实时雷达；缺失日期显示「暂无预报」，不默认晴天）
          </p>
          <a-list
            v-if="displayWeatherDays.length > 0"
            :data-source="displayWeatherDays"
            :grid="{ gutter: 16, xs: 1, sm: 2, md: 3, lg: 3, xl: 3 }"
          >
            <template #renderItem="{ item }">
              <a-list-item>
                <a-card size="small" class="weather-card" :class="{ 'weather-missing': item.missing }">
                  <div class="weather-date">{{ item.date }}</div>
                  <template v-if="!item.missing">
                    <div class="weather-info-row">
                      <span class="weather-icon" aria-hidden="true">🌤️</span>
                      <div>
                        <div class="weather-label">白天</div>
                        <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                      </div>
                    </div>
                    <div class="weather-info-row">
                      <span class="weather-icon" aria-hidden="true">🌙</span>
                      <div>
                        <div class="weather-label">夜间</div>
                        <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                      </div>
                    </div>
                    <div class="weather-wind">
                      {{ item.wind_direction }} {{ item.wind_power }}
                    </div>
                  </template>
                  <div v-else class="weather-missing-body">
                    暂无预报
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
          <a-alert
            type="info"
            show-icon
            message="攻略为背景建议，不是景点地图验证结果"
            description="以下内容来自联网攻略补充，仅供参考；不得视为 POI 已验证、实时票价或官方认证。"
            style="margin-bottom: 12px"
          />
          <div class="web-guide-content" v-html="renderedWebGuide"></div>
        </a-card>

        <a-card
          id="web-meta"
          v-if="safeWebReferences.length"
          title="🔎 资料来源（攻略参考）"
          :bordered="false"
          class="web-meta-card"
        >
          <p class="reference-disclaimer">
            以下链接为攻略/资料来源，不表示对应景点已通过地图 POI 验证。
          </p>
          <div class="reference-list">
            <a
              v-for="reference in safeWebReferences"
              :key="reference.url || reference.title"
              :href="reference.url"
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
import { computed, ref, onMounted, nextTick, watch } from 'vue'
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
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import TripMapPoster from '@/components/TripMapPoster.vue'
import apiClient, {
  fetchMapContext,
  fetchTripPlan,
  PHOTO_REQUEST_TIMEOUT_MS,
  updateTripPlan
} from '@/services/api'
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
import {
  asStrictBool,
  budgetSourceTrust,
  canPersistPlan,
  deriveTrustStatus,
  escapeHtml,
  formatMoneyCNY,
  generationModeHint,
  generationModeLabel,
  groupQualityIssues,
  hotelUnitPriceHint,
  isFiniteMoney,
  isUsableWeatherDescription,
  normalizeDateKey,
  normalizeGenerationMode,
  normalizeQualityIssues,
  poiCoordinateTrustLabel,
  renderSafeGuideMarkdown,
  routeTrustLabel,
  safeHttpUrl,
  transportUnitPriceHint,
  trustStatusDescription,
  trustStatusLabel,
  trustStatusTone,
  weatherCoverageNote,
  type DerivedTrustStatus,
  type DisplayQualityIssue
} from '@/utils/tripTrust'

const route = useRoute()
const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const isPlanLoading = ref(true)
const planLoadingText = '\u6b63\u5728\u6062\u590d\u65c5\u884c\u8ba1\u5212...'
const currentPlanNo = ref<string | null>(null)
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
// Only structured quality.issues here — agent_audit stays in its own card.
const qualityIssuesList = computed((): DisplayQualityIssue[] => {
  if (!tripPlan.value) return []
  return normalizeQualityIssues(tripPlan.value.quality?.issues)
})

const groupedQualityIssues = computed(() => groupQualityIssues(qualityIssuesList.value))
const blockingIssues = computed(() => groupedQualityIssues.value.blocking)
const advisoryIssues = computed(() => groupedQualityIssues.value.advisory)
const infoIssues = computed(() => groupedQualityIssues.value.info)

const PREVIEW_PER_GROUP = 3
const visibleBlockingIssues = computed(() =>
  isQualityExpanded.value ? blockingIssues.value : blockingIssues.value.slice(0, PREVIEW_PER_GROUP)
)
const visibleAdvisoryIssues = computed(() =>
  isQualityExpanded.value ? advisoryIssues.value : advisoryIssues.value.slice(0, PREVIEW_PER_GROUP)
)
const visibleInfoIssues = computed(() =>
  isQualityExpanded.value ? infoIssues.value : infoIssues.value.slice(0, PREVIEW_PER_GROUP)
)

const trustStatus = computed((): DerivedTrustStatus => deriveTrustStatus(tripPlan.value?.quality))
const trustTone = computed(() => trustStatusTone(trustStatus.value))
const trustTitle = computed(() => trustStatusLabel(trustStatus.value))
const trustDescription = computed(() => trustStatusDescription(trustStatus.value))
const trustBadge = computed(() => {
  switch (trustStatus.value) {
    case 'blocked':
      return 'BLOCKED'
    case 'needs_review':
      return 'NEEDS REVIEW'
    case 'passed':
      return 'PASSED'
    default:
      return 'UNKNOWN'
  }
})
const isPlanBlocked = computed(() => trustStatus.value === 'blocked')
/** History-oriented edit/save: blocked and unknown cannot enter save path. */
const canEditForHistory = computed(() => canPersistPlan(tripPlan.value?.quality))
const editDisabledReason = computed(() => {
  if (trustStatus.value === 'blocked') {
    return '存在阻止使用的问题，无法编辑保存到历史记录'
  }
  if (trustStatus.value === 'unknown') {
    return '缺少服务端质量结果，无法编辑保存到历史记录'
  }
  return '当前无法保存到历史记录'
})
const qualityEmptyStateText = computed(() => {
  switch (trustStatus.value) {
    case 'blocked':
      return '当前方案不可保存/发送。结构化问题列表为空或已被安全过滤，仍请以顶部质量状态为准，并返回首页重新生成。'
    case 'unknown':
      return '未获得服务端结构化质量结果（可能是浏览器缓存草稿）。请勿当作已通过检查的正式方案；无法写入历史记录。'
    case 'needs_review':
      return '未列出额外结构化问题，但方案仍标记为需核对（例如评分、修复路径或生成模式）。票务与天气仍以出发前官方信息为准。'
    case 'passed':
      return '当前未列出结构化问题。票务、开放状态与实时天气仍以出发前官方信息为准。'
    default:
      return '质量状态未知。'
  }
})
const qualityTagColor = computed(() => {
  if (trustStatus.value === 'blocked') return 'red'
  if (trustStatus.value === 'needs_review') return 'orange'
  if (trustStatus.value === 'passed') return 'green'
  return 'default'
})

const generationMode = computed(() => normalizeGenerationMode(tripPlan.value?.generation_mode))
const generationModeTag = computed(() => {
  if (!tripPlan.value) return ''
  if (generationMode.value === 'primary') return ''
  return generationModeLabel(generationMode.value)
})
const generationModeHintText = computed(() => generationModeHint(generationMode.value))
const verifiedFactsText = computed(() => {
  const n = tripPlan.value?.quality?.verified_facts
  if (typeof n === 'number' && Number.isFinite(n) && n >= 0) {
    return `已核对事实 ${n}`
  }
  return ''
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
const renderedWebGuide = computed(() => renderSafeGuideMarkdown(displayWebGuide.value))

const safeWebReferences = computed(() => {
  const refs = tripPlan.value?.web_references ?? []
  return refs
    .map((ref) => {
      const url = safeHttpUrl(ref?.url)
      if (!url) return null
      return {
        title: String(ref?.title || '').trim(),
        site_name: String(ref?.site_name || '').trim(),
        url,
      }
    })
    .filter((x): x is { title: string; site_name: string; url: string } => x != null)
})

const budgetSourceMeta = computed(() => budgetSourceTrust(tripPlan.value?.budget?.budget_source))
const hotelUnitHint = computed(() =>
  hotelUnitPriceHint(
    tripPlan.value?.budget?.hotel_nights ?? 0,
    tripPlan.value?.budget?.hotel_rooms ?? 1,
  )
)
const transportUnitHint = computed(() => transportUnitPriceHint())

const formatBudgetAmount = (value: unknown) => formatMoneyCNY(value, '待确认')

const totalBudgetText = computed(() => {
  const total = tripPlan.value?.budget?.total
  return isFiniteMoney(total) ? formatMoneyCNY(total) : '待确认'
})

const tripDateList = computed(() => {
  if (!tripPlan.value) return [] as string[]
  const fromDays = (tripPlan.value.days ?? [])
    .map((d) => normalizeDateKey(d.date))
    .filter(Boolean)
  if (fromDays.length) return fromDays
  // Fall back to start/end only when day list empty — no string date guessing beyond given fields.
  const start = normalizeDateKey(tripPlan.value.start_date)
  const end = normalizeDateKey(tripPlan.value.end_date)
  if (start && end && start === end) return [start]
  if (start) return [start]
  return []
})

const weatherByDate = computed(() => {
  const map = new Map<string, NonNullable<TripPlan['weather_info']>[number]>()
  for (const w of tripPlan.value?.weather_info ?? []) {
    const d = normalizeDateKey(w?.date)
    if (d) map.set(d, w)
  }
  return map
})

const displayWeatherDays = computed(() => {
  const dates = tripDateList.value
  if (!dates.length) {
    // No structured trip dates: show raw server weather without inventing sunny defaults.
    return (tripPlan.value?.weather_info ?? []).map((w) => {
      const usable = isUsableWeatherDescription(w.day_weather, w.night_weather)
      return {
        date: normalizeDateKey(w.date) || w.date,
        day_weather: w.day_weather,
        night_weather: w.night_weather,
        day_temp: w.day_temp,
        night_temp: w.night_temp,
        wind_direction: w.wind_direction,
        wind_power: w.wind_power,
        missing: !usable,
      }
    })
  }
  return dates.map((date) => {
    const w = weatherByDate.value.get(date)
    if (!w || !isUsableWeatherDescription(w.day_weather, w.night_weather)) {
      return {
        date,
        day_weather: '',
        night_weather: '',
        day_temp: '',
        night_temp: '',
        wind_direction: '',
        wind_power: '',
        missing: true,
      }
    }
    return {
      date,
      day_weather: w.day_weather,
      night_weather: w.night_weather,
      day_temp: w.day_temp,
      night_temp: w.night_temp,
      wind_direction: w.wind_direction,
      wind_power: w.wind_power,
      missing: false,
    }
  })
})

const weatherCoverageSummary = computed(() => {
  const weatherDates = (tripPlan.value?.weather_info ?? [])
    .filter((w) => isUsableWeatherDescription(w.day_weather, w.night_weather))
    .map((w) => normalizeDateKey(w.date))
    .filter(Boolean)
  return weatherCoverageNote(tripDateList.value, weatherDates).summary
})

const weatherReviewText = computed(() => {
  if (!tripPlan.value) return '暂未获取到可展示的天气信息。'
  return `${tripPlan.value.city} ${tripPlan.value.start_date} 至 ${tripPlan.value.end_date} 的逐日天气暂未获取到可靠预报。请在出发前 3–7 天复核每日天气、温差和降雨。`
})

const poiTrustLabel = (source?: string) => poiCoordinateTrustLabel(source).label
const poiTrustTagColor = (source?: string) => {
  const tone = poiCoordinateTrustLabel(source).tone
  if (tone === 'success') return 'green'
  if (tone === 'warning') return 'orange'
  return 'default'
}
const routeTrustText = (route: { verified?: boolean; source?: string }) => routeTrustLabel(route).label
const routeShowMetrics = (route: { verified?: boolean; source?: string; distance?: number; duration?: number }) =>
  routeTrustLabel(route).showMetrics
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

  const quality = raw.quality && typeof raw.quality === 'object'
    ? {
        ...raw.quality,
        publishable: asStrictBool(raw.quality.publishable),
        review_required: asStrictBool(raw.quality.review_required),
        issues: Array.isArray(raw.quality.issues) ? raw.quality.issues : [],
        checked_items: Array.isArray(raw.quality.checked_items) ? raw.quality.checked_items : [],
      }
    : undefined

  return {
    ...raw,
    generation_mode: normalizeGenerationMode(raw.generation_mode),
    days,
    weather_info: Array.isArray(raw.weather_info) ? raw.weather_info : [],
    budget,
    web_guide: typeof raw.web_guide === 'string' ? raw.web_guide : null,
    web_references: Array.isArray(raw.web_references) ? raw.web_references : [],
    agent_audit: agentAudit,
    quality,
    map_context: Array.isArray(raw.map_context) ? raw.map_context : []
  } as TripPlan
}

const activatePlan = async (raw: unknown, planNo?: string | null) => {
  const normalized = normalizeTripPlan(raw)
  if (!normalized) throw new Error('旅行计划数据格式无效')
  currentPlanNo.value = planNo || null
  tripPlan.value = normalized
  saveTripCache(normalized, currentPlanNo.value, cacheRetention.value)
  saveTripSession(normalized)
  if (!normalized.map_context?.length) {
    const mapContext = await fetchMapContext(normalized)
    if (mapContext.length && tripPlan.value) {
      tripPlan.value.map_context = mapContext
      saveTripCache(tripPlan.value, currentPlanNo.value, cacheRetention.value)
      saveTripSession(tripPlan.value)
      // Never auto-persist map context for non-publishable / blocked plans.
      if (currentPlanNo.value && canPersistPlan(tripPlan.value.quality)) {
        updateTripPlan(currentPlanNo.value, tripPlan.value).catch(error => {
          console.warn('[result] map context persistence skipped:', error)
        })
      }
    }
  }
  await nextTick()
  if (!isMobileViewport.value) initMap()
  loadAttractionPhotos()
}

onMounted(async () => {
  isMobileViewport.value = window.matchMedia('(max-width: 768px)').matches
  const queryPlanNo = typeof route.query.plan === 'string' ? route.query.plan : ''
  try {
    if (queryPlanNo) {
      try {
        const response = await fetchTripPlan(queryPlanNo)
        if (response.data) {
          await activatePlan(response.data, response.plan_no || queryPlanNo)
          return
        }
      } catch (error) {
        console.warn('[result] historical plan unavailable, using local draft', error)
      }
    }

    const cached = loadTripCache()
    if (cached) {
      await activatePlan(cached.plan, cached.planNo)
      return
    }

    const legacy = loadTripSession()
    if (legacy) {
      await activatePlan(legacy, null)
      return
    }
    console.warn('[result] no cached or historical trip plan found')
  } catch (error: any) {
    console.error('[result] failed to load trip plan', error)
    message.error(error.message || '结果页数据无效，请重新生成行程')
  } finally {
    isPlanLoading.value = false
  }
})

let draftTimer: number | undefined
watch(
  tripPlan,
  value => {
    if (!value) return
    window.clearTimeout(draftTimer)
    draftTimer = window.setTimeout(() => {
      saveTripCache(value, currentPlanNo.value, cacheRetention.value)
    }, 250)
  },
  { deep: true }
)

const changeCacheRetention = (value: CacheRetentionMinutes) => {
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
  if (!canEditForHistory.value) {
    message.error(editDisabledReason.value + '。请返回首页重新生成。')
    return
  }
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

const isSavingChanges = ref(false)

// 保存修改 — 与后端门禁对齐：blocked 不可持久化；needs_review 需确认
const saveChanges = async () => {
  if (!tripPlan.value || isSavingChanges.value) return

  // Handler-level gate (not CSS-only). Re-read quality from live plan (not a cached flag).
  if (!canPersistPlan(tripPlan.value.quality)) {
    if (trustStatus.value === 'unknown') {
      message.error('缺少服务端质量结果，无法将草稿保存为历史记录。请返回首页重新生成行程。')
    } else {
      message.error('当前方案存在阻止使用的问题，无法保存到历史记录或作为可出发方案。')
    }
    editMode.value = false
    return
  }

  if (trustStatus.value === 'needs_review') {
    const ok = window.confirm(
      '该方案仍有待核对事项。确认你已查看质量提示，并仍要保存修改？',
    )
    if (!ok) return
  }

  isSavingChanges.value = true
  editMode.value = false
  // Local draft keeps viewing recovery; server history requires publishable quality.
  saveTripSession(tripPlan.value)
  saveTripCache(tripPlan.value, currentPlanNo.value, cacheRetention.value)
  if (currentPlanNo.value) {
    try {
      // Re-check immediately before network write (quality may have been mutated in memory).
      if (!canPersistPlan(tripPlan.value.quality)) {
        message.error('质量状态已变更，已取消写入历史记录。')
        return
      }
      await updateTripPlan(currentPlanNo.value, tripPlan.value)
      message.success(
        trustStatus.value === 'needs_review'
          ? '修改已保存到历史记录（仍含待核对事项）'
          : '修改已保存到历史记录',
      )
    } catch (error: any) {
      const detail = error?.response?.data?.detail
      const serverMsg =
        typeof detail === 'string'
          ? detail
          : detail?.message || error?.message || '同步失败'
      message.warning(`修改已保存在本地草稿，但同步历史记录失败：${serverMsg}`)
    } finally {
      isSavingChanges.value = false
    }
  } else {
    isSavingChanges.value = false
    message.success('修改已保存到本地草稿')
  }

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
  const trustLine = escapeHtml(`${trustTitle.value}（${trustBadge.value}）`)
  const trustNote = escapeHtml(trustDescription.value)
  summary.innerHTML = `
    <div class="export-summary__kicker">LINGTU TRIP DOSSIER</div>
    <div class="export-summary__headline">
      <div>
        <h1>${escapeHtml(tripPlan.value?.city || '旅行计划')}</h1>
        <p>${escapeHtml(tripPlan.value?.start_date || '')} 至 ${escapeHtml(tripPlan.value?.end_date || '')}</p>
        <p class="export-summary__trust"><strong>方案质量：</strong>${trustLine}</p>
        <p class="export-summary__trust-note">${trustNote}</p>
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
const downloadCanvas = async (canvas: HTMLCanvasElement, filename: string) => {
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      value => value ? resolve(value) : reject(new Error('图片压缩失败')),
      'image/jpeg',
      0.92
    )
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.download = filename
  link.href = url
  link.click()
  URL.revokeObjectURL(url)
}

const exportAsImage = async () => {
  let container: HTMLElement | null = null
  try {
    message.loading({ content: '正在生成高清图片...', key: 'export', duration: 0 })
    await expandAllDaysForExport()
    container = await createExportContainer()
    const canvas = await renderExportCanvas(container, 2.3)
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
  let container: HTMLElement | null = null
  try {
    message.loading({ content: '正在生成高清 PDF...', key: 'export', duration: 0 })
    await expandAllDaysForExport()
    container = await createExportContainer()
    const canvas = await renderExportCanvas(container, 2.15)

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
      content: `<div style="padding:10px"><h4 style="margin:0 0 8px">${hotel.name}</h4><p>${hotel.address}</p><p>${hotel.distance}</p></div>`,
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
.trust-status-banner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px 24px;
  margin: 0 0 16px;
  padding: 16px 18px;
  border-radius: 12px;
  border: 1px solid #d9d9d9;
  background: #fafafa;
}
.trust-status-banner.tone-danger {
  border-color: #ffa39e;
  background: #fff2f0;
}
.trust-status-banner.tone-warning {
  border-color: #ffd591;
  background: #fffbe6;
}
.trust-status-banner.tone-success {
  border-color: #b7eb8f;
  background: #f6ffed;
}
.trust-status-banner.tone-neutral {
  border-color: #d9d9d9;
  background: #fafafa;
}
.trust-status-kicker {
  display: block;
  font-size: 12px;
  letter-spacing: 0.04em;
  color: #8c8c8c;
  margin-bottom: 4px;
}
.trust-status-title {
  margin: 0 0 8px;
  font-size: 20px;
  line-height: 1.3;
  color: #141414;
}
.trust-status-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.trust-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid transparent;
}
.trust-tag.tone-danger {
  color: #a8071a;
  background: #fff1f0;
  border-color: #ffa39e;
}
.trust-tag.tone-warning {
  color: #ad4e00;
  background: #fff7e6;
  border-color: #ffd591;
}
.trust-tag.tone-success {
  color: #237804;
  background: #f6ffed;
  border-color: #b7eb8f;
}
.trust-tag.tone-neutral {
  color: #595959;
  background: #f5f5f5;
  border-color: #d9d9d9;
}
.trust-status-desc,
.trust-status-mode-hint,
.trust-status-action-hint {
  margin: 0 0 6px;
  color: #434343;
  font-size: 14px;
  line-height: 1.55;
}
.trust-status-action-hint {
  font-weight: 500;
}
.trust-status-side {
  display: grid;
  grid-template-columns: repeat(3, minmax(64px, 1fr));
  gap: 8px;
  align-content: start;
}
.trust-stat {
  min-width: 72px;
  padding: 10px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(0, 0, 0, 0.06);
  text-align: center;
}
.trust-stat span {
  display: block;
  font-size: 12px;
  color: #8c8c8c;
}
.trust-stat strong {
  display: block;
  margin-top: 4px;
  font-size: 18px;
  color: #141414;
}

.quality-advisory-card {
  margin-bottom: 16px;
  border-radius: 8px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
}
.quality-advisory-card.quality-tone-danger {
  background: #fff2f0;
  border-color: #ffa39e;
}
.quality-advisory-card.quality-tone-success {
  background: #f6ffed;
  border-color: #b7eb8f;
}
.quality-advisory-card.quality-tone-neutral {
  background: #fafafa;
  border-color: #d9d9d9;
}
.quality-advisory-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  gap: 8px;
  flex-wrap: wrap;
}
.quality-advisory-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: #262626;
  flex-wrap: wrap;
}
.advisory-status-tag {
  font-weight: normal;
}
.advisory-count-badge {
  font-size: 12px;
  color: #8c8c8c;
  font-weight: normal;
}
.quality-empty-ok {
  color: #595959;
  font-size: 14px;
  line-height: 1.5;
}
.quality-advisory-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.quality-group-title {
  font-size: 13px;
  font-weight: 600;
  color: #595959;
  margin-bottom: 8px;
}
.quality-issue-item {
  padding: 10px 12px;
  background: #ffffff;
  border-radius: 6px;
  border-left: 4px solid #fa8c16;
  margin-bottom: 8px;
}
.quality-issue-item.blocking {
  border-left-color: #f5222d;
  background: #fff7f6;
}
.quality-issue-item.info {
  border-left-color: #1890ff;
}
.quality-issue-item.warning {
  border-left-color: #fa8c16;
}
.issue-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.issue-badge {
  font-size: 12px;
}
.issue-message {
  font-weight: 500;
  color: #262626;
}
.issue-code {
  font-size: 12px;
  color: #8c8c8c;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.issue-suggestion {
  margin-top: 4px;
  font-size: 13px;
  color: #595959;
  padding-left: 4px;
}
.poi-trust-tag {
  margin-top: 6px;
}
.route-meta-pending {
  color: #8c8c8c;
  font-size: 13px;
}
.budget-source-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  color: #595959;
  font-size: 13px;
}
.budget-unit-hint,
.budget-disclaimer,
.weather-source-note,
.reference-disclaimer {
  color: #8c8c8c;
  font-size: 12px;
  line-height: 1.5;
}
.weather-source-note {
  margin: 0 0 6px;
  color: #595959;
  font-size: 13px;
}
.weather-source-note.secondary {
  margin-bottom: 12px;
  color: #8c8c8c;
  font-size: 12px;
}
.weather-missing {
  border-style: dashed;
}
.weather-missing-body {
  min-height: 72px;
  display: flex;
  align-items: center;
  color: #8c8c8c;
  font-size: 14px;
}
.budget-disclaimer {
  margin-top: 10px;
}
.reference-disclaimer {
  margin: 0 0 10px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 768px) {
  .trust-status-banner {
    grid-template-columns: 1fr;
    padding: 14px;
  }
  .trust-status-title {
    font-size: 18px;
  }
  .trust-status-side {
    grid-template-columns: repeat(3, 1fr);
  }
  .quality-advisory-header {
    align-items: flex-start;
  }
}

@media (max-width: 430px) {
  .trust-stat strong {
    font-size: 16px;
  }
  .trust-status-tags {
    gap: 6px;
  }
}
</style>
