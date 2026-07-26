/**
 * Contract tests for frontend/src/utils/tripTrust.ts
 *
 * Loads the REAL production TypeScript module by bundling it with the
 * project's existing esbuild (Vite dependency) into a temp ESM file, then
 * importing those exports. No in-test reimplementation of gate/XSS logic.
 *
 * Run: node scripts/test-trip-trust.mjs
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import * as esbuild from 'esbuild'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const utilPath = path.resolve(__dirname, '../src/utils/tripTrust.ts')

assert.ok(fs.existsSync(utilPath), `missing production file: ${utilPath}`)

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'trip-trust-test-'))
const outFile = path.join(tmpDir, 'tripTrust.bundle.mjs')

try {
  await esbuild.build({
    entryPoints: [utilPath],
    outfile: outFile,
    bundle: true,
    format: 'esm',
    platform: 'neutral',
    target: ['es2020'],
    logLevel: 'silent',
    write: true,
  })
} catch (err) {
  fs.rmSync(tmpDir, { recursive: true, force: true })
  console.error('Failed to bundle production tripTrust.ts via esbuild:', err)
  process.exit(2)
}

const mod = await import(pathToFileURL(outFile).href + `?t=${Date.now()}`)

// Cleanup temp bundle after import (module already evaluated in memory).
fs.rmSync(tmpDir, { recursive: true, force: true })

const {
  deriveTrustStatus,
  canPersistPlan,
  canSendPlanEmail,
  asStrictBool,
  hasBlockingIssue,
  issueDisposition,
  normalizeQualityIssues,
  safeHttpUrl,
  escapeHtml,
  renderSafeInlineMarkdown,
  renderSafeGuideMarkdown,
  poiCoordinateTrustLabel,
  routeTrustLabel,
  budgetSourceTrust,
  isFiniteMoney,
  formatMoneyCNY,
  weatherCoverageNote,
  isUsableWeatherDescription,
  normalizeDateKey,
  generationModeLabel,
} = mod

// Prove we imported real exports (not a silent empty module).
assert.equal(typeof deriveTrustStatus, 'function')
assert.equal(typeof safeHttpUrl, 'function')
assert.equal(typeof renderSafeGuideMarkdown, 'function')

let passed = 0
const check = (name, fn) => {
  fn()
  passed += 1
  console.log(`  ok  ${name}`)
}

console.log('tripTrust production-module tests (esbuild → real tripTrust.ts)')
console.log(`  source: ${utilPath}`)

// --- status ---
check('passed when publishable and not review_required', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'passed',
      publishable: true,
      review_required: false,
      issues: [],
    }),
    'passed',
  )
})

check('needs_review when publishable and review_required', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues: [{ code: 'DAY_OVERLOADED', severity: 'warning', message: '紧' }],
    }),
    'needs_review',
  )
})

check('blocked when publishable=false', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'failed',
      publishable: false,
      review_required: true,
      issues: [],
    }),
    'blocked',
  )
})

check('blocked by raw blocking issue even with empty message', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues: [{ code: 'CITY_MISMATCH', severity: 'warning', message: '' }],
    }),
    'blocked',
  )
  assert.equal(
    hasBlockingIssue([{ code: 'CITY_MISMATCH', severity: 'warning', message: '' }]),
    true,
  )
})

check('unknown when quality missing', () => {
  assert.equal(deriveTrustStatus(null), 'unknown')
  assert.equal(deriveTrustStatus(undefined), 'unknown')
})

check('fallback mode does not alone force blocked when publishable', () => {
  // generation_mode is not part of deriveTrustStatus — needs_review still from flags
  assert.equal(
    deriveTrustStatus({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues: [{ code: 'FALLBACK_PLAN', severity: 'warning', message: '降级' }],
    }),
    'needs_review',
  )
  assert.equal(generationModeLabel('map_fallback'), '受限兜底方案')
})

check('repaired label independent of blocked', () => {
  assert.equal(generationModeLabel('repaired'), '结构已修复')
  assert.equal(
    deriveTrustStatus({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues: [{ code: 'MODEL_OUTPUT_REPAIRED', severity: 'warning', message: '已修复' }],
    }),
    'needs_review',
  )
})

check('blocking and advisory coexist → blocked', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'failed',
      publishable: false,
      review_required: true,
      issues: [
        { code: 'EMPTY_DAY', severity: 'error', message: '空天' },
        { code: 'DAY_OVERLOADED', severity: 'warning', message: '偏紧' },
      ],
    }),
    'blocked',
  )
})

check('strict boolean: string true/false not treated as booleans', () => {
  assert.equal(asStrictBool('false'), false)
  assert.equal(asStrictBool('true'), false)
  assert.equal(asStrictBool(true), true)
  assert.equal(
    deriveTrustStatus({
      status: 'passed',
      publishable: 'true',
      review_required: false,
      issues: [],
    }),
    'blocked',
  )
})

check('unknown status string with publishable true still blocked', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'weird',
      publishable: true,
      review_required: false,
      issues: [],
    }),
    'blocked',
  )
})

check('score=100 alone never yields passed without publishable', () => {
  assert.equal(
    deriveTrustStatus({
      status: 'passed',
      score: 100,
      publishable: false,
      review_required: false,
      issues: [],
    }),
    'blocked',
  )
})

// --- persist ---
check('blocked cannot persist or email', () => {
  const q = {
    status: 'failed',
    publishable: false,
    review_required: true,
    issues: [{ code: 'CITY_MISMATCH', severity: 'error', message: 'x' }],
  }
  assert.equal(canPersistPlan(q), false)
  assert.equal(canSendPlanEmail(q), false)
})

check('unknown cannot persist', () => {
  assert.equal(canPersistPlan(null), false)
  assert.equal(canPersistPlan(undefined), false)
})

check('needs_review can persist', () => {
  assert.equal(
    canPersistPlan({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues: [{ code: 'WEATHER_NOT_YET_AVAILABLE', severity: 'warning', message: '天气' }],
    }),
    true,
  )
})

check('passed can persist', () => {
  assert.equal(
    canPersistPlan({
      status: 'passed',
      publishable: true,
      review_required: false,
      issues: [],
    }),
    true,
  )
})

check('display filter does not drop gate for internal-looking blocking message', () => {
  const issues = [
    {
      code: 'EMPTY_DAY',
      severity: 'error',
      message: 'Traceback (most recent call last): app/foo.py line 1',
    },
  ]
  // Display may drop, gate must not.
  assert.equal(hasBlockingIssue(issues), true)
  assert.equal(
    deriveTrustStatus({
      status: 'warning',
      publishable: true,
      review_required: true,
      issues,
    }),
    'blocked',
  )
  const display = normalizeQualityIssues(issues)
  assert.equal(display.length, 0)
})

// --- URL / XSS ---
check('https allowed', () => {
  assert.ok(safeHttpUrl('https://example.com/a'))
})

check('http allowed', () => {
  assert.ok(safeHttpUrl('http://example.com/a'))
})

check('javascript rejected', () => {
  assert.equal(safeHttpUrl('javascript:alert(1)'), null)
  assert.equal(safeHttpUrl('JAVASCRIPT:alert(1)'), null)
})

check('data rejected', () => {
  assert.equal(safeHttpUrl('data:text/html,hi'), null)
  assert.equal(safeHttpUrl('DATA:text/html,hi'), null)
})

check('vbscript/file/blob rejected', () => {
  assert.equal(safeHttpUrl('vbscript:msgbox(1)'), null)
  assert.equal(safeHttpUrl('file:///etc/passwd'), null)
  assert.equal(safeHttpUrl('blob:https://example.com/uuid'), null)
})

check('control characters and leading whitespace tricks rejected', () => {
  assert.equal(safeHttpUrl('\njavascript:alert(1)'), null)
  assert.equal(safeHttpUrl('  javascript:alert(1)'), null)
  assert.equal(safeHttpUrl('https://example.com/a\nb'), null)
})

check('attribute-break URL rejected or neutralized in markdown', () => {
  const evil = 'https://example.com/" onmouseover="alert(1)'
  assert.equal(safeHttpUrl(evil), null)
  const rendered = renderSafeInlineMarkdown(`[x](${evil})`)
  // Must not create an anchor with event-handler attributes.
  assert.ok(!/<a\b/i.test(rendered) || !/\son\w+\s*=/i.test(rendered))
  assert.ok(!/<a[^>]*\sonmouseover/i.test(rendered))
  // Plain text may still contain the word onmouseover after escaping — that is safe.
  assert.ok(!rendered.includes('<script'))
})

check('script and img tags escaped in guide markdown', () => {
  const html = renderSafeGuideMarkdown('<script>alert(1)</script>\n<img src=x onerror=alert(1)>')
  assert.ok(!html.includes('<script>'))
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('&lt;script&gt;') || html.includes('&lt;script'))
  assert.ok(html.includes('&lt;img') || html.includes('onerror'))
})

check('markdown javascript and data links stripped', () => {
  // Prefer paren-free scheme targets so the full markdown link is consumed.
  const a = renderSafeInlineMarkdown('[t](javascript:alert%281%29)')
  const b = renderSafeInlineMarkdown('[t](data:text/html,hi)')
  const c = renderSafeInlineMarkdown('[t](javascript:alert(1))')
  assert.ok(!a.includes('href='))
  assert.ok(!b.includes('href='))
  assert.ok(!c.includes('href='))
  assert.ok(!a.includes('javascript:'))
  assert.ok(!c.includes('javascript:'))
  assert.equal(a, 't')
  assert.equal(b, 't')
  // Nested `)` inside URL may leave a trailing `)` as plain text — still no href.
  assert.ok(c === 't' || c === 't)')
})

check('markdown https link safe attributes', () => {
  const a = renderSafeInlineMarkdown('[文档](https://example.com/path)')
  assert.match(a, /<a href="https:\/\/example\.com\/path" target="_blank" rel="noopener noreferrer">/)
  assert.ok(a.includes('文档'))
})

check('escapeHtml encodes brackets', () => {
  assert.equal(escapeHtml('<b>"x"</b>'), '&lt;b&gt;&quot;x&quot;&lt;/b&gt;')
})

// --- display helpers ---
check('no quality → not passed', () => {
  assert.notEqual(deriveTrustStatus(null), 'passed')
})

check('coords without amap_poi not verified', () => {
  assert.equal(poiCoordinateTrustLabel('').verified, false)
  assert.equal(poiCoordinateTrustLabel('model').verified, false)
  assert.equal(poiCoordinateTrustLabel('amap_poi').verified, true)
})

check('guide labels are not POI verification (coordinate only)', () => {
  // POI helper only looks at coordinate_source; guide never calls it as verified.
  assert.equal(poiCoordinateTrustLabel('web_guide').verified, false)
})

check('missing weather dates reported', () => {
  const note = weatherCoverageNote(['2026-08-01', '2026-08-02'], ['2026-08-01'])
  assert.deepEqual(note.missing, ['2026-08-02'])
  assert.ok(note.summary.includes('暂无') || note.summary.includes('1'))
})

check('empty weather description unusable', () => {
  assert.equal(isUsableWeatherDescription('', ''), false)
  assert.equal(isUsableWeatherDescription('未知', '暂无'), false)
  assert.equal(isUsableWeatherDescription('晴', '多云'), true)
})

check('heuristic budget is estimate/fallback', () => {
  const t = budgetSourceTrust('酒店兜底估算 + 城际交通兜底估算 + 市内交通规则估算')
  assert.equal(t.isFallback, true)
  assert.equal(t.isProvider, false)
  assert.ok(t.label.includes('估算') || t.label.includes('兜底'))
})

check('money NaN Infinity negative rejected; no traveler multiply in helpers', () => {
  assert.equal(isFiniteMoney(Number.NaN), false)
  assert.equal(isFiniteMoney(Number.POSITIVE_INFINITY), false)
  assert.equal(isFiniteMoney(-3), false)
  assert.equal(formatMoneyCNY(null), '待确认')
  // formatMoneyCNY does not accept travelers — contract is display-only
  assert.match(formatMoneyCNY(1200), /1,200|¥|￥/)
})

check('unverified route hides precise metrics', () => {
  assert.equal(routeTrustLabel({ verified: false, distance: 1000, duration: 600 }).showMetrics, false)
  assert.equal(routeTrustLabel({ verified: true, distance: 1000, duration: 600 }).showMetrics, true)
})

check('unknown issue code still displayable', () => {
  const list = normalizeQualityIssues([
    { code: 'TOTALLY_NEW_CODE', severity: 'warning', message: '新问题' },
  ])
  assert.equal(list.length, 1)
  assert.equal(list[0].code, 'TOTALLY_NEW_CODE')
  assert.equal(list[0].disposition, 'advisory')
})

check('SQL/traceback messages filtered from display only', () => {
  const list = normalizeQualityIssues([
    {
      code: 'X',
      severity: 'warning',
      message: 'SELECT * FROM users WHERE password IS NOT NULL AND length > 80 chars padding padding padding',
    },
  ])
  assert.equal(list.length, 0)
})

check('issueDisposition codes match blocking set', () => {
  assert.equal(issueDisposition({ code: 'DAY_SCHEDULE_IMPOSSIBLE', severity: 'info' }), 'blocking')
  assert.equal(issueDisposition({ code: 'OTHER', severity: 'info' }), 'info')
})

check('normalizeDateKey strips time suffix', () => {
  assert.equal(normalizeDateKey('2026-08-01T12:00:00'), '2026-08-01')
})

console.log(`\n${passed} checks passed (production module)`)
console.log('Mutation check: deriveTrustStatus and safeHttpUrl are the bundled tripTrust.ts exports.')
