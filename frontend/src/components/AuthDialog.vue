<template>
  <a-modal
    :open="open"
    :title="mode === 'login' ? '登录灵途' : '注册账号'"
    :footer="null"
    width="420px"
    @cancel="$emit('close')"
  >
    <div class="auth-tabs">
      <button :class="{ active: mode === 'login' }" type="button" @click="mode = 'login'">登录</button>
      <button :class="{ active: mode === 'register' }" type="button" @click="mode = 'register'">注册</button>
    </div>

    <a-form layout="vertical" @finish="submit">
      <a-alert
        v-if="mode === 'login'"
        type="info"
        show-icon
        message="演示账号：user / 123456，manager / 123456，admin / 123456。"
        class="auth-note"
      />
      <div v-if="mode === 'login'" class="demo-login">
        <button
          v-for="account in demoAccounts"
          :key="account.username"
          type="button"
          @click="loginAs(account.username)"
        >
          {{ account.label }}
        </button>
      </div>
      <a-form-item label="用户名" required>
        <a-input v-model:value="username" placeholder="例如：xiaoming" />
      </a-form-item>
      <a-form-item label="密码" required>
        <a-input-password v-model:value="password" placeholder="至少 4 位" />
      </a-form-item>
      <a-form-item v-if="mode === 'register'" label="角色">
        <a-select v-model:value="role">
          <a-select-option value="user">普通用户</a-select-option>
          <a-select-option value="manager">经理</a-select-option>
          <a-select-option value="admin">管理员</a-select-option>
        </a-select>
      </a-form-item>
      <a-alert
        v-if="mode === 'register'"
        type="info"
        show-icon
        message="课程演示账号保存在浏览器本地，用于展示角色权限和数据沉淀。"
        class="auth-note"
      />
      <a-button type="primary" html-type="submit" block class="auth-submit">
        {{ mode === 'login' ? '登录' : '注册并登录' }}
      </a-button>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { login, register, type LocalUser, type UserRole } from '@/services/auth'

defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  close: []
  success: [user: LocalUser]
}>()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const role = ref<UserRole>('user')

const demoAccounts = [
  { label: '普通用户登录', username: 'user' },
  { label: '经理登录', username: 'manager' },
  { label: '管理员登录', username: 'admin' }
]

const submit = () => {
  try {
    const user = mode.value === 'login'
      ? login(username.value, password.value)
      : register(username.value, password.value, role.value)
    message.success(`${user.username}，欢迎回来`)
    emit('success', user)
    emit('close')
  } catch (error: any) {
    message.error(error.message || '操作失败')
  }
}

const loginAs = (name: string) => {
  username.value = name
  password.value = '123456'
  submit()
}
</script>

<style scoped>
.auth-tabs {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 18px;
}

.auth-tabs button {
  height: 36px;
  border: 1px solid #dce8e4;
  border-radius: 8px;
  background: #ffffff;
  color: #475467;
  cursor: pointer;
  font-weight: 700;
}

.auth-tabs button.active {
  border-color: #0f766e;
  background: #edf7f5;
  color: #0f766e;
}

.auth-note {
  margin-bottom: 14px;
}

.demo-login {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}

.demo-login button {
  min-height: 34px;
  border: 1px solid #b7d8d2;
  border-radius: 8px;
  background: #f6fbfa;
  color: #0f766e;
  cursor: pointer;
  font-weight: 700;
}

.demo-login button:hover {
  border-color: #0f766e;
  background: #edf7f5;
}

.auth-submit {
  height: 40px;
  border-radius: 8px;
  background: #0f766e;
  border-color: #0f766e;
  font-weight: 700;
}
</style>
