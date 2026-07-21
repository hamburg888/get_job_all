<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'

import { useJobHelpStore } from '../composables/useJobHelpStore'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import LoadingOverlay from '../components/LoadingOverlay.vue'
import ToastStack from '../components/ToastStack.vue'
import type { Platform } from '../types'
import ScrollBottomRocket from '../components/ScrollBottomRocket.vue'

const store = useJobHelpStore()
const route = useRoute()
const router = useRouter()

const currentRoutePlatform = computed<Platform | null>(() => {
  const platform = route.params.platform
  return platform === 'zhilian' || platform === 'qiancheng' || platform === 'liepin' || platform === 'boss' || platform === 'yupao' ? platform : null
})

function goPlatform(platform: Platform) {
  const section = route.params.section
  const targetSection = section === 'analysis' || section === 'live' || section === 'config' ? section : 'config'
  void router.push({ name: 'platform', params: { platform, section: targetSection } })
}

onMounted(() => {
  void store.initialize()
})
</script>

<template>
  <div class="shell">
    <aside class="left-nav">
      <div class="brand-card">JOB</div>

      <nav class="side-tabs">
        <button
          v-for="platform in store.platforms"
          :key="platform"
          :class="['side-tab', { active: currentRoutePlatform === platform }]"
          @click="goPlatform(platform)"
        >
          {{ store.meta[platform].shortName }}
        </button>
        <button :class="['side-tab', { active: route.name === 'accounts' }]" @click="router.push({ name: 'accounts' })">
          账号
        </button>
        <button :class="['side-tab', { active: route.name === 'resume-sync' }]" @click="router.push({ name: 'resume-sync' })">
          简历
        </button>
        <button :class="['side-tab', { active: route.name === 'settings' }]" @click="router.push({ name: 'settings' })">
          设置
        </button>
      </nav>

      <div class="service-status-wrap">
        <div
          :class="['service-status-dot', `service-status-dot--${store.backendServiceStatus.value}`]"
          :title="store.backendServiceMessage.value"
          aria-label="后端服务状态"
        ></div>
        <div class="service-status-pop">
          {{ store.backendServiceMessage.value }}
        </div>
      </div>
    </aside>

    <main class="content-area">
      <p v-if="store.errorMessage.value" class="error-banner">{{ store.errorMessage.value }}</p>
      <!-- <p v-if="store.actionMessage.value" class="action-banner">{{ store.actionMessage.value }}</p> -->
      <RouterView />
    </main>

    <ConfirmDialog
      :visible="store.confirmDialog.visible"
      :title="store.confirmDialog.title"
      :message="store.confirmDialog.message"
      :confirm-text="store.confirmDialog.confirmText"
      :cancel-text="store.confirmDialog.cancelText"
      @resolve="store.resolveConfirmDialog"
    />
    <LoadingOverlay
      v-if="store.browserConfigLoading.value"
      title="正在启动配置浏览器"
      message="正在为账号打开带缓存目录的浏览器，请稍等片刻，不需要重复点击。"
    />
   <ToastStack :items="store.toasts.value" />
   <ScrollBottomRocket />
 </div>
</template>
