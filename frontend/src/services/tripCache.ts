import type { TripPlan } from '@/types'

export type CacheRetentionMinutes = 0 | 5 | 10 | 60

const CACHE_SCHEMA_VERSION = 2 as const

type TripCachePayload = {
  schemaVersion: typeof CACHE_SCHEMA_VERSION
  ownerUserId: string | null
  plan: TripPlan
  planNo?: string | null
  savedAt: number
  expiresAt: number | null
}

type TripSessionPayload = {
  schemaVersion: typeof CACHE_SCHEMA_VERSION
  ownerUserId: string | null
  plan: TripPlan
}

const CACHE_KEY = 'lingtu:last-trip'
const SESSION_KEY = 'tripPlan'
const RETENTION_KEY = 'lingtu:trip-retention'
const VALID_RETENTIONS: CacheRetentionMinutes[] = [0, 5, 10, 60]

let activeOwnerUserId: string | null = null
let memoryCache: TripCachePayload | null = null

const normalizeOwnerUserId = (userId: string | null): string | null => {
  if (typeof userId !== 'string') return null
  const normalized = userId.trim()
  return normalized || null
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)
)

const hasValidOwner = (payload: Record<string, unknown>): boolean => (
  Object.prototype.hasOwnProperty.call(payload, 'ownerUserId')
  && (payload.ownerUserId === null || (
    typeof payload.ownerUserId === 'string' && payload.ownerUserId.length > 0
  ))
  && payload.ownerUserId === activeOwnerUserId
)

const isCacheValid = (value: unknown): value is TripCachePayload => {
  if (!isRecord(value) || value.schemaVersion !== CACHE_SCHEMA_VERSION) return false
  if (!hasValidOwner(value) || !isRecord(value.plan)) return false
  if (typeof value.savedAt !== 'number' || !Number.isFinite(value.savedAt)) return false
  if (value.planNo !== undefined && value.planNo !== null && typeof value.planNo !== 'string') {
    return false
  }
  return value.expiresAt === null
    || (
      typeof value.expiresAt === 'number'
      && Number.isFinite(value.expiresAt)
      && value.expiresAt > Date.now()
    )
}

const isSessionValid = (value: unknown): value is TripSessionPayload => (
  isRecord(value)
  && value.schemaVersion === CACHE_SCHEMA_VERSION
  && hasValidOwner(value)
  && isRecord(value.plan)
)

const removeLocalCache = (): void => {
  try {
    localStorage.removeItem(CACHE_KEY)
  } catch (error) {
    console.warn('[trip-cache] local draft could not be cleared', error)
  }
}

const removeSessionCache = (): void => {
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch (error) {
    console.warn('[trip-cache] session draft could not be cleared', error)
  }
}

/** Bind subsequent cache reads and writes to one account or to anonymous mode. */
export function setTripCacheOwner(userId: string | null): void {
  activeOwnerUserId = normalizeOwnerUserId(userId)
}

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
    schemaVersion: CACHE_SCHEMA_VERSION,
    ownerUserId: activeOwnerUserId,
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
  let raw: string | null = null
  try {
    raw = localStorage.getItem(CACHE_KEY)
  } catch (error) {
    console.warn('[trip-cache] local draft could not be read; trying memory cache', error)
  }

  if (raw) {
    try {
      const payload: unknown = JSON.parse(raw)
      if (isCacheValid(payload)) {
        memoryCache = payload
        return payload
      }
    } catch (error) {
      console.warn('[trip-cache] local draft is malformed and was discarded', error)
    }
    memoryCache = null
    removeLocalCache()
    return null
  }

  if (isCacheValid(memoryCache)) return memoryCache
  memoryCache = null
  return null
}

export function saveTripSession(plan: TripPlan): void {
  const payload: TripSessionPayload = {
    schemaVersion: CACHE_SCHEMA_VERSION,
    ownerUserId: activeOwnerUserId,
    plan
  }
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(payload))
  } catch (error) {
    console.warn('[trip-cache] session draft persistence unavailable', error)
  }
}

export function loadTripSession(): TripPlan | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const payload: unknown = JSON.parse(raw)
    if (isSessionValid(payload)) return payload.plan
    removeSessionCache()
    return null
  } catch (error) {
    console.warn('[trip-cache] session draft could not be read', error)
    removeSessionCache()
    return null
  }
}

export function clearTripCache(): void {
  memoryCache = null
  // One unavailable storage API must not prevent the other cache tier clearing.
  removeLocalCache()
  removeSessionCache()
}
