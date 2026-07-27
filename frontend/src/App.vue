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
              <a-menu-item v-if="currentUser.email" disabled>{{ currentUser.email }}</a-menu-item>
              <a-menu-divider />
              <a-menu-item @click="openHistory">
                <HistoryOutlined />
                <span>历史计划</span>
              </a-menu-item>
              <a-menu-item @click="openEmailSettings">
                <MailOutlined />
                <span>收件邮箱</span>
              </a-menu-item>
              <a-menu-item :disabled="logoutBusy" @click="handleLogout">退出登录</a-menu-item>
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

    <a-modal
      v-model:open="emailOpen"
      title="收件邮箱"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="emailSaving"
      @ok="saveEmail"
    >
      <a-input v-model:value="accountEmail" type="email" allow-clear placeholder="name@example.com">
        <template #prefix><MailOutlined /></template>
      </a-input>
    </a-modal>

    <a-modal
      v-model:open="historyOpen"
      title="我的历史计划"
      :footer="null"
      :width="720"
      class="history-modal"
    >
      <div class="history-modal__intro">
        <div>
          <strong>登录账号中已保存的旅行计划</strong>
          <span>生成成功后会自动保存在这里</span>
        </div>
        <span v-if="!historyLoading && !historyError" class="history-modal__count">
          {{ historyTrips.length }} 个计划
        </span>
      </div>

      <div v-if="historyLoading" class="history-modal__loading">
        <a-spin tip="正在读取历史计划" />
      </div>

      <a-alert
        v-else-if="historyError"
        type="warning"
        show-icon
        :message="historyError"
      >
        <template #action>
          <a-button size="small" @click="loadHistory">重试</a-button>
        </template>
      </a-alert>

      <a-empty
        v-else-if="historyTrips.length === 0"
        description="还没有历史计划，生成后的行程会自动出现在这里"
      />

      <div v-else class="history-modal__list">
        <button
          v-for="trip in historyTrips"
          :key="trip.plan_no"
          type="button"
          class="history-plan"
          :disabled="!trip.has_detail"
          @click="openHistoryTrip(trip.plan_no)"
        >
          <span class="history-plan__icon"><GlobalOutlined /></span>
          <span class="history-plan__main">
            <strong>{{ trip.destination }}</strong>
            <span>{{ trip.start_date }} 至 {{ trip.end_date }}</span>
          </span>
          <span class="history-plan__meta">
            <span>{{ trip.travel_days }} 天</span>
            <span v-if="trip.budget">预算 ¥{{ trip.budget }}</span>
            <span>{{ trip.transportation }}</span>
          </span>
          <span class="history-plan__action">
            {{ trip.has_detail ? '查看计划 →' : '仅有摘要' }}
          </span>
        </button>
      </div>
    </a-modal>

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
import { useRouter } from 'vue-router'
import {
  BarChartOutlined,
  GlobalOutlined,
  HistoryOutlined,
  LoginOutlined,
  MailOutlined,
  UserOutlined
} from '@ant-design/icons-vue'
import AgentAssistantModal from '@/components/AgentAssistantModal.vue'
import AuthDialog from '@/components/AuthDialog.vue'
import {
  getCurrentUser,
  logout,
  LogoutNotConfirmedError,
  restoreSession,
  updateEmail,
  type LocalUser
} from '@/services/auth'
import { fetchTripHistory, type TripHistoryResponse } from '@/services/api'
import { unsubscribeFromPush } from '@/services/pushNotifications'

const router = useRouter()
const authOpen = ref(false)
const agentOpen = ref(false)
const emailOpen = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyError = ref('')
const historyTrips = ref<TripHistoryResponse['trips']>([])
let historyRequestVersion = 0
const emailSaving = ref(false)
const logoutBusy = ref(false)
const accountEmail = ref('')
const currentUser = ref<LocalUser | null>(getCurrentUser())

const roleLabel = computed(() => {
  if (!currentUser.value) return ''
  const labels = { guest: '访客', user: '普通用户', manager: '经理', admin: '管理员' }
  return `角色：${labels[currentUser.value.role]}`
})

const syncAuth = () => {
  const previousUserId = currentUser.value?.user_id ?? null
  currentUser.value = getCurrentUser()
  if (previousUserId !== (currentUser.value?.user_id ?? null)) {
    historyRequestVersion += 1
    historyOpen.value = false
    historyTrips.value = []
    historyError.value = ''
  }
}

const openAgentAssistant = () => {
  agentOpen.value = true
}

const handleAgentNeedsLogin = () => {
  agentOpen.value = false
  authOpen.value = true
}

const handleLoginRequest = () => {
  authOpen.value = true
}

const openEmailSettings = () => {
  accountEmail.value = currentUser.value?.email || ''
  emailOpen.value = true
}

const loadHistory = async () => {
  const requestedUserId = currentUser.value?.user_id
  if (!requestedUserId) {
    historyOpen.value = false
    authOpen.value = true
    return
  }

  const requestVersion = ++historyRequestVersion
  historyLoading.value = true
  historyError.value = ''
  try {
    const response = await fetchTripHistory()
    if (
      requestVersion !== historyRequestVersion
      || currentUser.value?.user_id !== requestedUserId
    ) return
    if (response.user_id !== requestedUserId) {
      throw new Error('历史计划与当前账号不匹配')
    }
    historyTrips.value = response.trips
  } catch (error: any) {
    if (
      requestVersion !== historyRequestVersion
      || currentUser.value?.user_id !== requestedUserId
    ) return
    historyTrips.value = []
    historyError.value = error.message || '读取历史计划失败'
  } finally {
    if (
      requestVersion === historyRequestVersion
      && currentUser.value?.user_id === requestedUserId
    ) {
      historyLoading.value = false
    }
  }
}

const openHistory = () => {
  historyOpen.value = true
  void loadHistory()
}

const openHistoryTrip = async (planNo: string) => {
  if (!currentUser.value) {
    historyOpen.value = false
    authOpen.value = true
    return
  }
  historyOpen.value = false
  await router.push({ path: '/result', query: { plan: planNo } })
}

const saveEmail = async () => {
  if (!currentUser.value || emailSaving.value) return
  try {
    emailSaving.value = true
    currentUser.value = await updateEmail(accountEmail.value)
    emailOpen.value = false
    message.success(accountEmail.value.trim() ? '收件邮箱已保存' : '收件邮箱已清除')
  } catch (error: any) {
    message.error(error.message || '邮箱保存失败')
  } finally {
    emailSaving.value = false
  }
}

const handleLogout = async () => {
  if (logoutBusy.value) return
  logoutBusy.value = true
  try {
    try {
      const result = await unsubscribeFromPush()
      if (result.cleanupError) {
        console.warn('[push] Server-side subscription cleanup failed:', result.cleanupError)
      }
    } catch (error) {
      console.warn('[push] Browser subscription cleanup failed:', error)
    }
    await logout()
    message.success('\u5df2\u9000\u51fa\u767b\u5f55')
  } catch (error: unknown) {
    if (error instanceof LogoutNotConfirmedError) {
      message.error(error.message)
    } else {
      message.error('\u9000\u51fa\u5931\u8d25\uff0c\u5f53\u524d\u767b\u5f55\u72b6\u6001\u5df2\u4fdd\u7559\uff0c\u8bf7\u91cd\u8bd5\u3002')
    }
  } finally {
    logoutBusy.value = false
  }
}

onMounted(async () => {
  window.addEventListener('lingtu-auth-change', syncAuth)
  window.addEventListener('lingtu-request-login', handleLoginRequest)
  currentUser.value = await restoreSession()
})

onUnmounted(() => {
  window.removeEventListener('lingtu-auth-change', syncAuth)
  window.removeEventListener('lingtu-request-login', handleLoginRequest)
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

.history-modal .ant-modal-content {
  border-radius: 14px;
  overflow: hidden;
}

.history-modal .ant-modal-header {
  margin-bottom: 16px;
}

.history-modal__intro {
  margin-bottom: 14px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid #dce8e4;
  border-radius: 10px;
  background: #f6faf9;
}

.history-modal__intro > div {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.history-modal__intro strong {
  color: #172033;
  font-size: 14px;
}

.history-modal__intro span {
  color: #667085;
  font-size: 12px;
}

.history-modal__count {
  flex: 0 0 auto;
  padding: 4px 9px;
  border-radius: 999px;
  color: #0f766e !important;
  background: #e4f4f1;
  font-weight: 700;
}

.history-modal__loading {
  min-height: 220px;
  display: grid;
  place-items: center;
}

.history-modal__list {
  max-height: min(58vh, 520px);
  display: grid;
  gap: 9px;
  overflow-y: auto;
}

.history-plan {
  width: 100%;
  padding: 13px 14px;
  display: grid;
  grid-template-columns: 38px minmax(150px, 1fr) minmax(180px, auto) auto;
  align-items: center;
  gap: 12px;
  border: 1px solid #e1e9e6;
  border-radius: 10px;
  color: inherit;
  background: #ffffff;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.history-plan:hover:not(:disabled) {
  transform: translateY(-1px);
  border-color: #8fc5bd;
  box-shadow: 0 8px 22px rgba(15, 118, 110, 0.08);
}

.history-plan:disabled {
  opacity: 0.62;
  cursor: not-allowed;
}

.history-plan__icon {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 9px;
  color: #0f766e;
  background: #e9f6f3;
}

.history-plan__main {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.history-plan__main strong {
  color: #172033;
  font-size: 14px;
}

.history-plan__main span,
.history-plan__meta {
  color: #667085;
  font-size: 12px;
}

.history-plan__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 10px;
}

.history-plan__action {
  color: #0f766e;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
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

  .history-modal {
    max-width: calc(100vw - 20px);
  }

  .history-plan {
    grid-template-columns: 34px minmax(0, 1fr);
  }

  .history-plan__meta,
  .history-plan__action {
    grid-column: 2;
  }
}
</style>
