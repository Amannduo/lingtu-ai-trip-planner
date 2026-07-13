import axios from 'axios'

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

const readCachedUser = (): LocalUser | null => {
  try {
    const value = JSON.parse(localStorage.getItem(CURRENT_USER_KEY) || 'null')
    if (
      value
      && typeof value.user_id === 'string'
      && typeof value.username === 'string'
      && ['user', 'manager', 'admin'].includes(value.role)
    ) {
      return value as LocalUser
    }
  } catch {
    // Invalid browser cache is treated as logged out.
  }
  return null
}

let currentUser: LocalUser | null = readCachedUser()

const notifyAuthChange = () => {
  window.dispatchEvent(new Event('lingtu-auth-change'))
}

const setCurrentUser = (user: LocalUser | null) => {
  currentUser = user
  if (user) {
    localStorage.setItem(CURRENT_USER_KEY, JSON.stringify(user))
  } else {
    localStorage.removeItem(CURRENT_USER_KEY)
  }
  notifyAuthChange()
}

const authError = (error: any, fallback: string) =>
  error?.response?.data?.detail || error?.message || fallback

export const getCurrentUser = (): LocalUser | null => currentUser

export const restoreSession = async (): Promise<LocalUser | null> => {
  try {
    const response = await authClient.get<AuthResponse>('/api/auth/me')
    setCurrentUser(response.data.user)
    return response.data.user
  } catch {
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
    setCurrentUser(response.data.user)
    return response.data.user
  } catch (error: any) {
    throw new Error(authError(error, '登录失败'))
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
    setCurrentUser(response.data.user)
    return response.data.user
  } catch (error: any) {
    throw new Error(authError(error, '注册失败'))
  }
}

export const updateEmail = async (email: string): Promise<LocalUser> => {
  try {
    const response = await authClient.patch<AuthResponse>('/api/auth/me', {
      email: email.trim() || null
    })
    setCurrentUser(response.data.user)
    return response.data.user
  } catch (error: any) {
    throw new Error(authError(error, '邮箱保存失败'))
  }
}

export const logout = async (): Promise<void> => {
  try {
    await authClient.post('/api/auth/logout')
  } finally {
    setCurrentUser(null)
  }
}