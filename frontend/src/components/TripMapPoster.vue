<template>
  <section id="print-map" class="poster-section pdf-break-unit">
    <div class="poster-heading">
      <div>
        <span class="poster-kicker">AMAP CONTEXT TRIP ATLAS</span>
        <h2>{{ plan.city }}行程地图手册</h2>
        <p>真实路线、酒店与高德周边场所，适合总览、打印和简单找方向。</p>
      </div>
      <div class="poster-actions export-hidden">
        <a-button title="放大查看" @click="previewOpen = true">
          <ZoomInOutlined />
          <span>放大</span>
        </a-button>
        <a-dropdown>
          <template #overlay>
            <a-menu>
              <a-menu-item key="svg" @click="downloadSvg">下载 SVG</a-menu-item>
              <a-menu-item key="png" @click="downloadPng">下载高清 PNG</a-menu-item>
            </a-menu>
          </template>
          <a-button title="下载地图">
            <DownloadOutlined />
            <span>下载</span>
          </a-button>
        </a-dropdown>
      </div>
    </div>

    <button type="button" class="poster-frame" title="点击放大行程地图" @click="previewOpen = true">
      <div class="poster-svg" v-html="svgMarkup"></div>
    </button>
    <div class="poster-note">
      景点、酒店、路线和周边场所均来自高德坐标；可用于场景总览，实时导航仍以手机地图为准。
    </div>

    <a-modal
      v-model:open="previewOpen"
      width="min(96vw, 1440px)"
      :footer="null"
      title="行程地图手册"
      destroy-on-close
    >
      <div class="poster-modal-svg" v-html="svgMarkup"></div>
      <div class="modal-actions">
        <a-button @click="downloadSvg"><DownloadOutlined />下载 SVG</a-button>
        <a-button type="primary" @click="downloadPng"><DownloadOutlined />下载高清 PNG</a-button>
      </div>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { DownloadOutlined, ZoomInOutlined } from '@ant-design/icons-vue'
import type { Attraction, Location, MapContextPOI, TripPlan } from '@/types'

const props = defineProps<{ plan: TripPlan }>()
const previewOpen = ref(false)

const WIDTH = 1200
const HEIGHT = 1110
const MAP_TOP = 104
const MAP_BOTTOM = 646
const MAP_LEFT = 306
const MAP_RIGHT = 894
const LABEL_LEFT = 42
const LABEL_RIGHT = 908
const LABEL_WIDTH = 250
const DAY_COLORS = ['#0f766e', '#dc2626', '#2563eb', '#d97706', '#15803d', '#9333ea', '#475569', '#be123c']

const CATEGORY_STYLE: Record<string, { color: string; symbol: string }> = {
  餐饮: { color: '#dc2626', symbol: '餐' },
  商店: { color: '#d97706', symbol: '店' },
  周边景点: { color: '#15803d', symbol: '景' },
  交通: { color: '#2563eb', symbol: '站' }
}

type ValidLocation = { longitude: number; latitude: number }
type Box = { x: number; y: number; width: number; height: number }
type Projection = {
  project: (location: ValidLocation) => { x: number; y: number }
  scale: number
  spanKm: number
}

type PosterPoint = {
  x: number
  y: number
  dayIndex: number
  attractionIndex: number
  globalIndex: number
  attraction: Attraction
}

type CalloutLayout = {
  point: PosterPoint
  side: 'left' | 'right'
  box: Box
  lines: string[]
}

const escapeXml = (value: unknown) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&apos;')

const validLocation = (location?: Location | null): ValidLocation | null => {
  const longitude = Number(location?.longitude)
  const latitude = Number(location?.latitude)
  return Number.isFinite(longitude) && Number.isFinite(latitude)
    && Math.abs(longitude) <= 180 && Math.abs(latitude) <= 90
    ? { longitude, latitude }
    : null
}

const wrapText = (value: string, maxCharacters: number) => {
  const characters = Array.from(value.trim())
  const lines: string[] = []
  for (let index = 0; index < characters.length; index += maxCharacters) {
    lines.push(characters.slice(index, index + maxCharacters).join(''))
  }
  return lines.length ? lines : ['']
}

const collectMapLocations = (): ValidLocation[] => {
  const locations: ValidLocation[] = []
  const add = (location?: Location | null) => {
    const value = validLocation(location)
    if (value) locations.push(value)
  }

  props.plan.days.forEach(day => {
    day.attractions.forEach(attraction => add(attraction.location))
    add(day.hotel?.location)
    day.routes?.forEach(route => {
      const path = route.path || []
      const step = Math.max(1, Math.floor(path.length / 160))
      path.forEach((point, index) => {
        if (index % step === 0 || index === path.length - 1) add(point)
      })
    })
  })
  ;(props.plan.map_context || []).forEach(item => add(item.location))
  return locations
}

const createProjection = (): Projection | null => {
  const locations = collectMapLocations()
  if (!locations.length) return null

  const averageLatitude = locations.reduce((sum, item) => sum + item.latitude, 0) / locations.length
  const longitudeFactor = Math.max(0.25, Math.cos(averageLatitude * Math.PI / 180))
  const values = locations.map(item => ({
    x: item.longitude * longitudeFactor,
    y: item.latitude
  }))
  let minX = Math.min(...values.map(item => item.x))
  let maxX = Math.max(...values.map(item => item.x))
  let minY = Math.min(...values.map(item => item.y))
  let maxY = Math.max(...values.map(item => item.y))

  if (maxX - minX < 0.004) {
    minX -= 0.002
    maxX += 0.002
  }
  if (maxY - minY < 0.004) {
    minY -= 0.002
    maxY += 0.002
  }

  const marginX = (maxX - minX) * 0.07
  const marginY = (maxY - minY) * 0.08
  minX -= marginX
  maxX += marginX
  minY -= marginY
  maxY += marginY

  const scale = Math.min(
    (MAP_RIGHT - MAP_LEFT) / (maxX - minX),
    (MAP_BOTTOM - MAP_TOP) / (maxY - minY)
  )
  const drawnWidth = (maxX - minX) * scale
  const drawnHeight = (maxY - minY) * scale
  const offsetX = MAP_LEFT + ((MAP_RIGHT - MAP_LEFT) - drawnWidth) / 2
  const offsetY = MAP_TOP + ((MAP_BOTTOM - MAP_TOP) - drawnHeight) / 2

  return {
    scale,
    spanKm: Math.max(maxX - minX, maxY - minY) * 111,
    project: location => ({
      x: offsetX + (location.longitude * longitudeFactor - minX) * scale,
      y: offsetY + (maxY - location.latitude) * scale
    })
  }
}

const boxesOverlap = (left: Box, right: Box) =>
  left.x < right.x + right.width
  && left.x + left.width > right.x
  && left.y < right.y + right.height
  && left.y + left.height > right.y

const layoutAttractionCallouts = (points: PosterPoint[]): CalloutLayout[] => {
  const grouped: Record<'left' | 'right', Array<{
    point: PosterPoint
    lines: string[]
    height: number
  }>> = {
    left: [],
    right: []
  }

  points
    .slice()
    .sort((left, right) => left.y - right.y)
    .forEach(point => {
      const lines = wrapText(
        'D' + (point.dayIndex + 1) + ' · ' + point.attraction.name,
        19
      )
      const leftCost = Math.abs(point.x - MAP_LEFT) + grouped.left.length * 62
      const rightCost = Math.abs(MAP_RIGHT - point.x) + grouped.right.length * 62
      const side = leftCost <= rightCost ? 'left' : 'right'
      grouped[side].push({
        point,
        lines,
        height: lines.length * 15 + 12
      })
    })

  const layoutSide = (
    items: Array<{ point: PosterPoint; lines: string[]; height: number }>,
    side: 'left' | 'right'
  ): CalloutLayout[] => {
    if (!items.length) return []

    const minY = MAP_TOP + 8
    const maxY = MAP_BOTTOM - 8
    const totalHeight = items.reduce((sum, item) => sum + item.height, 0)
    const availableGap = items.length > 1
      ? (maxY - minY - totalHeight) / (items.length - 1)
      : 0
    const gap = Math.max(0, Math.min(7, availableGap))
    const x = side === 'left' ? LABEL_LEFT : LABEL_RIGHT
    const layouts = items.map(item => ({
      point: item.point,
      side,
      lines: item.lines,
      box: {
        x,
        y: Math.max(minY, Math.min(item.point.y - item.height / 2, maxY - item.height)),
        width: LABEL_WIDTH,
        height: item.height
      }
    }))

    let cursor = minY
    layouts.forEach(layout => {
      layout.box.y = Math.max(layout.box.y, cursor)
      cursor = layout.box.y + layout.box.height + gap
    })

    if (cursor - gap > maxY) {
      cursor = maxY
      for (let index = layouts.length - 1; index >= 0; index -= 1) {
        const layout = layouts[index]
        layout.box.y = Math.min(layout.box.y, cursor - layout.box.height)
        cursor = layout.box.y - gap
      }
    }

    return layouts
  }

  return [
    ...layoutSide(grouped.left, 'left'),
    ...layoutSide(grouped.right, 'right')
  ]
}

const buildPolyline = (
  locations: Array<Location | undefined>,
  projection: Projection
) => locations
  .map(location => validLocation(location))
  .filter((location): location is ValidLocation => Boolean(location))
  .map(location => projection.project(location))
  .map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`)
  .join(' ')

const buildSvg = () => {
  const projection = createProjection()
  if (!projection) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}"><rect width="100%" height="100%" fill="#ffffff"/><text x="50%" y="50%" text-anchor="middle" fill="#475467" font-size="24">暂无有效地图坐标</text></svg>`
  }


  const points: PosterPoint[] = []
  props.plan.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attractionIndex) => {
      const location = validLocation(attraction.location)
      if (!location) return
      const point = projection.project(location)
      points.push({
        ...point,
        dayIndex,
        attractionIndex,
        globalIndex: points.length,
        attraction
      })
    })
  })

  const routeLines = props.plan.days.map((day, dayIndex) => {
    const color = DAY_COLORS[dayIndex % DAY_COLORS.length]
    const verified = (day.routes || [])
      .map(route => buildPolyline(route.path || [], projection))
      .filter(path => path.split(' ').length >= 2)
    const paths = verified.length
      ? verified
      : [buildPolyline(day.attractions.map(item => item.location), projection)].filter(Boolean)
    return paths.map(path => `
      <polyline points="${path}" fill="none" stroke="#ffffff" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>
      <polyline points="${path}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" ${verified.length ? '' : 'stroke-dasharray="11 7"'}/>
    `).join('')
  }).join('')

  const contextBadgeOccupied: Box[] = points.map(point => ({
    x: point.x - 23,
    y: point.y - 23,
    width: 46,
    height: 46
  }))
  const contextOffsets = [
    [0, 0], [0, -21], [21, 0], [0, 21], [-21, 0],
    [15, -15], [15, 15], [-15, 15], [-15, -15],
    [0, -40], [40, 0], [0, 40], [-40, 0],
    [28, -28], [28, 28], [-28, 28], [-28, -28],
    [0, -58], [58, 0], [0, 58], [-58, 0]
  ]

  const contextCounters: Record<string, number> = {}
  const contextEntries = (props.plan.map_context || []).flatMap((item: MapContextPOI) => {
    const location = validLocation(item.location)
    if (!location) return []
    const point = projection.project(location)
    const style = CATEGORY_STYLE[item.category] || { color: '#64748b', symbol: '点' }
    const categoryIndex = (contextCounters[item.category] || 0) + 1
    contextCounters[item.category] = categoryIndex

    const badgeBox = contextOffsets
      .map(([offsetX, offsetY]) => ({
        x: point.x + offsetX - 9,
        y: point.y + offsetY - 9,
        width: 18,
        height: 18
      }))
      .find(box =>
        box.x >= MAP_LEFT - 4
        && box.y >= MAP_TOP
        && box.x + box.width <= MAP_RIGHT + 4
        && box.y + box.height <= MAP_BOTTOM
        && !contextBadgeOccupied.some(occupiedBox => boxesOverlap(box, occupiedBox))
      ) || {
        x: point.x - 9,
        y: point.y - 9,
        width: 18,
        height: 18
      }

    contextBadgeOccupied.push(badgeBox)
    return [{
      item,
      point,
      badge: {
        x: badgeBox.x + badgeBox.width / 2,
        y: badgeBox.y + badgeBox.height / 2
      },
      style,
      categoryIndex
    }]
  })

  const contextMarkers = contextEntries.map(entry => {
    const offsetDistance = Math.hypot(
      entry.badge.x - entry.point.x,
      entry.badge.y - entry.point.y
    )
    return `
      <g>
        <circle cx="${entry.point.x}" cy="${entry.point.y}" r="2.4" fill="${entry.style.color}"/>
        ${offsetDistance > 2 ? `<line x1="${entry.point.x}" y1="${entry.point.y}" x2="${entry.badge.x}" y2="${entry.badge.y}" stroke="${entry.style.color}" stroke-width="1" opacity="0.48"/>` : ''}
        <circle cx="${entry.badge.x}" cy="${entry.badge.y}" r="8.5" fill="#ffffff" stroke="${entry.style.color}" stroke-width="2.5"/>
        <text x="${entry.badge.x}" y="${entry.badge.y + 3.2}" text-anchor="middle" font-size="8" font-weight="800" fill="${entry.style.color}">${entry.categoryIndex}</text>
      </g>
    `
  }).join('')

  const hotel = props.plan.days.map(day => day.hotel).find(item => validLocation(item?.location))
  let hotelMarker = ''
  let hotelLegend = ''
  if (hotel?.location) {
    const location = validLocation(hotel.location)
    if (location) {
      const point = projection.project(location)
      const legendLines = wrapText('酒店 · ' + hotel.name, 70)
      const legendText = legendLines.map((line, index) =>
        `<tspan x="82" y="${720 + index * 15}">${escapeXml(line)}</tspan>`
      ).join('')
      hotelMarker = `
        <g>
          <rect x="${point.x - 13}" y="${point.y - 13}" width="26" height="26" rx="4" fill="#2563eb" stroke="#ffffff" stroke-width="4"/>
          <text x="${point.x}" y="${point.y + 5}" text-anchor="middle" font-size="13" font-weight="900" fill="#ffffff">H</text>
        </g>
      `
      hotelLegend = `
        <g>
          <rect x="58" y="706" width="17" height="17" rx="3" fill="#2563eb"/>
          <text x="66.5" y="719" text-anchor="middle" font-size="10" font-weight="900" fill="#ffffff">H</text>
          <text font-size="12" font-weight="700" fill="#1d4ed8">${legendText}</text>
        </g>
      `
    }
  }
  const attractionMarkers = layoutAttractionCallouts(points).map(layout => {
    const { point, box, side, lines } = layout
    const color = DAY_COLORS[point.dayIndex % DAY_COLORS.length]
    const labelCenterY = box.y + box.height / 2
    const markerEdgeX = point.x + (side === 'left' ? -18 : 18)
    const elbowX = side === 'left' ? MAP_LEFT - 9 : MAP_RIGHT + 9
    const labelEdgeX = side === 'left' ? box.x + box.width : box.x
    const text = lines.map((line, index) =>
      `<tspan x="${box.x + 10}" y="${box.y + 18 + index * 15}">${escapeXml(line)}</tspan>`
    ).join('')

    return `
      <g>
        <polyline points="${markerEdgeX},${point.y} ${elbowX},${point.y} ${labelEdgeX},${labelCenterY}" fill="none" stroke="${color}" stroke-width="1.4" opacity="0.62"/>
        <rect x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}" rx="4" fill="#ffffff" stroke="${color}" stroke-width="1.5"/>
        <text font-size="12" font-weight="700" fill="#101828">${text}</text>
        <circle cx="${point.x}" cy="${point.y}" r="18" fill="#ffffff" stroke="${color}" stroke-width="7"/>
        <text x="${point.x}" y="${point.y + 5}" text-anchor="middle" font-size="13" font-weight="800" fill="${color}">${point.attractionIndex + 1}</text>
      </g>
    `
  }).join('')
  const dayLegend = props.plan.days.slice(0, 8).map((day, index) => {
    const x = 58 + index * 134
    const color = DAY_COLORS[index % DAY_COLORS.length]
    return `<g><circle cx="${x}" cy="690" r="6" fill="${color}"/><text x="${x + 11}" y="695" font-size="12" fill="#344054">第${index + 1}天 ${escapeXml(day.date.slice(5))}</text></g>`
  }).join('')

  const contextIndex = Object.entries(CATEGORY_STYLE).map(([name, style], columnIndex) => {
    const entries = contextEntries.filter(entry => entry.item.category === name)
    const columnX = 46 + columnIndex * 282
    let rowY = 839
    const rows = entries.map(entry => {
      const lines = wrapText(entry.item.name, 20)
      const currentY = rowY
      const rowHeight = Math.max(24, lines.length * 13 + 7)
      rowY += rowHeight
      const text = lines.map((line, lineIndex) =>
        `<tspan x="${columnX + 29}" y="${currentY + 13 + lineIndex * 13}">${escapeXml(line)}</tspan>`
      ).join('')
      return `
        <g>
          <circle cx="${columnX + 9}" cy="${currentY + 10}" r="8" fill="#ffffff" stroke="${style.color}" stroke-width="2"/>
          <text x="${columnX + 9}" y="${currentY + 13}" text-anchor="middle" font-size="7.5" font-weight="800" fill="${style.color}">${entry.categoryIndex}</text>
          <text font-size="11" font-weight="600" fill="#344054">${text}</text>
        </g>
      `
    }).join('')

    return `
      <g>
        <circle cx="${columnX + 9}" cy="813" r="10" fill="#ffffff" stroke="${style.color}" stroke-width="3"/>
        <text x="${columnX + 9}" y="817" text-anchor="middle" font-size="8.5" font-weight="800" fill="${style.color}">${style.symbol}</text>
        <text x="${columnX + 26}" y="818" font-size="13" font-weight="800" fill="#101828">${name} ${entries.length}</text>
        ${rows || `<text x="${columnX}" y="854" font-size="11" fill="#98a2b3">暂无数据</text>`}
      </g>
    `
  }).join('')
  const desiredScale = Math.max(0.5, projection.spanKm / 5)
  const scaleOptions = [0.5, 1, 2, 5, 10, 20, 50]
  const scaleKm = scaleOptions.find(value => value >= desiredScale) || 50
  const scalePixels = Math.min(180, Math.max(50, projection.scale * scaleKm / 111))

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="${escapeXml(props.plan.city)}高德周边行程地图">
    <rect width="${WIDTH}" height="${HEIGHT}" fill="#ffffff"/>
    <rect x="28" y="90" width="1144" height="566" rx="6" fill="#fbfcfc" stroke="#d0d5dd"/>
    <line x1="298" y1="102" x2="298" y2="644" stroke="#e4e7ec" stroke-dasharray="4 5"/>
    <line x1="902" y1="102" x2="902" y2="644" stroke="#e4e7ec" stroke-dasharray="4 5"/>
    <text x="48" y="45" font-size="28" font-weight="900" fill="#101828">${escapeXml(props.plan.city)}旅行路线与周边场所</text>
    <text x="48" y="73" font-size="14" fill="#475467">${escapeXml(props.plan.start_date)} 至 ${escapeXml(props.plan.end_date)} · ${points.length} 个行程景点 · ${(props.plan.map_context || []).length} 个高德周边场所</text>
    <g transform="translate(1116 31)">
      <path d="M0 30 L11 0 L22 30 L11 24 Z" fill="#101828"/>
      <text x="11" y="48" text-anchor="middle" font-size="12" font-weight="800" fill="#101828">北</text>
    </g>
    ${routeLines}
    ${contextMarkers}
    ${hotelMarker}
    ${attractionMarkers}
    <g transform="translate(510 628)">
      <line x1="0" y1="0" x2="${scalePixels}" y2="0" stroke="#101828" stroke-width="3"/>
      <line x1="0" y1="-5" x2="0" y2="5" stroke="#101828" stroke-width="2"/>
      <line x1="${scalePixels}" y1="-5" x2="${scalePixels}" y2="5" stroke="#101828" stroke-width="2"/>
      <text x="${scalePixels / 2}" y="-9" text-anchor="middle" font-size="11" fill="#344054">约 ${scaleKm} km</text>
    </g>
    ${dayLegend}
    ${hotelLegend}
    <rect x="28" y="748" width="1144" height="333" rx="6" fill="#ffffff" stroke="#d0d5dd"/>
    <text x="48" y="778" font-size="18" font-weight="900" fill="#101828">周边场所索引</text>
    <text x="48" y="798" font-size="11" fill="#667085">图中同色编号对应下方完整名称；圆点为高德原始坐标，引线仅用于错开密集标记。</text>
    <line x1="318" y1="806" x2="318" y2="1065" stroke="#eaecf0"/>
    <line x1="600" y1="806" x2="600" y2="1065" stroke="#eaecf0"/>
    <line x1="882" y1="806" x2="882" y2="1065" stroke="#eaecf0"/>
    ${contextIndex}
    <text x="1142" y="1100" text-anchor="end" font-size="11" fill="#667085">AMAP GCJ-02 · REAL ROUTES &amp; CONTEXT POI</text>
  </svg>`
}

const svgMarkup = computed(buildSvg)
const safeName = computed(() => props.plan.city.replace(/[\\/:*?"<>|]/g, '_'))

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

const downloadSvg = () => {
  downloadBlob(
    new Blob([svgMarkup.value], { type: 'image/svg+xml;charset=utf-8' }),
    `${safeName.value}_行程地图.svg`
  )
}

const downloadPng = async () => {
  const blob = new Blob([svgMarkup.value], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  try {
    const image = new Image()
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error('地图图片生成失败'))
      image.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = WIDTH * 2
    canvas.height = HEIGHT * 2
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器不支持图片导出')
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(value => value ? resolve(value) : reject(new Error('PNG 生成失败')), 'image/png')
    })
    downloadBlob(png, `${safeName.value}_行程地图_高清.png`)
  } finally {
    URL.revokeObjectURL(url)
  }
}
</script>

<style scoped>
.poster-section {
  margin-top: 20px;
  padding: 22px;
  background: #ffffff;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
}

.poster-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 16px;
}

.poster-kicker {
  color: #0f766e;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0;
}

.poster-heading h2 {
  margin: 4px 0;
  color: #101828;
  font-size: 21px;
}

.poster-heading p,
.poster-note {
  margin: 0;
  color: #475467;
  font-size: 13px;
}

.poster-actions,
.modal-actions {
  display: flex;
  gap: 8px;
}

.poster-frame {
  display: block;
  width: 100%;
  padding: 0;
  overflow: hidden;
  background: #ffffff;
  border: 1px solid #cbd5d1;
  border-radius: 4px;
  cursor: zoom-in;
}

.poster-svg :deep(svg),
.poster-modal-svg :deep(svg) {
  display: block;
  width: 100%;
  height: auto;
}

.poster-note {
  margin-top: 10px;
}

.poster-modal-svg {
  overflow: auto;
  border: 1px solid #cbd5d1;
  background: #ffffff;
}

.modal-actions {
  justify-content: flex-end;
  margin-top: 14px;
}

@media (max-width: 720px) {
  .poster-section {
    padding: 14px;
  }

  .poster-heading {
    display: block;
  }

  .poster-actions {
    margin-top: 12px;
  }
}
</style>