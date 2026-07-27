/** Restricted chart payload from the Agent API — not an ECharts option. */

export type AgentChartKind = 'bar' | 'line' | 'pie'

export interface AgentChartSeries {
  name: string
  values: number[]
}

export interface AgentChartPayload {
  kind: AgentChartKind
  title: string
  x_label: string
  y_label: string
  categories: string[]
  series: AgentChartSeries[]
  truncated: boolean
  note: string
}

const ALLOWED_KINDS = new Set<AgentChartKind>(['bar', 'line', 'pie'])
const FORBIDDEN_KEYS = new Set([
  'formatter',
  'renderItem',
  'encode',
  'markLine',
  'tooltip',
  'graphic',
  'dataset',
  'rich',
  'xAxis',
  'yAxis',
  'grid',
  '__proto__',
  'prototype',
  'constructor'
])

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && !Number.isNaN(value)
}

function cleanText(value: unknown, maxLen: number): string {
  let text = String(value ?? '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .trim()
  // Neutralize markup so ECharts canvas labels stay pure text.
  text = text.replace(/</g, '＜').replace(/>/g, '＞')
  if (text.length > maxLen) text = `${text.slice(0, maxLen - 1)}…`
  return text
}

/**
 * Validate and normalize a chart payload; return null when unusable.
 * Input is treated as unknown — never trust raw library options.
 * Returns a fresh object with only allowlisted fields.
 */
export function normalizeAgentChart(raw: unknown): AgentChartPayload | null {
  if (raw == null) return null
  if (typeof raw !== 'object' || Array.isArray(raw)) return null
  const data = raw as Record<string, unknown>

  // Legacy full ECharts options or dangerous keys → ignore chart, keep chat.
  for (const key of Object.keys(data)) {
    if (FORBIDDEN_KEYS.has(key)) return null
  }

  const kind = data.kind
  if (kind !== 'bar' && kind !== 'line' && kind !== 'pie') return null
  if (!ALLOWED_KINDS.has(kind)) return null

  if (!Array.isArray(data.categories) || !Array.isArray(data.series)) return null

  const categories = data.categories
    .map((item) => cleanText(item, 100))
    .filter((item) => item.length > 0)
    .slice(0, 50)

  const series: AgentChartSeries[] = []
  for (const item of data.series.slice(0, 8)) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return null
    const row = item as Record<string, unknown>
    for (const key of Object.keys(row)) {
      if (FORBIDDEN_KEYS.has(key)) return null
    }
    if (!Array.isArray(row.values)) return null
    // Reject bool and non-finite; do not coerce numeric strings.
    const numbers: number[] = []
    for (const value of row.values) {
      if (typeof value === 'boolean') return null
      if (!isFiniteNumber(value)) return null
      numbers.push(value)
    }
    if (kind === 'pie' && numbers.some((value) => value < 0)) return null
    if ((kind === 'bar' || kind === 'line') && numbers.length !== categories.length) {
      return null
    }
    series.push({
      name: cleanText(row.name ?? '系列', 64) || '系列',
      values: numbers
    })
  }

  if (!categories.length || !series.length) return null

  if (kind === 'pie') {
    const values = series[0].values
    if (values.length !== categories.length) return null
    if (!values.length || values.every((value) => value === 0)) return null
    const size = Math.min(values.length, 20, categories.length)
    const truncated = Boolean(data.truncated) || values.length > 20 || categories.length > 20
    return {
      kind,
      title: cleanText(data.title ?? '', 120),
      x_label: cleanText(data.x_label ?? '', 64),
      y_label: cleanText(data.y_label ?? '', 64),
      categories: categories.slice(0, size),
      series: [{ name: series[0].name, values: values.slice(0, size) }],
      truncated,
      note: cleanText(
        data.note ?? (truncated ? '数据点较多，图表仅展示部分结果。' : ''),
        200
      )
    }
  }

  // Point budget: categories * series <= 400
  let cats = categories
  let ser = series
  let truncated = Boolean(data.truncated)
  if (cats.length * ser.length > 400) {
    const maxCats = Math.max(1, Math.floor(400 / Math.max(1, ser.length)))
    cats = cats.slice(0, maxCats)
    ser = ser.map((item) => ({
      name: item.name,
      values: item.values.slice(0, maxCats)
    }))
    truncated = true
  }

  return {
    kind,
    title: cleanText(data.title ?? '', 120),
    x_label: cleanText(data.x_label ?? '', 64),
    y_label: cleanText(data.y_label ?? '', 64),
    categories: cats,
    series: ser,
    truncated,
    note: cleanText(
      data.note ?? (truncated ? '数据点较多，图表仅展示部分结果。' : ''),
      200
    )
  }
}
