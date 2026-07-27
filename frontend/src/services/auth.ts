import axios from 'axios'
import { clearTripCache, setTripCacheOwner } from './tripCache'

export type UserRole = 'user' | 'manager' | 'admin'

export interface LocalUser {
  user_id: string
  username: string
  email: string | null
  role: UserRole
  is_active: boolean
}

type AuthResponse = {
  success: boolean
  user: LocalUser
}

type LogoutResponse = {
  success: boolean
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
const CURRENT_USER_KEY = 'lingtu_current_user'

const authClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

const parseUser = (value: unknown): LocalUser | null => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const candidate = value as Partial<LocalUser>
  if (
    typeof candidate.user_id !== 'string'
    || !candidate.user_id.trim()
    || typeof candidate.username !== 'string'
    || !candidate.username.trim()
    || (candidate.email !== null && typeof candidate.email !== 'string')
    || !['user', 'manager', 'admin'].includes(candidate.role as UserRole)
    || typeof candidate.is_active !== 'boolean'
  ) {
    return null
  }
  return {
    user_id: candidate.user_id.trim(),
    username: candidate.username,
    email: candidate.email,
    role: candidate.role as UserRole,
    is_active: candidate.is_active
  }
}

const readCachedUser = (): LocalUser | null => {
  try {
    return parseUser(JSON.parse(localStorage.getItem(CURRENT_USER_KEY) || 'null'))
  } catch {
    return null
  }
}

const requireResponseUser = (value: unknown): LocalUser => {
  const user = parseUser(value)
  if (!user) throw new Error('\u670d\u52a1\u7aef\u8fd4\u56de\u7684\u7528\u6237\u6570\u636e\u65e0\u6548')
  return user
}

let currentUser: LocalUser | null = readCachedUser()
let authStateRevision = 0
setTripCacheOwner(currentUser?.user_id ?? null)

const notifyAuthChange = () => {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('lingtu-auth-change'))
  }
}

type SetCurrentUserOptions = {
  persist?: boolean
}

const setCurrentUser = (
  user: LocalUser | null,
  options: SetCurrentUserOptions = {}
) => {
  const previousUserId = currentUser?.user_id ?? null
  const nextUserId = user?.user_id ?? null
  if (previousUserId !== nextUserId) clearTripCache()

  currentUser = user
  setTripCacheOwner(nextUserId)
  authStateRevision += 1

  if (options.persist !== false) {
    try {
      if (user) {
        localStorage.setItem(CURRENT_USER_KEY, JSON.stringify(user))
      } else {
        localStorage.removeItem(CURRENT_USER_KEY)
      }
    } catch (error) {
      console.warn('[auth] current user could not be persisted', error)
    }
  }
  notifyAuthChange()
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key !== CURRENT_USER_KEY && event.key !== null) return
    if (event.storageArea && event.storageArea !== localStorage) return

    let nextUser: LocalUser | null = null
    if (event.key !== null && event.newValue) {
      try {
        nextUser = parseUser(JSON.parse(event.newValue))
      } catch {
        nextUser = null
      }
    }
    setCurrentUser(nextUser, { persist: false })
  })
}

const authError = (error: any, fallback: string): string => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return typeof error?.message === 'string' && error.message.trim() ? error.message : fallback
}

const isUnauthenticatedResponse = (error: any): boolean => (
  error?.response?.status === 401 || error?.response?.status === 403
)

export const getCurrentUser = (): LocalUser | null => currentUser

export const restoreSession = async (): Promise<LocalUser | null> => {
  const revisionAtStart = authStateRevision
  try {
    const response = await authClient.get<AuthResponse>('/api/auth/me')
    const user = requireResponseUser(response.data.user)
    if (revisionAtStart !== authStateRevision) return currentUser
    setCurrentUser(user)
    return user
  } catch {
    if (revisionAtStart !== authStateRevision) return currentUser
    setCurrentUser(null)
    return null
  }
}

export const login = async (username: string, password: string): Promise<LocalUser> => {
  try {
    const response = await authClient.post<AuthResponse>('/api/auth/login', {
      username: username.trim(),
      password
    })
    const user = requireResponseUser(response.data.user)
    setCurrentUser(user)
    return user
  } catch (error: any) {
    throw new Error(authError(error, '\u767b\u5f55\u5931\u8d25'))
  }
}

export const register = async (
  username: string,
  password: string,
  email: string = '',
  role: UserRole = 'user',
  inviteCode: string = ''
): Promise<LocalUser> => {
  try {
    const response = await authClient.post<AuthResponse>('/api/auth/register', {
      username: username.trim(),
      password,
      email: email.trim() || null,
      role,
      invite_code: inviteCode.trim()
    })
    const user = requireResponseUser(response.data.user)
    setCurrentUser(user)
    return user
  } catch (error: any) {
    throw new Error(authError(error, '\u6ce8\u518c\u5931\u8d25'))
  }
}

export const updateEmail = async (email: string): Promise<LocalUser> => {
  try {
    const response = await authClient.patch<AuthResponse>('/api/auth/me', {
      email: email.trim() || null
    })
    const user = requireResponseUser(response.data.user)
    setCurrentUser(user)
    return user
  } catch (error: any) {
    throw new Error(authError(error, '\u90ae\u7bb1\u4fdd\u5b58\u5931\u8d25'))
  }
}

export class LogoutNotConfirmedError extends Error {
  readonly code = 'LOGOUT_NOT_CONFIRMED' as const

  constructor(message: string) {
    super(message)
    this.name = 'LogoutNotConfirmedError'
  }
}

const confirmSessionEnded = async (): Promise<boolean> => {
  const revisionAtStart = authStateRevision
  try {
    const response = await authClient.get<AuthResponse>('/api/auth/me')
    const user = requireResponseUser(response.data.user)
    if (revisionAtStart !== authStateRevision) return false
    setCurrentUser(user)
    return false
  } catch (error: any) {
    if (revisionAtStart !== authStateRevision) return false
    if (isUnauthenticatedResponse(error)) {
      setCurrentUser(null)
      return true
    }
    return false
  }
}

export const logout = async (): Promise<void> => {
  const expectedUserId = currentUser?.user_id ?? null
  try {
    const response = await authClient.post<LogoutResponse>('/api/auth/logout')
    if (response.data?.success !== true) {
      throw new Error('\u670d\u52a1\u7aef\u672a\u8fd4\u56de\u9000\u51fa\u6210\u529f\u786e\u8ba4')
    }
    const activeUserId = currentUser?.user_id ?? null
    if (activeUserId !== null && activeUserId !== expectedUserId) {
      throw new Error('\u9000\u51fa\u671f\u95f4\u8d26\u53f7\u72b6\u6001\u5df2\u53d1\u751f\u53d8\u5316')
    }
    setCurrentUser(null)
  } catch (error: any) {
    // The server may have completed logout even when its response was lost.
    if (await confirmSessionEnded()) return
    const detail = authError(error, '\u7f51\u7edc\u5f02\u5e38')
    throw new LogoutNotConfirmedError(
      `${detail}\uff1b\u670d\u52a1\u7aef\u672a\u786e\u8ba4\u9000\u51fa\uff0c\u5f53\u524d\u767b\u5f55\u72b6\u6001\u5df2\u4fdd\u7559\uff0c\u8bf7\u91cd\u8bd5\u3002`
    )
  }
}
