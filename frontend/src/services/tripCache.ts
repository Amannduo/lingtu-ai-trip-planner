import type { TripPlan } from '@/types'

export type CacheRetentionMinutes = 0 | 5 | 10 | 60

type TripCachePayload = {
  plan: TripPlan
  planNo?: string | null
  savedAt: number
  expiresAt: number | null
}

const CACHE_KEY = 'lingtu:last-trip'
const SESSION_KEY = 'tripPlan'
const RETENTION_KEY = 'lingtu:trip-retention'
const VALID_RETENTIONS: CacheRetentionMinutes[] = [0, 5, 10, 60]
let memoryCache: TripCachePayload | null = null

const isCacheValid = (payload: TripCachePayload | null): payload is TripCachePayload => (
  Boolean(payload?.plan)
  && (payload?.expiresAt === null || Number(payload?.expiresAt) > Date.now())
)

export function getCacheRetention(): CacheRetentionMinutes {
  try {
    const value = Number(localStorage.getItem(RETENTION_KEY) ?? 10)
    return VALID_RETENTIONS.includes(value as CacheRetentionMinutes)
      ? value as CacheRetentionMinutes
      : 10
  } catch {
    return 10
  }
}

export function setCacheRetention(value: CacheRetentionMinutes): void {
  try {
    localStorage.setItem(RETENTION_KEY, String(value))
  } catch (error) {
    console.warn('[trip-cache] retention preference could not be persisted', error)
  }
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
  memoryCache = payload
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload))
  } catch (error) {
    console.warn('[trip-cache] local draft persistence unavailable; using memory cache', error)
  }
}

export function loadTripCache(): TripCachePayload | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw) {
      const payload = JSON.parse(raw) as TripCachePayload
      if (isCacheValid(payload)) {
        memoryCache = payload
        return payload
      }
      localStorage.removeItem(CACHE_KEY)
    }
  } catch (error) {
    console.warn('[trip-cache] local draft could not be read; trying memory cache', error)
  }

  if (isCacheValid(memoryCache)) return memoryCache
  memoryCache = null
  return null
}

export function saveTripSession(plan: TripPlan): void {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(plan))
  } catch (error) {
    console.warn('[trip-cache] session draft persistence unavailable', error)
  }
}

export function loadTripSession(): TripPlan | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return raw ? JSON.parse(raw) as TripPlan : null
  } catch (error) {
    console.warn('[trip-cache] session draft could not be read', error)
    return null
  }
}

export function clearTripCache(): void {
  memoryCache = null
  try {
    localStorage.removeItem(CACHE_KEY)
    sessionStorage.removeItem(SESSION_KEY)
  } catch (error) {
    console.warn('[trip-cache] browser draft could not be cleared', error)
  }
}
