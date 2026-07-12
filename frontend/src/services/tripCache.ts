import type { TripPlan } from '@/types'

export type CacheRetentionMinutes = 0 | 5 | 10 | 60

type TripCachePayload = {
  plan: TripPlan
  planNo?: string | null
  savedAt: number
  expiresAt: number | null
}

const CACHE_KEY = 'lingtu:last-trip'
const RETENTION_KEY = 'lingtu:trip-retention'
const VALID_RETENTIONS: CacheRetentionMinutes[] = [0, 5, 10, 60]

export function getCacheRetention(): CacheRetentionMinutes {
  const value = Number(localStorage.getItem(RETENTION_KEY) ?? 10)
  return VALID_RETENTIONS.includes(value as CacheRetentionMinutes)
    ? value as CacheRetentionMinutes
    : 10
}

export function setCacheRetention(value: CacheRetentionMinutes): void {
  localStorage.setItem(RETENTION_KEY, String(value))
}

export function saveTripCache(
  plan: TripPlan,
  planNo?: string | null,
  retention: CacheRetentionMinutes = getCacheRetention()
): void {
  const now = Date.now()
  const payload: TripCachePayload = {
    plan,
    planNo,
    savedAt: now,
    expiresAt: retention === 0 ? null : now + retention * 60_000
  }
  localStorage.setItem(CACHE_KEY, JSON.stringify(payload))
}

export function loadTripCache(): TripCachePayload | null {
  const raw = localStorage.getItem(CACHE_KEY)
  if (!raw) return null
  try {
    const payload = JSON.parse(raw) as TripCachePayload
    if (!payload?.plan || (payload.expiresAt !== null && payload.expiresAt <= Date.now())) {
      localStorage.removeItem(CACHE_KEY)
      return null
    }
    return payload
  } catch {
    localStorage.removeItem(CACHE_KEY)
    return null
  }
}

export function clearTripCache(): void {
  localStorage.removeItem(CACHE_KEY)
}