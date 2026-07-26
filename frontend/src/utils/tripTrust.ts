/**
 * Trip result page trust helpers.
 * Mirror backend quality disposition rules (trip_plan_quality_service.issue_disposition)
 * so the Result page never guesses status from scores or Chinese copy alone.
 */

/** Codes that are always blocking, matching backend BLOCKING_ISSUE_CODES. */
export const BLOCKING_ISSUE_CODES = new Set([
  'CITY_MISMATCH',
  'SHORT_TRIP_DESTINATION_UNREACHABLE',
  'PLAN_DATE_RANGE_MISMATCH',
  'INVALID_DATE_RANGE',
  'PAST_TRIP_DATE',
  'DAY_COUNT_MISMATCH',
  'DAY_DATE_MISMATCH',
  'EMPTY_DAY',
  'DAY_SCHEDULE_IMPOSSIBLE',
])

export type IssueDisposition = 'blocking' | 'advisory' | 'info'

export type DerivedTrustStatus = 'blocked' | 'needs_review' | 'passed' | 'unknown'

export type GenerationMode = 'primary' | 'repaired' | 'map_fallback'

export interface QualityIssueLike {
  code?: string
  severity?: string
  path?: string
  message?: string
  suggestion?: string
  auto_repaired?: boolean
}

export interface QualityLike {
  status?: string
  score?: number
  publishable?: boolean
  review_required?: boolean
  issues?: QualityIssueLike[] | null
  verified_facts?: number
  generated_at?: string
  checked_items?: string[]
}

export interface DisplayQualityIssue {
  code: string
  severity: string
  disposition: IssueDisposition
  path: string
  message: string
  suggestion: string
  auto_repaired: boolean
}

export interface BudgetLike {
  total_attractions?: number
  total_hotels?: number
  total_meals?: number
  total_transportation?: number
  total?: number
  hotel_nights?: number
  hotel_rooms?: number
  hotel_unit_price?: number
  intercity_transportation?: number
  local_transportation?: number
  transport_unit_price?: number
  budget_source?: string
  hotel_reference?: string | null
  transport_reference?: string | null
  budget_notes?: string[]
}

const cnyFormatter = new Intl.NumberFormat('zh-CN', {
  style: 'currency',
  currency: 'CNY',
  maximumFractionDigits: 0,
})

export function issueDisposition(issue: QualityIssueLike | string): IssueDisposition {
  const code = typeof issue === 'string'
    ? issue
    : String(issue?.code ?? '').trim()
  const severity = typeof issue === 'string'
    ? 'warning'
    : String(issue?.severity ?? 'warning').trim().toLowerCase()

  if (BLOCKING_ISSUE_CODES.has(code) || severity === 'error') {
    return 'blocking'
  }
  if (severity === 'info') {
    return 'info'
  }
  return 'advisory'
}

export function normalizeQualityIssues(
  issues: QualityIssueLike[] | null | undefined,
): DisplayQualityIssue[] {
  if (!Array.isArray(issues)) return []

  const out: DisplayQualityIssue[] = []
  for (const raw of issues) {
    if (!raw || typeof raw !== 'object') continue
    const message = String(raw.message ?? '').trim()
    if (!message) continue
    // Drop internal traces / SQL / stack-looking payloads from display.
    if (looksLikeInternalPayload(message)) continue
    const code = String(raw.code ?? 'UNKNOWN_ISSUE').trim() || 'UNKNOWN_ISSUE'
    const severity = String(raw.severity ?? 'warning').trim().toLowerCase() || 'warning'
    out.push({
      code,
      severity,
      disposition: issueDisposition({ code, severity }),
      path: String(raw.path ?? '').trim(),
      message: sanitizeUserText(message, 500),
      suggestion: sanitizeUserText(String(raw.suggestion ?? '').trim(), 300),
      auto_repaired: Boolean(raw.auto_repaired),
    })
  }
  return out
}

export function groupQualityIssues(issues: DisplayQualityIssue[]): {
  blocking: DisplayQualityIssue[]
  advisory: DisplayQualityIssue[]
  info: DisplayQualityIssue[]
} {
  const blocking: DisplayQualityIssue[] = []
  const advisory: DisplayQualityIssue[] = []
  const info: DisplayQualityIssue[] = []
  for (const issue of issues) {
    if (issue.disposition === 'blocking') blocking.push(issue)
    else if (issue.disposition === 'info') info.push(issue)
    else advisory.push(issue)
  }
  return { blocking, advisory, info }
}

/** Strict bool for quality flags — avoid Boolean("false") === true on corrupt cache. */
export function asStrictBool(value: unknown): boolean {
  return value === true
}

/**
 * Gate-time blocking check on **raw** issues (code/severity only).
 * Must not depend on display filtering (empty message / internal payload drops).
 */
export function hasBlockingIssue(issues: QualityIssueLike[] | null | undefined): boolean {
  if (!Array.isArray(issues)) return false
  return issues.some((issue) => {
    if (!issue || typeof issue !== 'object') return false
    return issueDisposition(issue) === 'blocking'
  })
}

/**
 * Derive trust status from structured quality fields only.
 * Aligns with backend `_plan_is_publishable` + `_derived_quality_status`,
 * with UI labels: blocked | needs_review | passed.
 */
export function deriveTrustStatus(quality: QualityLike | null | undefined): DerivedTrustStatus {
  if (!quality || typeof quality !== 'object') {
    return 'unknown'
  }

  // Use raw issues for gates — display normalization must not weaken blocking.
  const hasBlocking = hasBlockingIssue(quality.issues)
  const status = String(quality.status ?? '').trim().toLowerCase()
  const publishable = asStrictBool(quality.publishable)
  const reviewRequired = asStrictBool(quality.review_required)

  // Backend: publishable + status in {passed,warning,review} + no blocking.
  const statusOk = status === 'passed' || status === 'warning' || status === 'review'
  const isPublishable = publishable && statusOk && !hasBlocking

  if (!isPublishable) {
    return 'blocked'
  }
  if (reviewRequired) {
    return 'needs_review'
  }
  // Only treat as passed when backend says publishable and not review_required.
  // Do not infer from score alone.
  return 'passed'
}

export function isPlanPublishable(quality: QualityLike | null | undefined): boolean {
  const status = deriveTrustStatus(quality)
  return status === 'passed' || status === 'needs_review'
}

/** Server history save / email: blocked must not proceed. needs_review is allowed by backend. */
export function canPersistPlan(quality: QualityLike | null | undefined): boolean {
  return isPlanPublishable(quality)
}

export function canSendPlanEmail(quality: QualityLike | null | undefined): boolean {
  return isPlanPublishable(quality)
}

export function trustStatusLabel(status: DerivedTrustStatus): string {
  switch (status) {
    case 'blocked':
      return '存在阻止使用的问题'
    case 'needs_review':
      return '可使用，但建议核对'
    case 'passed':
      return '检查通过'
    default:
      return '质量状态未知'
  }
}

export function trustStatusDescription(status: DerivedTrustStatus): string {
  switch (status) {
    case 'blocked':
      return '当前方案存在阻止保存与发送的问题。你可以继续查看内容并返回首页重新生成，但不能将本方案当作可出发计划保存或邮件发送。'
    case 'needs_review':
      return '方案可以保存与使用，但仍有待确认事项。操作前请先查看下方提示，并在出发前核对票务、开放时间和天气。'
    case 'passed':
      return '结构化质量检查已通过。票务、开放状态与出发前天气仍以官方信息为准。'
    default:
      return '未获得服务端质量结果。请勿将缓存草稿当作已通过检查的正式方案。'
  }
}

export function trustStatusTone(status: DerivedTrustStatus): 'danger' | 'warning' | 'success' | 'neutral' {
  switch (status) {
    case 'blocked':
      return 'danger'
    case 'needs_review':
      return 'warning'
    case 'passed':
      return 'success'
    default:
      return 'neutral'
  }
}

export function normalizeGenerationMode(raw: unknown): GenerationMode {
  if (raw === 'repaired' || raw === 'map_fallback') return raw
  return 'primary'
}

export function generationModeLabel(mode: GenerationMode | string | undefined | null): string {
  switch (mode) {
    case 'map_fallback':
      return '受限兜底方案'
    case 'repaired':
      return '结构已修复'
    case 'primary':
      return '主规划'
    default:
      return '生成路径未知'
  }
}

export function generationModeHint(mode: GenerationMode | string | undefined | null): string {
  switch (mode) {
    case 'map_fallback':
      return '主规划不可用，已使用地图降级方案；不代表完整、完美行程。'
    case 'repaired':
      return '计划在生成过程中经过结构修复，不代表模型原始输出天然正确。'
    case 'primary':
      return '主规划路径生成。'
    default:
      return ''
  }
}

/** POI coordinate trust — coordinates alone are not "verified destination". */
export function poiCoordinateTrustLabel(coordinateSource: string | undefined | null): {
  label: string
  tone: 'success' | 'warning' | 'neutral'
  verified: boolean
} {
  const source = String(coordinateSource ?? '').trim().toLowerCase()
  if (source === 'amap_poi') {
    return {
      label: '地图服务已确认坐标',
      tone: 'success',
      verified: true,
    }
  }
  if (source) {
    return {
      label: '坐标来源待核对',
      tone: 'warning',
      verified: false,
    }
  }
  return {
    label: '未获得地图确认',
    tone: 'neutral',
    verified: false,
  }
}

export function routeTrustLabel(route: {
  verified?: boolean
  source?: string
  distance?: number
  duration?: number
}): { label: string; showMetrics: boolean } {
  if (route?.verified === true) {
    return { label: '高德路线已校验', showMetrics: true }
  }
  const source = String(route?.source ?? '').trim().toLowerCase()
  if (source.includes('amap') || source.includes('gaode')) {
    // Source hints at map data but not verified — do not present precise metrics as authoritative.
    return { label: '地图路线摘要（建议复核，距离/时间非校验值）', showMetrics: false }
  }
  return { label: '路线摘要（未由地图服务确认）', showMetrics: false }
}

export function isFiniteMoney(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

export function formatMoneyCNY(value: unknown, emptyLabel = '待确认'): string {
  if (!isFiniteMoney(value)) return emptyLabel
  if (value === 0) {
    // Zero may be free or missing — callers can override; default show ¥0 only when explicit 0.
    return cnyFormatter.format(0)
  }
  return cnyFormatter.format(Math.round(value))
}

/**
 * Classify budget_source string for display.
 * Does not recompute totals; only labels trust of server-provided breakdown.
 */
export function budgetSourceTrust(budgetSource: string | undefined | null): {
  label: string
  isEstimate: boolean
  isFallback: boolean
  isProvider: boolean
} {
  const source = String(budgetSource ?? '').trim()
  if (!source) {
    return {
      label: '暂无可靠价格来源',
      isEstimate: true,
      isFallback: true,
      isProvider: false,
    }
  }
  const lower = source.toLowerCase()
  const isProvider = lower.includes('flyai')
  const isFallback =
    source.includes('兜底')
    || lower.includes('heuristic')
    || lower.includes('fallback')
    || source.includes('规则估算')
  const isEstimate = isFallback || source.includes('估算') || !isProvider

  let label = source
  if (isProvider && isFallback) {
    label = `混合来源（含估算）：${source}`
  } else if (isProvider) {
    label = `服务端报价参考：${source}`
  } else if (isFallback) {
    label = `服务端兜底估算：${source}`
  } else {
    label = `服务端估算：${source}`
  }

  return { label, isEstimate, isFallback, isProvider }
}

export function transportUnitPriceHint(): string {
  return '城际交通单人往返参考价（服务端已按人数聚合到城际总额，前端不再乘人数）'
}

export function hotelUnitPriceHint(nights: number, rooms: number): string {
  const n = Number.isFinite(nights) ? nights : 0
  const r = Number.isFinite(rooms) ? rooms : 1
  return `酒店单晚参考价 × ${n} 晚 × ${r} 间（总额见酒店合计，前端不再重算）`
}

/**
 * Only allow http(s) external links for guide sources.
 * Rejects control characters, embedded whitespace, and non-http(s) schemes
 * (including javascript:/data:/vbscript: and mixed-case variants).
 */
export function safeHttpUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  // Control chars (incl. tab/newline) and DEL — cannot be "smuggled" via trim alone.
  if (/[\u0000-\u001F\u007F]/.test(raw)) return null
  const value = raw.trim()
  if (!value) return null
  // Embedded whitespace after trim (e.g. "https://a.com evil") is rejected.
  if (/\s/.test(value)) return null
  // Characters that can break out of HTML attributes or introduce markup.
  if (/["'<>`]/.test(value)) return null
  try {
    const parsed = new URL(value)
    const protocol = String(parsed.protocol || '').toLowerCase()
    if (protocol !== 'http:' && protocol !== 'https:') return null
    const normalized = parsed.toString()
    // Defense in depth after URL normalization.
    if (/["'<>`]/.test(normalized) || /[\u0000-\u001F\u007F]/.test(normalized)) return null
    // Rebuild; browsers normalize scheme case. Do not return the raw input.
    return normalized
  } catch {
    return null
  }
}

/** HTML-escape user/model text before any markup is introduced. */
export function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function decodeBasicEntities(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

/**
 * Restricted inline markdown for guide text:
 * escapeHtml first → bold/code → http(s) links via safeHttpUrl only.
 * Never reintroduces raw HTML tags from model output.
 */
export function renderSafeInlineMarkdown(source: unknown): string {
  const escaped = escapeHtml(source)
  // Bold/code operate only on already-escaped text (no tag injection).
  let out = escaped
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')

  // Consume full markdown link `[text](url)` including the closing `)`.
  // `[^)]*` allows parentheses-free URLs; nested `)` in URL is uncommon and rejected by safeHttpUrl.
  out = out.replace(/\[([^\]]+)\]\(([^)]*)\)/g, (_match, label: string, hrefRaw: string) => {
    // label is already escaped. Decode href entities only for validation, then re-escape.
    const candidate = decodeBasicEntities(String(hrefRaw).trim())
    const safe = safeHttpUrl(candidate)
    if (!safe) {
      // Drop unsafe link; keep escaped label text only (entire markdown construct removed).
      return label
    }
    return `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })
  return out
}

const isGuideHeadingLine = (line: string, lineIndex: number): boolean => {
  if (lineIndex === 0 && line.length <= 24) return true
  if (!/[：:]$/.test(line)) return false
  return line.length <= 18 && !/[。；;，,]/.test(line)
}

/**
 * Restricted block markdown for web_guide display.
 * Pipeline: escape-first inline → limited headings/lists → no raw HTML blocks.
 */
export function renderSafeGuideMarkdown(source: unknown): string {
  const text = String(source ?? '').replace(/\r\n/g, '\n')
  const lines = text.split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let listType: 'ol' | 'ul' | null = null

  const closeParagraph = () => {
    if (!paragraph.length) return
    html.push(`<p>${paragraph.map(renderSafeInlineMarkdown).join('<br>')}</p>`)
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
      html.push(`<h${level}>${renderSafeInlineMarkdown(markdownHeading[2].trim())}</h${level}>`)
      return
    }

    const ordered = line.match(/^\d+[.)]\s+(.+)$/)
    if (ordered) {
      closeParagraph()
      openList('ol')
      html.push(`<li>${renderSafeInlineMarkdown(ordered[1])}</li>`)
      return
    }

    const unordered = line.match(/^[-*]\s+(.+)$/)
    if (unordered) {
      closeParagraph()
      openList('ul')
      html.push(`<li>${renderSafeInlineMarkdown(unordered[1])}</li>`)
      return
    }

    if (isGuideHeadingLine(line, index)) {
      closeParagraph()
      closeList()
      html.push(`<h3>${renderSafeInlineMarkdown(line.replace(/[：:]$/, ''))}</h3>`)
      return
    }

    closeList()
    paragraph.push(line)
  })

  closeParagraph()
  closeList()
  return html.join('')
}

/** Normalize date keys to YYYY-MM-DD when possible (backend often uses date[:10]). */
export function normalizeDateKey(raw: unknown): string {
  const s = String(raw ?? '').trim()
  if (!s) return ''
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
  return s
}

/** Mirror backend unusable weather description checks (lightweight). */
export function isUsableWeatherDescription(dayWeather: unknown, nightWeather: unknown): boolean {
  const day = String(dayWeather ?? '').trim()
  const night = String(nightWeather ?? '').trim()
  const invalid = new Set(['', '未知', '暂无', '暂无预报', 'n/a', 'na', '-', '--'])
  if (invalid.has(day.toLowerCase()) && invalid.has(night.toLowerCase())) return false
  if (invalid.has(day) && invalid.has(night)) return false
  // At least one side must have a non-placeholder description.
  const dayOk = day.length > 0 && !invalid.has(day) && !invalid.has(day.toLowerCase())
  const nightOk = night.length > 0 && !invalid.has(night) && !invalid.has(night.toLowerCase())
  return dayOk || nightOk
}

export function weatherCoverageNote(
  tripDates: string[],
  weatherDates: string[],
): { covered: string[]; missing: string[]; summary: string } {
  const coveredSet = new Set(
    weatherDates.map((d) => normalizeDateKey(d)).filter(Boolean),
  )
  const normalizedTrip = tripDates.map((d) => normalizeDateKey(d)).filter(Boolean)
  const covered = normalizedTrip.filter((d) => coveredSet.has(d))
  const missing = normalizedTrip.filter((d) => !coveredSet.has(d))
  let summary = ''
  if (!normalizedTrip.length) {
    summary = '行程日期未知，无法核对天气覆盖。'
  } else if (!covered.length) {
    summary = '行程日期暂无可用预报，请出发前 3–7 天再查。'
  } else if (missing.length) {
    summary = `已有 ${covered.length} 天预报，另有 ${missing.length} 天暂无预报。`
  } else {
    summary = '行程日期均有预报记录（服务端天气，非实时雷达）。'
  }
  return { covered, missing, summary }
}

function looksLikeInternalPayload(text: string): boolean {
  const t = text.toLowerCase()
  if (t.includes('traceback (most recent call last)')) return true
  if (t.includes('sqlalchemy') || t.includes('psycopg')) return true
  if (/select\s+.+\s+from\s+/i.test(text) && text.length > 80) return true
  if (t.includes('api_key') || t.includes('authorization:')) return true
  return false
}

function sanitizeUserText(text: string, maxLen: number): string {
  const cleaned = text.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').trim()
  if (cleaned.length <= maxLen) return cleaned
  return `${cleaned.slice(0, maxLen - 1)}…`
}
