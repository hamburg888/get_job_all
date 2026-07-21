<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useJobHelpStore, type SectionView } from '../composables/useJobHelpStore'
import type { Platform, AccountItem, StartTaskPayload } from '../types'
import PlatformHero from '../components/PlatformHero.vue'
import SharedStatusPanel from '../components/SharedStatusPanel.vue'
import TaskConfigPanel from '../components/TaskConfigPanel.vue'
import AnalysisPanel from '../components/AnalysisPanel.vue'
import LivePreviewPanel from '../components/LivePreviewPanel.vue'
import TaskHistoryModal from '../components/TaskHistoryModal.vue'

const route = useRoute()
const router = useRouter()
const store = useJobHelpStore()

const platform = computed<Platform>(() => {
  const value = route.params.platform
  return value === 'boss' || value === 'qiancheng' || value === 'liepin' || value === 'yupao' ? value : 'zhilian'
})
const section = computed<SectionView>(() => {
  const value = route.params.section
  return value === 'analysis' || value === 'live' || value === 'config' ? value : 'config'
})
const panel = computed(() => store.panels[platform.value])
const form = computed(() => store.forms[platform.value])
const draftInputs = computed(() => store.draftInputs[platform.value])
const imagePreviewUrl = computed(() => store.imagePreviewUrls[platform.value])
const formMeta = computed(() => store.formStateMeta[platform.value])
const dashboardFilters = computed(() => store.dashboardFilters[platform.value])
const selectedAccount = computed(() => panel.value.accounts.find((item) => item.id === form.value.account_id) ?? null)
const showLoginCard = computed(() => Boolean(panel.value.taskStatus?.waiting_for_login))
const jobsPageCount = computed(() => Math.max(1, Math.ceil(panel.value.jobsTotal / store.pageSize)))
const canStart = computed(() => !['running', 'stopping'].includes(panel.value.taskStatus?.status ?? 'idle'))
const canStop = computed(() => ['running', 'stopping'].includes(panel.value.taskStatus?.status ?? 'idle'))

function handleOpenAccountBrowser(account: AccountItem | null) {
  if (!account) return
  void store.openAccountBrowserForSite(account)
}

const taskHistoryOpen = ref(false)

function handleRestoreConfig(config: Partial<StartTaskPayload>) {
  store.applyTaskRunConfig(platform.value, config)
}
const previewImageUrl = computed(() => {
  const frameUrl = panel.value.preview.frame_url
  if (!frameUrl) return ''
  return frameUrl.startsWith('http') ? frameUrl : `${store.API_ORIGIN}${frameUrl}`
})

function goSection(nextSection: SectionView) {
  void router.push({ name: 'platform', params: { platform: platform.value, section: nextSection } })
}

watch(
  [platform, section],
  async () => {
    await store.loadPlatform(platform.value)
    await store.syncPreviewStream(platform.value, section.value)
  },
  { immediate: true },
)

watch(
  () => panel.value.taskStatus?.waiting_for_login,
  async (waiting, previous) => {
    if (!waiting || previous === waiting) return
    await store.promptLoginConfirmation(platform.value)
  },
)

onBeforeUnmount(() => {
  store.closePreviewStream()
})
</script>

<template>
  <PlatformHero
    :platform="platform"
    :section="section"
    :meta="store.meta[platform]"
    :account="selectedAccount"
    :account-display-name="store.accountDisplayName"
    @change-section="goSection"
    @refresh="store.refreshAll"
    @open-history="taskHistoryOpen = true"
  />

  <SharedStatusPanel
    :platform="platform"
    :task-status="panel.taskStatus"
    :overview-time="panel.overview?.last_run_at ?? ''"
    :show-login-card="showLoginCard"
    :compact="true"
    :busy="store.busy[platform]"
    :can-start="canStart"
    :can-stop="canStop"
    :format-time="store.formatTime"
    :tone-for-status="store.toneForStatus"
    :status-label="store.statusLabel"
    @start="store.handleStart(platform)"
    @stop="store.handleStop(platform)"
    @confirm-login="store.handleConfirmLogin(platform)"
  />

  <TaskConfigPanel
    v-if="section === 'config'"
    :platform="platform"
    :form="form"
    :accounts="panel.accounts"
    :draft-inputs="draftInputs"
    :account-display-name="store.accountDisplayName"
    :image-preview-url="imagePreviewUrl"
    :image-uploading="store.imageUploading[platform]"
    :open-account-browser-loading="store.browserConfigLoading"
    :open-account-browser-account-id="store.browserConfigAccountId"
    :form-meta="formMeta"
    @add-tag="store.addTag(platform, $event.field, $event.source)"
    @remove-tag="store.removeTag(platform, $event.field, $event.value)"
    @apply-default-company-blacklist="store.applyDefaultCompanyBlacklist(platform)"
    @clear-company-blacklist="store.clearCompanyBlacklist(platform)"
    @clear-job-title-blacklist="store.clearJobTitleBlacklist(platform)"
    @image-selected="store.handleImageSelected(platform, $event)"
    @save-config="store.saveFormConfig(platform)"
    @export-config="store.exportFormConfig(platform)"
    @import-config="store.importFormConfig(platform, $event)"
    @open-account-browser="handleOpenAccountBrowser"
  />

  <AnalysisPanel
    v-else-if="section === 'analysis'"
    :platform="platform"
    :overview="panel.overview"
    :task-status="panel.taskStatus"
    :jobs="panel.jobs"
    :jobs-total="panel.jobsTotal"
    :current-page="store.pagination[platform]"
    :page-count="jobsPageCount"
    :logs="panel.logs"
    :stats="panel.stats"
    :accounts="panel.accounts"
    :filters="dashboardFilters"
    :format-time="store.formatTime"
    :tone-for-status="store.toneForStatus"
    :status-label="store.statusLabel"
    :display-reason="store.displayReason"
    @change-page="store.changeJobsPage(platform, $event)"
    @apply-filters="store.applyDashboardFilters(platform)"
    @reset-filters="store.resetDashboardFilters(platform)"
  />

  <LivePreviewPanel
    v-else
    :image-url="previewImageUrl"
    :preview="panel.preview"
    :format-time="store.formatTime"
    :logs="panel.logs"
  />

  <TaskHistoryModal
    v-model:open="taskHistoryOpen"
    :platform="platform"
    :format-time="store.formatTime"
    @restore="handleRestoreConfig"
  />
</template>
