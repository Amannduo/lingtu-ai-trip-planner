<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <div class="brand">
        <GlobalOutlined />
        <span>灵途 AI 旅行规划师</span>
      </div>
      <span class="header-tag">智能目的地推荐与行程规划</span>
    </a-layout-header>

    <a-layout-content class="app-content">
      <router-view />
    </a-layout-content>

    <a-layout-footer class="app-footer">
      灵途 AI 旅行规划师 ©2025
    </a-layout-footer>
  </a-layout>
</template>

<script setup lang="ts">
import { GlobalOutlined } from '@ant-design/icons-vue'
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
  background: rgba(255, 255, 255, 0.92) !important;
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
}

.brand svg {
  flex-shrink: 0;
  color: #0f766e;
  font-size: 21px;
}

.header-tag {
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
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
    height: 58px;
    padding: 0 16px;
  }

  .brand {
    font-size: 16px;
  }

  .brand span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .header-tag {
    display: none;
  }

  .app-content {
    min-height: calc(100vh - 106px);
  }

  .app-footer {
    padding: 12px 16px;
    font-size: 13px;
  }
}
</style>
