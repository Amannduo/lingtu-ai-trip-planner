<template>
  <div class="chart-shell" role="img" :aria-label="ariaLabel">
    <div v-if="payload?.title" class="chart-header">
      <h4>{{ payload.title }}</h4>
      <p v-if="payload.note" class="chart-note">{{ payload.note }}</p>
    </div>

    <div v-if="!payload" class="chart-fallback">
      图表数据无效或为空，已改用下方表格（如有）。
    </div>
    <template v-else>
      <div ref="chartEl" class="chart-canvas"></div>
      <div v-if="loadError" class="chart-fallback">
        图表组件加载失败，请查看下方数据摘要。
      </div>
      <details class="chart-a11y">
        <summary>查看数据摘要</summary>
        <p class="chart-summary">{{ textSummary }}</p>
        <table>
          <thead>
            <tr>
              <th scope="col">{{ payload.x_label || '类别' }}</th>
              <th
                v-for="item in payload.series"
                :key="item.name"
                scope="col"
              >
                {{ item.name || payload.y_label || '数值' }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(category, index) in payload.categories" :key="`${category}-${index}`">
              <th scope="row">{{ category }}</th>
              <td v-for="item in payload.series" :key="`${item.name}-${index}`">
                {{ formatNumber(item.values[index]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </details>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  normalizeAgentChart,
  type AgentChartPayload
} from '@/types/agentChart'

const props = defineProps<{
  /** Restricted typed payload — never a raw ECharts option object. */
  chart: unknown
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const loadError = ref(false)
let chart: { setOption: (option: object, notMerge?: boolean) => void; resize: () => void; dispose: () => void } | null = null

const payload = computed(() => normalizeAgentChart(props.chart))

const ariaLabel = computed(() => {
  if (!payload.value) return '无效图表'
  return payload.value.title || '数据分析图表'
})

const textSummary = computed(() => {
  const data = payload.value
  if (!data) return '无可用图表数据。'
  const seriesNames = data.series.map((item) => item.name).join('、')
  return `${data.kind} 图，${data.categories.length} 个类别，系列：${seriesNames || '未命名'}。`
})

const formatNumber = (value: number | undefined) => {
  if (value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
}

const PALETTE = ['#0f766e', '#2563eb', '#14b8a6', '#6366f1', '#0ea5e9', '#059669']

/**
 * Map restricted payload → fixed ECharts option.
 * No user formatters, no HTML tooltips, no rich-text objects from payload.
 */
const toEchartsOption = (data: AgentChartPayload) => {
  // renderMode richText avoids HTML parsing of label strings in tooltips.
  const safeTooltip = {
    trigger: data.kind === 'pie' ? 'item' : 'axis',
    renderMode: 'richText' as const,
    // Never accept payload-controlled formatter / appendToBody HTML.
    confine: true
  }

  if (data.kind === 'pie') {
    const values = data.series[0]?.values || []
    return {
      color: PALETTE,
      backgroundColor: 'transparent',
      title: { show: false },
      tooltip: safeTooltip,
      legend: {
        bottom: 0,
        type: 'scroll',
        // legend labels are plain strings from normalized payload only
        data: data.categories
      },
      series: [
        {
          name: data.series[0]?.name || data.y_label || '占比',
          type: 'pie',
          radius: ['38%', '66%'],
          label: { show: true },
          data: data.categories.map((name, index) => ({
            name,
            value: values[index] ?? 0
          }))
        }
      ]
    }
  }

  return {
    color: PALETTE,
    backgroundColor: 'transparent',
    title: { show: false },
    tooltip: safeTooltip,
    legend: { top: 8, type: 'scroll' },
    grid: { left: 24, right: 16, top: 40, bottom: 28, containLabel: true },
    xAxis: {
      type: 'category',
      data: data.categories,
      name: data.x_label || undefined,
      axisLabel: {
        rotate: data.categories.length > 6 ? 24 : 0,
        hideOverlap: true
      }
    },
    yAxis: {
      type: 'value',
      name: data.y_label || undefined
    },
    series: data.series.map((item) => ({
      name: item.name,
      type: data.kind,
      smooth: data.kind === 'line',
      data: item.values,
      barMaxWidth: 28
    }))
  }
}



const renderChart = async () => {
  const data = payload.value
  if (!chartEl.value || !data) {
    chart?.dispose()
    chart = null
    return
  }
  try {
    const echarts = await import('echarts')
    await nextTick()
    if (!chartEl.value) return
    if (!chart) {
      chart = echarts.init(chartEl.value, undefined, {
        renderer: 'canvas',
        devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2)
      })
    }
    chart.setOption(toEchartsOption(data), true)
    chart.resize()
    loadError.value = false
  } catch (error) {
    console.warn('ECharts load failed:', error)
    loadError.value = true
  }
}

const handleResize = () => {
  chart?.resize()
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

watch(
  () => props.chart,
  () => {
    renderChart()
  },
  { deep: true }
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart-shell {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 320px;
  border: 1px solid #dce8e4;
  border-radius: 12px;
  background: #ffffff;
  overflow: hidden;
}

.chart-header {
  padding: 12px 14px 0;
}

.chart-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.chart-note {
  margin: 4px 0 0;
  font-size: 12px;
  color: #64748b;
}

.chart-canvas {
  flex: 1 1 auto;
  width: 100%;
  min-height: 280px;
  height: 300px;
}

.chart-fallback {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

.chart-a11y {
  margin: 0 12px 12px;
  font-size: 12px;
  color: #475569;
}

.chart-a11y summary {
  cursor: pointer;
  user-select: none;
}

.chart-summary {
  margin: 8px 0;
}

.chart-a11y table {
  width: 100%;
  border-collapse: collapse;
}

.chart-a11y th,
.chart-a11y td {
  border: 1px solid #e2e8f0;
  padding: 4px 6px;
  text-align: left;
}
</style>
