export type UserRole = 'guest' | 'user' | 'manager' | 'admin'

export interface LocalUser {
  user_id: string
  username: string
  role: UserRole
}

type StoredUser = LocalUser & {
  password: string
}

const USERS_KEY = 'lingtu_users'
const CURRENT_USER_KEY = 'lingtu_current_user'
const MANAGER_INVITE_CODE = import.meta.env.VITE_MANAGER_INVITE_CODE || 'LINGTU_MANAGER_2026'
const ADMIN_INVITE_CODE = import.meta.env.VITE_ADMIN_INVITE_CODE || 'LINGTU_ADMIN_2026'

const DEFAULT_USERS: StoredUser[] = [
  { user_id: 'u_0001', username: 'user', password: '123456', role: 'user' },
  { user_id: 'u_0100', username: 'manager', password: '123456', role: 'manager' },
  { user_id: 'u_0002', username: 'admin', password: '123456', role: 'admin' }
]

const readUsers = (): StoredUser[] => {
  try {
    const stored = JSON.parse(localStorage.getItem(USERS_KEY) || '[]') as StoredUser[]
    const merged = [...DEFAULT_USERS]
    let changed = stored.length < DEFAULT_USERS.length
    stored.forEach(user => {
      if (!merged.some(item => item.username === user.username)) {
        merged.push(user)
      } else if (!DEFAULT_USERS.some(item => item.username === user.username)) {
        changed = true
      }
    })
    if (changed) {
      writeUsers(merged)
    }
    return merged
  } catch {
    writeUsers(DEFAULT_USERS)
    return DEFAULT_USERS
  }
}

const writeUsers = (users: StoredUser[]) => {
  localStorage.setItem(USERS_KEY, JSON.stringify(users))
}

const toPublicUser = (user: StoredUser): LocalUser => ({
  user_id: user.user_id,
  username: user.username,
  role: user.role
})

export const getCurrentUser = (): LocalUser | null => {
  try {
    const current = JSON.parse(localStorage.getItem(CURRENT_USER_KEY) || 'null') as LocalUser | null
    const defaultUser = DEFAULT_USERS.find(user => user.username === current?.username)
    if (current && defaultUser && current.user_id !== defaultUser.user_id) {
      const synced = toPublicUser(defaultUser)
      localStorage.setItem(CURRENT_USER_KEY, JSON.stringify(synced))
      return synced
    }
    return current
  } catch {
    return null
  }
}

export const setCurrentUser = (user: LocalUser | null) => {
  if (!user) {
    localStorage.removeItem(CURRENT_USER_KEY)
    window.dispatchEvent(new Event('lingtu-auth-change'))
    return
  }
  localStorage.setItem(CURRENT_USER_KEY, JSON.stringify(user))
  window.dispatchEvent(new Event('lingtu-auth-change'))
}

export const login = (username: string, password: string): LocalUser => {
  const name = username.trim()
  const user = readUsers().find(item => item.username === name && item.password === password)
  if (!user) {
    throw new Error('用户名或密码不正确')
  }
  const publicUser = toPublicUser(user)
  setCurrentUser(publicUser)
  return publicUser
}

const validateInviteCode = (role: UserRole, inviteCode: string) => {
  if (role === 'manager' && inviteCode.trim() !== MANAGER_INVITE_CODE) {
    throw new Error('经理账号需要有效授权码')
  }
  if (role === 'admin' && inviteCode.trim() !== ADMIN_INVITE_CODE) {
    throw new Error('管理员账号需要有效授权码')
  }
}

export const register = (
  username: string,
  password: string,
  role: UserRole = 'user',
  inviteCode: string = ''
): LocalUser => {
  const name = username.trim()
  const normalizedRole: UserRole = role === 'admin' || role === 'manager' ? role : 'user'
  if (name.length < 2) {
    throw new Error('用户名至少需要 2 个字符')
  }
  if (password.length < 4) {
    throw new Error('密码至少需要 4 个字符')
  }
  const users = readUsers()
  if (users.some(item => item.username === name)) {
    throw new Error('用户名已存在')
  }
  validateInviteCode(normalizedRole, inviteCode)
  const user: StoredUser = {
    user_id: `u_${Date.now().toString().slice(-6)}`,
    username: name,
    password,
    role: normalizedRole
  }
  users.push(user)
  writeUsers(users)
  const publicUser = toPublicUser(user)
  setCurrentUser(publicUser)
  return publicUser
}

export const logout = () => {
  setCurrentUser(null)
}
