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

    <a-form layout="vertical" @submit.prevent>

      <a-form-item :label="mode === 'login' ? '用户名或邮箱' : '用户名'" required>
        <a-input v-model:value="username" :placeholder="mode === 'login' ? '用户名或邮箱' : '例如：xiaoming'" />
      </a-form-item>
      <a-form-item v-if="mode === 'register'" label="邮箱">
        <a-input v-model:value="email" type="email" placeholder="用于接收旅行计划（选填）" />
      </a-form-item>
      <a-form-item label="密码" required>
        <a-input-password v-model:value="password" placeholder="至少 8 位，包含字母和数字" />
      </a-form-item>
      <a-form-item v-if="mode === 'register'" label="注册角色">
        <a-select v-model:value="role">
          <a-select-option value="user">普通用户</a-select-option>
          <a-select-option value="manager">经理</a-select-option>
          <a-select-option value="admin">管理员</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item v-if="mode === 'register' && role !== 'user'" label="授权码" required>
        <a-input-password
          v-model:value="inviteCode"
          :placeholder="role === 'manager' ? '请输入经理授权码' : '请输入管理员授权码'"
        />
      </a-form-item>
      <a-alert
        v-if="mode === 'register'"
        type="info"
        show-icon
        message="普通用户可直接注册；经理和管理员授权码由服务端管理员配置。"
        class="auth-note"
      />
      <a-button type="primary" html-type="button" block class="auth-submit" :loading="loading" @click="submit">
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
const email = ref('')
const password = ref('')
const role = ref<UserRole>('user')
const inviteCode = ref('')
const loading = ref(false)


const submit = async () => {
  if (loading.value) return
  try {
    if (!username.value.trim()) {
      throw new Error('请输入用户名')
    }
    if (!password.value) {
      throw new Error('请输入密码')
    }
    if (mode.value === 'register' && role.value !== 'user' && !inviteCode.value.trim()) {
      throw new Error(role.value === 'manager' ? '请输入经理授权码' : '请输入管理员授权码')
    }

    loading.value = true
    const user = mode.value === 'login'
      ? await login(username.value, password.value)
      : await register(username.value, password.value, email.value, role.value, inviteCode.value)
    message.success(user.username + '，欢迎回来')
    emit('success', user)
    emit('close')
    password.value = ''
    email.value = ''
    inviteCode.value = ''
  } catch (error: any) {
    message.error(error.message || '操作失败')
  } finally {
    loading.value = false
  }
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

.auth-submit {
  height: 40px;
  border-radius: 8px;
  background: #0f766e;
  border-color: #0f766e;
  font-weight: 700;
}
</style>
