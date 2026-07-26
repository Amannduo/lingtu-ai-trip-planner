<template>
  <div class="chart-shell">
    <div v-if="chartTitle" class="chart-header">
      <h4>{{ chartTitle }}</h4>
    </div>
    <div ref="chartEl" class="chart-canvas"></div>
    <div v-if="loadError" class="chart-fallback">
      图表组件加载失败，请刷新后重试
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  option: Record<string, any>
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const loadError = ref(false)
let chart: any = null

const chartTitle = computed(() => {
  const title = props.option?.title
  if (!title) return ''
  if (typeof title === 'string') return title
  if (typeof title?.text === 'string') return title.text
  return ''
})

/** Soft theme defaults so sparse backend options still look polished. */
const polishOption = (raw: Record<string, any>) => {
  const option = { ...raw }

  // Title lives in the DOM header — free vertical space for the plot.
  option.title = {
    ...(typeof raw.title === 'object' ? raw.title : { text: raw.title }),
    show: false
  }

  option.color = option.color || [
    '#0f766e',
    '#2563eb',
    '#14b8a6',
    '#6366f1',
    '#0ea5e9',
    '#059669'
  ]
  option.backgroundColor = option.backgroundColor || 'transparent'
  option.textStyle = {
    fontFamily: 'Segoe UI, PingFang SC, Microsoft YaHei, sans-serif',
    color: '#334155',
    ...(option.textStyle || {})
  }

  const isPie = Array.isArray(option.series)
    && option.series.some((item: any) => item?.type === 'pie')

  if (!isPie) {
    option.grid = {
      left: 20,
      right: 20,
      top: 36,
      bottom: 32,
      containLabel: true,
      ...(option.grid || {})
    }
    // Prefer roomy defaults even if backend sent a tight grid.
    option.grid.top = Math.max(Number(option.grid.top) || 0, 36)
    option.grid.bottom = Math.max(Number(option.grid.bottom) || 0, 28)
  } else if (Array.isArray(option.series)) {
    option.series = option.series.map((series: any) => {
      if (series?.type !== 'pie') return series
      return {
        ...series,
        radius: series.radius || ['44%', '70%'],
        center: series.center || ['50%', '46%']
      }
    })
  }

  if (option.tooltip && typeof option.tooltip === 'object') {
    option.tooltip = {
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [10, 12],
      textStyle: { color: '#f8fafc', fontSize: 12 },
      extraCssText: 'border-radius:10px;box-shadow:0 12px 28px rgba(15,23,42,0.18);',
      ...option.tooltip
    }
  }
  return option
}

const renderChart = async () => {
  if (!chartEl.value || !props.option) return
  try {
    const echarts = await import('echarts')
    await nextTick()
    if (!chart) {
      chart = echarts.init(chartEl.value, undefined, {
        renderer: 'canvas',
        devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2)
      })
    }
    chart.setOption(polishOption(props.option), true)
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

watch(() => props.option, renderChart, { deep: true })

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
  min-height: 420px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 16px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.99), rgba(248, 250, 252, 0.94));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
  overflow: hidden;
}

.chart-header {
  flex: 0 0 auto;
  padding: 14px 16px 0;
}

.chart-header h4 {
  margin: 0;
  color: #0f172a;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: -0.01em;
  line-height: 1.4;
}

.chart-canvas {
  flex: 1 1 auto;
  width: 100%;
  min-height: 360px;
  height: 380px;
  padding: 4px 8px 10px;
  box-sizing: border-box;
}

.chart-fallback {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #64748b;
  font-size: 13px;
  background: rgba(248, 250, 252, 0.94);
}
</style>
