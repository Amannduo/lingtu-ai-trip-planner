<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <router-link to="/" class="brand">
        <GlobalOutlined />
        <span>灵途 AI 旅行助手</span>
      </router-link>

      <div class="header-actions">
        <a-button class="analysis-button" @click="openAgentAssistant">
          <BarChartOutlined />
          <span>智能分析</span>
        </a-button>

        <a-dropdown v-if="currentUser">
          <button class="user-chip" type="button">
            <UserOutlined />
            <span>{{ currentUser.username }}</span>
          </button>
          <template #overlay>
            <a-menu>
              <a-menu-item disabled>{{ roleLabel }}</a-menu-item>
              <a-menu-divider />
              <a-menu-item @click="handleLogout">退出登录</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>

        <a-button v-else type="primary" class="login-button" @click="authOpen = true">
          <LoginOutlined />
          <span>登录 / 注册</span>
        </a-button>
      </div>
    </a-layout-header>

    <a-layout-content class="app-content">
      <router-view />
    </a-layout-content>

    <a-layout-footer class="app-footer">
      灵途 AI 旅行画像与个性化推荐系统 © 2026
    </a-layout-footer>

    <AuthDialog
      :open="authOpen"
      @close="authOpen = false"
      @success="currentUser = $event"
    />
    <AgentAssistantModal
      :open="agentOpen"
      :user="currentUser"
      @close="agentOpen = false"
      @request-login="handleAgentNeedsLogin"
    />
  </a-layout>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  BarChartOutlined,
  GlobalOutlined,
  LoginOutlined,
  UserOutlined
} from '@ant-design/icons-vue'
import AgentAssistantModal from '@/components/AgentAssistantModal.vue'
import AuthDialog from '@/components/AuthDialog.vue'
import { getCurrentUser, logout, type LocalUser } from '@/services/auth'

const authOpen = ref(false)
const agentOpen = ref(false)
const currentUser = ref<LocalUser | null>(getCurrentUser())

const roleLabel = computed(() => {
  if (!currentUser.value) return ''
  const labels = { guest: '访客', user: '普通用户', manager: '经理', admin: '管理员' }
  return `角色：${labels[currentUser.value.role]}`
})

const syncAuth = () => {
  currentUser.value = getCurrentUser()
}

const openAgentAssistant = () => {
  agentOpen.value = true
}

const handleAgentNeedsLogin = () => {
  agentOpen.value = false
  authOpen.value = true
}

const handleLogout = () => {
  logout()
  currentUser.value = null
  message.success('已退出登录')
}

onMounted(() => {
  window.addEventListener('lingtu-auth-change', syncAuth)
})

onUnmounted(() => {
  window.removeEventListener('lingtu-auth-change', syncAuth)
})
</script>

<style>
html,
body,
#app {
  width: 100%;
  min-height: 100%;
  margin: 0;
}

* {
  box-sizing: border-box;
}

#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
}

.app-layout {
  min-height: 100vh;
  background: #f7faf9;
}

.app-header {
  position: sticky;
  top: 0;
  z-index: 20;
  height: 64px;
  padding: 0 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(255, 255, 255, 0.94) !important;
  border-bottom: 1px solid #e4ebe8;
  backdrop-filter: blur(12px);
  line-height: normal;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  color: #172033;
  font-size: 18px;
  font-weight: 800;
  line-height: 1.2;
  text-decoration: none;
}

.brand svg {
  flex-shrink: 0;
  color: #0f766e;
  font-size: 21px;
}

.header-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.analysis-button,
.login-button,
.user-chip {
  height: 38px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border-radius: 8px;
  font-weight: 700;
}

.analysis-button {
  border: 1px solid #dce8e4;
  background: #ffffff;
  color: #0f766e;
}

.analysis-button:hover {
  border-color: #0f766e !important;
  color: #0f766e !important;
  background: #edf7f5;
}

.login-button {
  background: #0f766e;
  border-color: #0f766e;
}

.user-chip {
  padding: 0 12px;
  border: 1px solid #dce8e4;
  background: #ffffff;
  color: #172033;
  cursor: pointer;
}

.app-content {
  min-height: calc(100vh - 112px);
}

.app-footer {
  padding: 14px 24px;
  color: #667085;
  text-align: center;
  background: #ffffff;
  border-top: 1px solid #e4ebe8;
}

@media (max-width: 640px) {
  .app-header {
    height: auto;
    min-height: 58px;
    padding: 10px 14px;
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
  }

  .brand {
    font-size: 16px;
  }

  .brand span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .header-actions {
    width: 100%;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .analysis-button,
  .login-button,
  .user-chip {
    width: 100%;
  }

  .app-content {
    min-height: calc(100vh - 124px);
  }

  .app-footer {
    padding: 12px 16px;
    font-size: 13px;
  }
}
</style>
