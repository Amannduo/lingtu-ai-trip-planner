<template>
  <div class="chart-shell">
    <div ref="chartEl" class="chart-canvas"></div>
    <div v-if="loadError" class="chart-fallback">
      图表组件待安装 ECharts 后显示。
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  option: Record<string, any>
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const loadError = ref(false)
let chart: any = null

const renderChart = async () => {
  if (!chartEl.value || !props.option) return
  try {
    const echarts = await import('echarts')
    await nextTick()
    if (!chart) {
      chart = echarts.init(chartEl.value)
    }
    chart.setOption(props.option, true)
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
  min-height: 280px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
}

.chart-canvas {
  width: 100%;
  height: 280px;
}

.chart-fallback {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #667085;
  font-size: 14px;
  background: rgba(248, 251, 251, 0.92);
}
</style>
