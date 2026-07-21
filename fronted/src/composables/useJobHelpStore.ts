import { computed, reactive, ref, watch } from 'vue'

import {
  API_ORIGIN,
  closeAccountBrowserConfig,
  confirmLogin,
  createAccount,
  fetchAccounts,
  fetchHealth,
  fetchJobs,
  fetchLogs,
  fetchOverview,
  fetchPreviewState,
  fetchStats,
  fetchTaskStatus,
  openAccountBrowserConfig,
  previewStreamUrl,
  setDefaultAccount,
  startTask,
  stopTask,
  uploadBossImageResume,
  updateAccount,
} from '../api'
import { createBossReplyRule, createDefaultBossReplyRules, DEFAULT_COMPANY_BLACKLIST } from '../types'
import type {
  AccountItem,
  AccountPayload,
  BossReplyRule,
  DashboardFilters,
  JobItem,
  LogItem,
  Platform,
  PlatformOverview,
  PlatformStats,
  PreviewState,
  StartTaskPayload,
  TaskStatus,
} from '../types'

export type SectionView = 'config' | 'analysis' | 'live'

type DashboardState = {
  overview: PlatformOverview | null
  taskStatus: TaskStatus | null
  jobs: JobItem[]
  jobsTotal: number
  logs: LogItem[]
  stats: PlatformStats
  accounts: AccountItem[]
  preview: PreviewState
}

type ToastTone = 'info' | 'success' | 'warning' | 'danger'

type ToastItem = {
  id: number
  message: string
  tone: ToastTone
}

type SubmitAccountResult = {
  createdAccount: AccountItem | null
}

const platforms: Platform[] = ['zhilian', 'qiancheng', 'liepin', 'boss', 'yupao']
const pageSize = 10
const formStorageKey = 'job-help-task-forms-v3'
const legacyFormStorageKey = 'job-help-task-forms-v2'
const activeTaskStatuses = new Set(['running', 'stopping'])
const terminalTaskStatuses = new Set(['completed', 'failed', 'stopped'])

type SavedFormRecord = {
  saved_at: string | null
  payload: Partial<StartTaskPayload>
}

type FormStateMeta = {
  dirty: boolean
  lastSavedAt: string | null
  lastModifiedAt: string | null
}

function createEmptyStats(): PlatformStats {
  return { salary_buckets: [], experience_buckets: [], city_buckets: [], keyword_buckets: [] }
}

function createEmptyPreview(platform: Platform): PreviewState {
  return {
    platform,
    task_run_id: null,
    active: false,
    enabled: false,
    headless_enabled: false,
    interval_ms: 1000,
    seq: 0,
    frame_url: null,
    updated_at: null,
    current_step: '暂无任务',
    message: '当前暂无实时画面。',
  }
}

function createEmptyForm(platform: Platform): StartTaskPayload {
  return {
    platform,
    account_id: null,
    search_terms: [],
    use_manual_entry_url: platform !== 'qiancheng',
    manual_entry_url: '',
    keywords: [],
    cities: [],
    enable_company_blacklist: false,
    company_blacklist: [],
    enable_job_title_blacklist: false,
    job_title_blacklist: [],
    salary_min: null,
    salary_max: null,
    salary_floor_flexible_enabled: false,
    salary_floor_min: null,
    salary_floor_required_max: null,
    cooldown_days: 30,
    cross_account_dedupe_enabled: false,
    dedupe_match_by_title: false,
    dedupe_match_by_company: false,
    headless_enabled: false,
    live_preview_enabled: false,
    live_preview_interval_ms: 1000,
    delivery_interval_seconds: 3,
    max_deliveries_per_run: 100,
    auto_send_chat: false,
    use_chat_template: false,
    chat_template: '您好，我正在关注贵司的 {job_title} 岗位，希望进一步沟通。',
    enable_common_phrases: false,
    common_phrase_indexes: [1],
    enable_image_resume: false,
    image_resume_path: '',
    auto_agree_wechat_card: false,
    boss_first_greeting_only: false,
    enable_follow_up_reply: false,
    boss_reply_rules: createDefaultBossReplyRules(),
    boss_active_statuses: ['不限'],
    remark: '',
  }
}

function createEmptyFilters(): DashboardFilters {
  return {
    account_id: null,
    statuses: [],
    send_statuses: [],
    date_from: '',
    date_to: '',
  }
}

function normalizeBossReplyRules(input: unknown): BossReplyRule[] {
  if (!Array.isArray(input)) return createDefaultBossReplyRules()
  const rules = input
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const rule = item as Partial<BossReplyRule>
      return createBossReplyRule({
        id: typeof rule.id === 'string' ? rule.id : '',
        label: typeof rule.label === 'string' ? rule.label : '',
        enabled: rule.enabled === undefined ? true : Boolean(rule.enabled),
        keywords: Array.isArray(rule.keywords)
          ? rule.keywords.filter((keyword): keyword is string => typeof keyword === 'string' && keyword.trim().length > 0)
          : [],
        reply_template: typeof rule.reply_template === 'string' ? rule.reply_template : '',
      })
    })
    .filter((rule): rule is BossReplyRule => Boolean(rule))
  return rules.length ? rules : createDefaultBossReplyRules()
}

function normalizeStringList(input: unknown) {
  if (!Array.isArray(input)) return []
  const seen = new Set<string>()
  const values: string[] = []
  for (const item of input) {
    if (typeof item !== 'string') continue
    const normalized = item.trim()
    if (!normalized) continue
    const key = normalized.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    values.push(normalized)
  }
  return values
}

const meta: Record<Platform, { name: string; shortName: string; description: string }> = {
  zhilian: {
    name: '智联招聘',
    shortName: '智联',
    description: '统一管理账号、入口链接、投递节奏与实时监控，适合批量执行前的参数编排。',
  },
  qiancheng: {
    name: '前程无忧',
    shortName: '前程',
    description: '改为从前程无忧搜索页输入关键词起跑，可留空直接使用默认搜索页，再进入列表翻页、详情抓取与投递回流。',
  },
  liepin: {
    name: '猎聘',
    shortName: '猎聘',
    description: '围绕猎聘职位列表 URL、继续聊动作和可选追加消息来执行，适合直接使用你已经筛好的列表页链接。',
  },
  boss: {
    name: 'Boss 直聘',
    shortName: 'Boss',
    description: '围绕入口链接、投递策略与自动聊天联动，保持执行、分析与实时观察三块信息同步。',
  },
  yupao: {
    name: '鱼泡网',
    shortName: '鱼泡',
    description: '围绕鱼泡岗位列表 URL、沟通按钮和可选自动发消息来执行，整体流程按聊天型平台接入。',
  },
}

const panels = reactive<Record<Platform, DashboardState>>({
  zhilian: {
    overview: null,
    taskStatus: null,
    jobs: [],
    jobsTotal: 0,
    logs: [],
    stats: createEmptyStats(),
    accounts: [],
    preview: createEmptyPreview('zhilian'),
  },
  qiancheng: {
    overview: null,
    taskStatus: null,
    jobs: [],
    jobsTotal: 0,
    logs: [],
    stats: createEmptyStats(),
    accounts: [],
    preview: createEmptyPreview('qiancheng'),
  },
  liepin: {
    overview: null,
    taskStatus: null,
    jobs: [],
    jobsTotal: 0,
    logs: [],
    stats: createEmptyStats(),
    accounts: [],
    preview: createEmptyPreview('liepin'),
  },
  boss: {
    overview: null,
    taskStatus: null,
    jobs: [],
    jobsTotal: 0,
    logs: [],
    stats: createEmptyStats(),
    accounts: [],
    preview: createEmptyPreview('boss'),
  },
  yupao: {
    overview: null,
    taskStatus: null,
    jobs: [],
    jobsTotal: 0,
    logs: [],
    stats: createEmptyStats(),
    accounts: [],
    preview: createEmptyPreview('yupao'),
  },
})

const forms = reactive<Record<Platform, StartTaskPayload>>({
  zhilian: createEmptyForm('zhilian'),
  qiancheng: createEmptyForm('qiancheng'),
  liepin: createEmptyForm('liepin'),
  boss: createEmptyForm('boss'),
  yupao: createEmptyForm('yupao'),
})

const formStateMeta = reactive<Record<Platform, FormStateMeta>>({
  zhilian: { dirty: false, lastSavedAt: null, lastModifiedAt: null },
  qiancheng: { dirty: false, lastSavedAt: null, lastModifiedAt: null },
  liepin: { dirty: false, lastSavedAt: null, lastModifiedAt: null },
  boss: { dirty: false, lastSavedAt: null, lastModifiedAt: null },
  yupao: { dirty: false, lastSavedAt: null, lastModifiedAt: null },
})

const dashboardFilters = reactive<Record<Platform, DashboardFilters>>({
  zhilian: createEmptyFilters(),
  qiancheng: createEmptyFilters(),
  liepin: createEmptyFilters(),
  boss: createEmptyFilters(),
  yupao: createEmptyFilters(),
})

const draftInputs = reactive<Record<Platform, { searchTerm: string; keyword: string; city: string; commonPhraseIndex: string; companyBlacklist: string; jobTitleBlacklist: string }>>({
  zhilian: { searchTerm: '', keyword: '', city: '', commonPhraseIndex: '', companyBlacklist: '', jobTitleBlacklist: '' },
  qiancheng: { searchTerm: '', keyword: '', city: '', commonPhraseIndex: '', companyBlacklist: '', jobTitleBlacklist: '' },
  liepin: { searchTerm: '', keyword: '', city: '', commonPhraseIndex: '', companyBlacklist: '', jobTitleBlacklist: '' },
  boss: { searchTerm: '', keyword: '', city: '', commonPhraseIndex: '', companyBlacklist: '', jobTitleBlacklist: '' },
  yupao: { searchTerm: '', keyword: '', city: '', commonPhraseIndex: '', companyBlacklist: '', jobTitleBlacklist: '' },
})

const pagination = reactive<Record<Platform, number>>({ zhilian: 1, qiancheng: 1, liepin: 1, boss: 1, yupao: 1 })
const busy = reactive<Record<Platform, boolean>>({ zhilian: false, qiancheng: false, liepin: false, boss: false, yupao: false })
const allAccounts = ref<AccountItem[]>([])
const errorMessage = ref('')
const actionMessage = ref('')
const accountFilter = ref<'all' | Platform>('all')
const editingAccountId = ref<number | null>(null)
const savingAccount = ref(false)
const browserConfigLoading = ref(false)
const browserConfigAccountId = ref<number | null>(null)
const imageUploading = reactive<Record<Platform, boolean>>({ zhilian: false, qiancheng: false, liepin: false, boss: false, yupao: false })
const imagePreviewUrls = reactive<Record<Platform, string>>({ zhilian: '', qiancheng: '', liepin: '', boss: '', yupao: '' })
const backendServiceStatus = ref<'checking' | 'online' | 'offline'>('checking')
const backendServiceMessage = ref('正在检查后端服务')
const accountForm = reactive<AccountPayload>({
  platform: 'zhilian',
  phone: '',
  username: '',
  remark: '',
  is_default: false,
  is_active: true,
})
const confirmDialog = reactive({
  visible: false,
  title: '',
  message: '',
  confirmText: '确定',
  cancelText: '取消',
  onResolve: null as null | ((value: boolean) => void),
})
const toasts = ref<ToastItem[]>([])

let pollTimer: number | undefined
let healthPollTimer: number | undefined
let previewSource: EventSource | null = null
let initialized = false
let toastId = 0
let suppressFormTracking = false
let notificationUnsupportedWarned = false
let notificationDeniedWarned = false
const loginPromptOpen = reactive<Record<Platform, boolean>>({ zhilian: false, qiancheng: false, liepin: false, boss: false, yupao: false })
const savedFormSnapshots = reactive<Record<Platform, string>>({ zhilian: '', qiancheng: '', liepin: '', boss: '', yupao: '' })

function pushToast(message: string, tone: ToastTone = 'info') {
  const id = ++toastId
  toasts.value.push({ id, message, tone })
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }, 2600)
}

async function checkBackendHealth(silent = false) {
  try {
    const result = await fetchHealth()
    backendServiceStatus.value = result.status === 'ok' ? 'online' : 'checking'
    backendServiceMessage.value = result.status === 'ok' ? '后端服务在线' : '后端服务状态未知'
  } catch {
    backendServiceStatus.value = 'offline'
    backendServiceMessage.value = silent ? '后端服务离线' : '后端服务连接失败'
  }
}

function supportsBrowserNotification() {
  return typeof window !== 'undefined' && 'Notification' in window
}

async function ensureTaskFinishedNotificationPermission() {
  if (!supportsBrowserNotification()) {
    if (!notificationUnsupportedWarned) {
      notificationUnsupportedWarned = true
      pushToast('当前浏览器不支持系统通知，任务结束时不会弹出提醒', 'warning')
    }
    return false
  }

  if (Notification.permission === 'granted') {
    return true
  }

  if (Notification.permission === 'denied') {
    if (!notificationDeniedWarned) {
      notificationDeniedWarned = true
      pushToast('浏览器通知权限已被拒绝，任务结束时不会弹出提醒', 'warning')
    }
    return false
  }

  try {
    const permission = await Notification.requestPermission()
    if (permission === 'granted') {
      pushToast('已开启浏览器通知，任务结束后会自动提醒你', 'success')
      return true
    }
    if (!notificationDeniedWarned) {
      notificationDeniedWarned = true
      pushToast('未开启浏览器通知，任务结束时不会弹出提醒', 'warning')
    }
  } catch {
    if (!notificationUnsupportedWarned) {
      notificationUnsupportedWarned = true
      pushToast('浏览器通知初始化失败，任务结束时不会弹出提醒', 'warning')
    }
  }
  return false
}

function isActiveTaskStatus(status?: string | null) {
  return Boolean(status && activeTaskStatuses.has(status))
}

function isTerminalTaskStatus(status?: string | null) {
  return Boolean(status && terminalTaskStatuses.has(status))
}

function buildTaskFinishedNotificationBody(platform: Platform, taskStatus: TaskStatus) {
  const parts = [`状态：${statusLabel(taskStatus.status)}`]
  if (taskStatus.current_step) {
    parts.push(taskStatus.current_step)
  }
  parts.push(`投递 ${taskStatus.delivered_count}，失败 ${taskStatus.failed_count}，处理 ${taskStatus.processed_count}`)
  return `${meta[platform].name}任务已结束\n${parts.join('\n')}`
}

function notifyTaskFinished(platform: Platform, taskStatus: TaskStatus) {
  if (!supportsBrowserNotification() || Notification.permission !== 'granted') {
    return
  }
  const notification = new Notification(`${meta[platform].name}任务提醒`, {
    body: buildTaskFinishedNotificationBody(platform, taskStatus),
    tag: `job-help-${platform}-${taskStatus.task_run_id ?? 'latest'}-${taskStatus.status}`,
  })
  notification.onclick = () => {
    window.focus()
    notification.close()
  }
  window.setTimeout(() => notification.close(), 12000)
}

function maybeNotifyTaskFinished(platform: Platform, previousStatus: TaskStatus | null, nextStatus: TaskStatus) {
  if (!previousStatus) return
  if (!isActiveTaskStatus(previousStatus.status) || !isTerminalTaskStatus(nextStatus.status)) return
  if (
    previousStatus.task_run_id !== null &&
    nextStatus.task_run_id !== null &&
    previousStatus.task_run_id !== nextStatus.task_run_id
  ) {
    return
  }
  notifyTaskFinished(platform, nextStatus)
}

function accountDisplayName(account: Pick<AccountItem, 'username' | 'phone'>) {
  return account.username || account.phone || '未命名账号'
}

function formatTime(value: string | null | undefined) {
  if (!value) return '暂无'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function toneForStatus(status?: string) {
  if (!status) return 'neutral'
  if (['running', 'completed', 'delivered'].includes(status)) return 'success'
  if (['failed'].includes(status)) return 'danger'
  if (['stopping', 'stopped', 'skipped'].includes(status)) return 'warning'
  return 'neutral'
}

function statusLabel(status?: string) {
  const mapping: Record<string, string> = {
    idle: '空闲',
    running: '运行中',
    stopping: '停止中',
    stopped: '已停止',
    completed: '已完成',
    failed: '失败',
    delivered: '投递成功',
    skipped: '已跳过',
  }
  return mapping[status ?? ''] ?? (status || '-')
}

function displayReason(status: string | undefined, reason: string | null | undefined) {
  if (status === 'delivered' || status === 'completed' || !reason) return '-'
  return reason
}

function deriveUploadedImageUrl(path: string) {
  if (!path) return ''
  const normalized = path.replace(/\\/g, '/')
  const marker = '/boss_chat_images/'
  const markerIndex = normalized.lastIndexOf(marker)
  if (markerIndex === -1) return ''
  const fileName = normalized.slice(markerIndex + marker.length)
  if (!fileName) return ''
  return `${API_ORIGIN}/uploads/boss_chat_images/${fileName}`
}

function normalizeFormPayload(platform: Platform, stored?: Partial<StartTaskPayload>): StartTaskPayload {
  const source = stored ?? {}
  const normalizedSearchTerms = normalizeStringList(source.search_terms)
  return {
    ...createEmptyForm(platform),
    ...source,
    platform,
    search_terms: platform === 'qiancheng' ? normalizedSearchTerms.slice(0, 1) : normalizedSearchTerms,
    use_manual_entry_url: platform !== 'qiancheng',
    manual_entry_url: typeof source.manual_entry_url === 'string' ? source.manual_entry_url : '',
    keywords: normalizeStringList(source.keywords),
    cities: normalizeStringList(source.cities),
    enable_company_blacklist: Boolean(source.enable_company_blacklist),
    company_blacklist: normalizeStringList(source.company_blacklist),
    account_id: typeof source.account_id === 'number' ? source.account_id : null,
    salary_min: typeof source.salary_min === 'number' ? source.salary_min : null,
    salary_max: typeof source.salary_max === 'number' ? source.salary_max : null,
    cooldown_days: typeof source.cooldown_days === 'number' ? source.cooldown_days : 30,
    headless_enabled: Boolean(source.headless_enabled),
    live_preview_enabled: Boolean(source.live_preview_enabled),
    live_preview_interval_ms: typeof source.live_preview_interval_ms === 'number' ? source.live_preview_interval_ms : 1000,
    delivery_interval_seconds: typeof source.delivery_interval_seconds === 'number' ? source.delivery_interval_seconds : 3,
    max_deliveries_per_run: typeof source.max_deliveries_per_run === 'number' ? source.max_deliveries_per_run : 100,
    auto_send_chat: Boolean(source.auto_send_chat),
    use_chat_template: Boolean(source.use_chat_template),
    chat_template:
      typeof source.chat_template === 'string'
        ? source.chat_template
        : '您好，我正在关注贵司的 {job_title} 岗位，希望进一步沟通。',
    enable_common_phrases: Boolean(source.enable_common_phrases),
    common_phrase_indexes: Array.isArray(source.common_phrase_indexes)
      ? source.common_phrase_indexes.filter((item): item is number => typeof item === 'number' && item > 0)
      : [1],
    enable_image_resume: Boolean(source.enable_image_resume),
    image_resume_path: typeof source.image_resume_path === 'string' ? source.image_resume_path : '',
    auto_agree_wechat_card: Boolean(source.auto_agree_wechat_card),
    boss_first_greeting_only: Boolean(source.boss_first_greeting_only),
    enable_follow_up_reply: Boolean(source.enable_follow_up_reply),
    boss_reply_rules: normalizeBossReplyRules(source.boss_reply_rules),
    boss_active_statuses: Array.isArray(source.boss_active_statuses)
      ? source.boss_active_statuses.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      : ['不限'],
    remark: typeof source.remark === 'string' ? source.remark : '',
  }
}

function snapshotFormPayload(platform: Platform): StartTaskPayload {
  const normalized = normalizeFormPayload(platform, forms[platform])
  return {
    ...normalized,
    search_terms: [...normalized.search_terms],
    keywords: [...normalized.keywords],
    cities: [...normalized.cities],
    company_blacklist: [...normalized.company_blacklist],
    job_title_blacklist: [...normalized.job_title_blacklist],
    common_phrase_indexes: [...normalized.common_phrase_indexes],
    boss_reply_rules: normalized.boss_reply_rules.map((rule) => ({
      ...rule,
      keywords: [...rule.keywords],
    })),
    boss_active_statuses: [...normalized.boss_active_statuses],
    remark: normalized.remark,
  }
}

function serializeFormPayload(platform: Platform) {
  return JSON.stringify(snapshotFormPayload(platform))
}

function readStoredForms(): Partial<Record<Platform, SavedFormRecord>> {
  const raw = window.localStorage.getItem(formStorageKey)
  if (raw) {
    try {
      return JSON.parse(raw) as Partial<Record<Platform, SavedFormRecord>>
    } catch {
      window.localStorage.removeItem(formStorageKey)
    }
  }

  const legacyRaw = window.localStorage.getItem(legacyFormStorageKey)
  if (!legacyRaw) return {}
  try {
    const parsed = JSON.parse(legacyRaw) as Partial<Record<Platform, Partial<StartTaskPayload>>>
    const migrated = Object.fromEntries(
      platforms
        .filter((platform) => parsed[platform])
        .map((platform) => [
          platform,
          {
            saved_at: null,
            payload: parsed[platform] ?? {},
          },
        ]),
    ) as Partial<Record<Platform, SavedFormRecord>>
    window.localStorage.setItem(formStorageKey, JSON.stringify(migrated))
    window.localStorage.removeItem(legacyFormStorageKey)
    return migrated
  } catch {
    window.localStorage.removeItem(legacyFormStorageKey)
    return {}
  }
}

function applyStoredForms() {
  suppressFormTracking = true
  try {
    const parsed = readStoredForms()
    for (const platform of platforms) {
      const record = parsed[platform]
      if (!record) {
        savedFormSnapshots[platform] = serializeFormPayload(platform)
        continue
      }
      const normalized = normalizeFormPayload(platform, record.payload)
      Object.assign(forms[platform], normalized)
      imagePreviewUrls[platform] = deriveUploadedImageUrl(forms[platform].image_resume_path)
      savedFormSnapshots[platform] = JSON.stringify(normalized)
      formStateMeta[platform].dirty = false
      formStateMeta[platform].lastSavedAt = record.saved_at
      formStateMeta[platform].lastModifiedAt = record.saved_at
    }
  } finally {
    window.setTimeout(() => {
      suppressFormTracking = false
    }, 0)
  }
}

function persistSavedForms(platform: Platform) {
  const current = readStoredForms()
  const now = new Date().toISOString()
  const snapshot = snapshotFormPayload(platform)
  current[platform] = {
    saved_at: now,
    payload: snapshot,
  }
  window.localStorage.setItem(formStorageKey, JSON.stringify(current))
  savedFormSnapshots[platform] = JSON.stringify(snapshot)
  formStateMeta[platform].dirty = false
  formStateMeta[platform].lastSavedAt = now
  formStateMeta[platform].lastModifiedAt = now
}

for (const platform of platforms) {
  watch(
    () => serializeFormPayload(platform),
    (nextValue) => {
      if (suppressFormTracking) return
      formStateMeta[platform].lastModifiedAt = new Date().toISOString()
      formStateMeta[platform].dirty = nextValue !== savedFormSnapshots[platform]
      imagePreviewUrls[platform] = deriveUploadedImageUrl(forms[platform].image_resume_path)
    },
  )
}

function addTag(
  platform: Platform,
  field: 'search_terms' | 'keywords' | 'cities' | 'company_blacklist' | 'job_title_blacklist' | 'common_phrase_indexes',
  source: 'searchTerm' | 'keyword' | 'city' | 'companyBlacklist' | 'commonPhraseIndex',
) {
  const rawValue = draftInputs[platform][source]
  const value = String(rawValue ?? '').trim()
  if (!value) {
    pushToast('请输入内容后再添加', 'warning')
    return
  }
  if (field === 'common_phrase_indexes') {
    const numericValue = Number.parseInt(value, 10)
    if (!Number.isInteger(numericValue) || numericValue <= 0) {
      pushToast('常用语序号必须是大于 0 的整数', 'warning')
      return
    }
    if (!forms[platform].common_phrase_indexes.includes(numericValue)) {
      forms[platform].common_phrase_indexes.push(numericValue)
      forms[platform].common_phrase_indexes.sort((left, right) => left - right)
    } else {
      pushToast('该常用语序号已经添加过了', 'warning')
    }
  } else if (!forms[platform][field].includes(value)) {
    forms[platform][field].push(value)
  } else {
    pushToast('该项已经添加过了', 'warning')
  }
  draftInputs[platform][source] = ''
}

function removeTag(
  platform: Platform,
  field: 'search_terms' | 'keywords' | 'cities' | 'company_blacklist' | 'job_title_blacklist' | 'common_phrase_indexes',
  value: string | number,
) {
  if (field === 'common_phrase_indexes') {
    forms[platform].common_phrase_indexes = forms[platform].common_phrase_indexes.filter((item) => item !== value)
    return
  }
  forms[platform][field] = forms[platform][field].filter((item) => item !== value)
}

function applyDefaultCompanyBlacklist(platform: Platform) {
  forms[platform].company_blacklist = [...DEFAULT_COMPANY_BLACKLIST]
  forms[platform].enable_company_blacklist = true
  draftInputs[platform].companyBlacklist = ''
  pushToast(`${meta[platform].name}已填入默认公司黑名单`, 'success')
}

function clearCompanyBlacklist(platform: Platform) {
  forms[platform].company_blacklist = []
  draftInputs[platform].companyBlacklist = ''
  pushToast(`${meta[platform].name}公司黑名单已清空`, 'info')
}

function clearJobTitleBlacklist(platform: Platform) {
  forms[platform].job_title_blacklist = []
  draftInputs[platform].jobTitleBlacklist = ''
  pushToast(`${meta[platform].name}岗位标题黑名单已清空`, 'info')
}

async function handleImageSelected(platform: Platform, file: File | null) {
  if (!file) {
    forms[platform].image_resume_path = ''
    imagePreviewUrls[platform] = ''
    persistSavedForms(platform)
    return
  }
  if (!file.type.startsWith('image/')) {
    pushToast('请选择图片文件', 'warning')
    return
  }
  imageUploading[platform] = true
  errorMessage.value = ''
  try {
    const result = await uploadBossImageResume(file)
    forms[platform].image_resume_path = result.path
    imagePreviewUrls[platform] = result.url ? `${API_ORIGIN}${result.url}` : deriveUploadedImageUrl(result.path)
    persistSavedForms(platform)
    pushToast(`图片已上传并保存：${result.name}`, 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '上传图片失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    imageUploading[platform] = false
  }
}

function saveFormConfig(platform: Platform) {
  persistSavedForms(platform)
  pushToast(`${meta[platform].name}配置已保存到本地`, 'success')
}

function exportFormConfig(platform: Platform) {
  const payload = {
    platform,
    exported_at: new Date().toISOString(),
    payload: snapshotFormPayload(platform),
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `job-help-${platform}-config-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
  anchor.click()
  window.URL.revokeObjectURL(url)
}

async function importFormConfig(platform: Platform, file: File | null) {
  if (!file) return
  errorMessage.value = ''
  try {
    const text = await file.text()
    const parsed = JSON.parse(text) as unknown
    const payload =
      parsed && typeof parsed === 'object' && 'payload' in parsed
        ? (parsed as { payload?: Partial<StartTaskPayload> }).payload
        : (parsed as Partial<StartTaskPayload>)
    if (!payload || typeof payload !== 'object') {
      throw new Error('配置文件内容不正确')
    }
    suppressFormTracking = true
    Object.assign(forms[platform], normalizeFormPayload(platform, payload))
    imagePreviewUrls[platform] = deriveUploadedImageUrl(forms[platform].image_resume_path)
    window.setTimeout(() => {
      suppressFormTracking = false
      formStateMeta[platform].lastModifiedAt = new Date().toISOString()
      formStateMeta[platform].dirty = serializeFormPayload(platform) !== savedFormSnapshots[platform]
    }, 0)
    pushToast(`${meta[platform].name}配置已导入，请确认后点击保存`, 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入配置失败'
    pushToast(errorMessage.value, 'danger')
  }
}

function applyTaskRunConfig(platform: Platform, config: Partial<StartTaskPayload>) {
  if (!config || typeof config !== 'object') return
  suppressFormTracking = true
  Object.assign(forms[platform], normalizeFormPayload(platform, { ...forms[platform], ...config }))
  imagePreviewUrls[platform] = deriveUploadedImageUrl(forms[platform].image_resume_path)
  window.setTimeout(() => {
    suppressFormTracking = false
    formStateMeta[platform].lastModifiedAt = new Date().toISOString()
    formStateMeta[platform].dirty = serializeFormPayload(platform) !== savedFormSnapshots[platform]
  }, 0)
  pushToast(`${meta[platform].name}历史配置已恢复到表单，未自动保存`, 'success')
}

async function applyDashboardFilters(platform: Platform) {
  pagination[platform] = 1
  await loadPlatform(platform)
}

async function resetDashboardFilters(platform: Platform) {
  Object.assign(dashboardFilters[platform], createEmptyFilters())
  pagination[platform] = 1
  await loadPlatform(platform)
}

function syncAccountSelections() {
  for (const platform of platforms) {
    const available = panels[platform].accounts
    if (available.length === 0) {
      forms[platform].account_id = null
      continue
    }
    const exists = available.some((account) => account.id === forms[platform].account_id)
    if (!exists) {
      forms[platform].account_id = available.find((account) => account.is_default)?.id ?? available[0].id
    }
  }
}

function clampPagination(platform: Platform) {
  const totalPages = Math.max(1, Math.ceil(panels[platform].jobsTotal / pageSize))
  pagination[platform] = Math.min(Math.max(1, pagination[platform]), totalPages)
}

function normalizedDashboardFilters(platform: Platform): DashboardFilters {
  const filters = dashboardFilters[platform]
  if (platform === 'boss' || platform === 'liepin' || platform === 'yupao') return filters
  return {
    ...filters,
    send_statuses: [],
  }
}

async function loadPlatform(platform: Platform) {
  const previousTaskStatus = panels[platform].taskStatus ? { ...panels[platform].taskStatus } : null
  const filters = normalizedDashboardFilters(platform)
  const [overview, taskStatus, jobs, logs, stats, preview] = await Promise.all([
    fetchOverview(platform, filters),
    fetchTaskStatus(platform),
    fetchJobs(platform, pagination[platform], pageSize, filters),
    fetchLogs(platform, filters),
    fetchStats(platform, filters),
    fetchPreviewState(platform),
  ])
  panels[platform].overview = overview
  panels[platform].taskStatus = taskStatus
  panels[platform].jobs = jobs.items
  panels[platform].jobsTotal = jobs.total
  panels[platform].logs = logs
  panels[platform].stats = stats
  panels[platform].preview = preview
  maybeNotifyTaskFinished(platform, previousTaskStatus, taskStatus)
  clampPagination(platform)
}

async function loadAccounts() {
  const accounts = await fetchAccounts()
  allAccounts.value = accounts
  for (const platform of platforms) {
    panels[platform].accounts = accounts.filter((account) => account.platform === platform)
  }
  syncAccountSelections()
}

async function refreshAll() {
  try {
    await Promise.all([loadAccounts(), ...platforms.map((platform) => loadPlatform(platform))])
    backendServiceStatus.value = 'online'
    backendServiceMessage.value = '后端服务在线'
  } catch (error) {
    backendServiceStatus.value = 'offline'
    backendServiceMessage.value = '后端服务连接失败'
    errorMessage.value = error instanceof Error ? error.message : '数据加载失败'
    pushToast(errorMessage.value, 'danger')
  }
}

function resetAccountForm(platform: Platform = 'zhilian') {
  editingAccountId.value = null
  accountForm.platform = platform
  accountForm.phone = ''
  accountForm.username = ''
  accountForm.remark = ''
  accountForm.is_default = true
  accountForm.is_active = true
}

function startCreateAccount(platform?: Platform) {
  resetAccountForm(platform ?? (accountFilter.value === 'all' ? 'zhilian' : accountFilter.value))
}

function startEditAccount(account: AccountItem) {
  editingAccountId.value = account.id
  accountForm.platform = account.platform
  accountForm.phone = account.phone
  accountForm.username = account.username
  accountForm.remark = account.remark
  accountForm.is_default = account.is_default
  accountForm.is_active = account.is_active
}

function openConfirmDialog(options: { title: string; message: string; confirmText?: string; cancelText?: string }) {
  confirmDialog.visible = true
  confirmDialog.title = options.title
  confirmDialog.message = options.message
  confirmDialog.confirmText = options.confirmText ?? '确定'
  confirmDialog.cancelText = options.cancelText ?? '取消'
  return new Promise<boolean>((resolve) => {
    confirmDialog.onResolve = resolve
  })
}

function resolveConfirmDialog(value: boolean) {
  confirmDialog.visible = false
  const resolver = confirmDialog.onResolve
  confirmDialog.onResolve = null
  if (resolver) resolver(value)
}

async function configureAccountBrowser(account: AccountItem) {
  errorMessage.value = ''
  browserConfigLoading.value = true
  browserConfigAccountId.value = account.id
  try {
    await openAccountBrowserConfig(account.id)
    browserConfigLoading.value = false
    const confirmed = await openConfirmDialog({
      title: '登录配置进行中',
      message: `已打开 ${meta[account.platform].name} 配置浏览器，账号：${accountDisplayName(account)}。请先在打开的浏览器中完成登录配置，完成后点击“已配置完成”关闭浏览器。`,
      confirmText: '已配置完成',
      cancelText: '稍后再说',
    })
    if (confirmed) {
      await closeAccountBrowserConfig(account.id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打开账号配置浏览器失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    browserConfigLoading.value = false
    browserConfigAccountId.value = null
  }
}

async function openAccountBrowserForSite(account: AccountItem) {
  errorMessage.value = ''
  browserConfigLoading.value = true
  browserConfigAccountId.value = account.id
  try {
    await openAccountBrowserConfig(account.id)
    pushToast(
      `已使用 ${meta[account.platform].name} 账号 ${accountDisplayName(account)} 的 Cookie 打开对应网站`,
      'success',
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打开浏览器失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    browserConfigLoading.value = false
    browserConfigAccountId.value = null
  }
}

async function submitAccount() {
  savingAccount.value = true
  errorMessage.value = ''
  try {
    if (editingAccountId.value) {
      await updateAccount(editingAccountId.value, { ...accountForm })
      await refreshAll()
      resetAccountForm(accountForm.platform)
      pushToast('账号已更新', 'success')
      return { createdAccount: null } satisfies SubmitAccountResult
    }
    const created = await createAccount({ ...accountForm })
    await refreshAll()
    resetAccountForm(accountForm.platform)
    pushToast(`账号 ${accountDisplayName(created)} 已创建`, 'success')
    return { createdAccount: created } satisfies SubmitAccountResult
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存账号失败'
    pushToast(errorMessage.value, 'danger')
    return { createdAccount: null } satisfies SubmitAccountResult
  } finally {
    savingAccount.value = false
  }
}

async function promptConfigureCreatedAccount(account: AccountItem) {
  const shouldConfigure = await openConfirmDialog({
    title: '账号创建成功',
    message: `账号 ${accountDisplayName(account)} 已创建，是否现在打开浏览器配置 ${meta[account.platform].name} 登录？`,
    confirmText: '立即配置',
    cancelText: '暂不配置',
  })
  if (shouldConfigure) {
    await configureAccountBrowser(account)
  }
}

async function handleSetDefault(account: AccountItem) {
  errorMessage.value = ''
  try {
    await setDefaultAccount(account.id)
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '设置默认账号失败'
    pushToast(errorMessage.value, 'danger')
  }
}

async function handleStart(platform: Platform) {
  const status = panels[platform].taskStatus?.status
  if (busy[platform]) return
  if (status === 'running' || status === 'stopping') {
    pushToast('当前任务正在运行或停止中，请勿重复启动', 'warning')
    return
  }
  busy[platform] = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const normalizedSearchTerms = forms[platform].search_terms.map((item) => item.trim()).filter(Boolean)
    const payload: StartTaskPayload = {
      ...forms[platform],
      search_terms: normalizedSearchTerms,
      manual_entry_url: forms[platform].manual_entry_url.trim(),
      keywords: forms[platform].keywords.map((item) => item.trim()).filter(Boolean),
      cities: forms[platform].cities.map((item) => item.trim()).filter(Boolean),
      company_blacklist: normalizeStringList(forms[platform].company_blacklist),
      job_title_blacklist: normalizeStringList(forms[platform].job_title_blacklist),
      boss_reply_rules: forms[platform].boss_reply_rules.map((rule) =>
        createBossReplyRule({
          id: rule.id,
          label: rule.label.trim(),
          enabled: rule.enabled,
          keywords: rule.keywords.map((keyword) => keyword.trim()).filter(Boolean),
          reply_template: rule.reply_template.trim(),
        }),
      ),
    }
    const isSearchPlatform = platform === 'qiancheng'
    if (isSearchPlatform) {
      payload.use_manual_entry_url = false
      payload.manual_entry_url = ''
      payload.search_terms = normalizedSearchTerms.length ? [normalizedSearchTerms[0]] : []
    } else {
      payload.use_manual_entry_url = true
      payload.search_terms = []
      if (!payload.manual_entry_url) {
        throw new Error('请先输入入口 URL 后再启动任务')
      }
    }
    if (!payload.account_id) {
      throw new Error('请先选择投递账号后再启动任务')
    }
    void ensureTaskFinishedNotificationPermission()
    if (platform === 'boss') {
      // Boss 不再暴露总开关：任一聊天子功能开启即视为启用自动聊天
      payload.auto_send_chat = Boolean(
        payload.use_chat_template
        || payload.enable_common_phrases
        || payload.enable_image_resume
        || payload.auto_agree_wechat_card
        || payload.enable_follow_up_reply,
      )
      if (payload.boss_first_greeting_only) {
        payload.enable_follow_up_reply = false
        // 仅首次聊天时仍允许首聊相关动作
        payload.auto_send_chat = Boolean(
          payload.use_chat_template
          || payload.enable_common_phrases
          || payload.enable_image_resume
          || payload.auto_agree_wechat_card
          || payload.enable_follow_up_reply,
        )
      }
      if (payload.delivery_interval_seconds < 0) {
        throw new Error('投递间隔不能小于 0 秒')
      }
      if (payload.max_deliveries_per_run < 1) {
        throw new Error('单次投递成功上限必须至少为 1')
      }
      if (payload.enable_follow_up_reply) {
        payload.boss_reply_rules = payload.boss_reply_rules.filter(
          (rule) => rule.label || rule.keywords.length || rule.reply_template,
        )
      }
    }
    console.info(`[job-help:start:${platform}]`, {
      account_id: payload.account_id,
      headless_enabled: payload.headless_enabled,
      live_preview_enabled: payload.live_preview_enabled,
      use_manual_entry_url: payload.use_manual_entry_url,
      manual_entry_url: payload.manual_entry_url,
    })
    const result = await startTask(payload)
    Object.assign(forms[platform], payload)
    await loadPlatform(platform)
    await loadAccounts()
    actionMessage.value = `${meta[platform].name}任务已启动：${result.current_step}`
    pushToast(actionMessage.value, 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '启动任务失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    busy[platform] = false
  }
}

async function handleStop(platform: Platform) {
  const status = panels[platform].taskStatus?.status
  if (busy[platform]) return
  if (!status || ['idle', 'stopped', 'completed', 'failed'].includes(status)) {
    pushToast('当前没有可停止的运行任务', 'warning')
    return
  }
  busy[platform] = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    if (panels[platform].taskStatus) {
      panels[platform].taskStatus = {
        ...panels[platform].taskStatus,
        status: 'stopping',
        current_step: '收到停止指令，正在收尾',
        waiting_for_login: false,
      }
    }
    const result = await stopTask(platform)
    panels[platform].taskStatus = result
    loginPromptOpen[platform] = false
    await loadPlatform(platform)
    actionMessage.value = `${meta[platform].name}任务状态：${result.current_step}`
    pushToast(actionMessage.value, 'info')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '停止任务失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    busy[platform] = false
  }
}

async function handleConfirmLogin(platform: Platform) {
  if (busy[platform]) return
  if (!panels[platform].taskStatus?.waiting_for_login) {
    pushToast('当前没有等待确认登录的任务', 'warning')
    return
  }
  busy[platform] = true
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const result = await confirmLogin(platform)
    loginPromptOpen[platform] = false
    await loadPlatform(platform)
    actionMessage.value = `${meta[platform].name}：${result.current_step}`
    pushToast(actionMessage.value, 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录确认失败'
    pushToast(errorMessage.value, 'danger')
  } finally {
    busy[platform] = false
  }
}

async function promptLoginConfirmation(platform: Platform) {
  if (loginPromptOpen[platform]) return
  if (!panels[platform].taskStatus?.waiting_for_login) return
  loginPromptOpen[platform] = true
  try {
    const confirmed = await openConfirmDialog({
      title: `${meta[platform].name} 登录提醒`,
      message: `浏览器会保持打开。请先在对应账号缓存窗口完成${meta[platform].name}登录；完成后点击“已登录，继续执行”，如暂时不处理可点“稍后处理”。`,
      confirmText: '已登录，继续执行',
      cancelText: '稍后处理',
    })
    if (confirmed && panels[platform].taskStatus?.waiting_for_login) {
      await handleConfirmLogin(platform)
    }
  } finally {
    loginPromptOpen[platform] = false
  }
}

function changeJobsPage(platform: Platform, nextPage: number) {
  pagination[platform] = Math.min(Math.max(1, nextPage), Math.max(1, Math.ceil(panels[platform].jobsTotal / pageSize)))
  void loadPlatform(platform)
}

function closePreviewStream() {
  if (previewSource) {
    previewSource.close()
    previewSource = null
  }
}

async function syncPreviewStream(platform: Platform | null, section: SectionView) {
  closePreviewStream()
  if (!platform || section !== 'live') return
  try {
    panels[platform].preview = await fetchPreviewState(platform)
  } catch {
    return
  }
  previewSource = new EventSource(previewStreamUrl(platform))
  previewSource.onmessage = (event) => {
    try {
      panels[platform].preview = JSON.parse(event.data) as PreviewState
    } catch {
      // ignore malformed payload
    }
  }
  previewSource.onerror = () => closePreviewStream()
}

async function initialize() {
  if (initialized) return
  initialized = true
  resetAccountForm()
  applyStoredForms()
  await checkBackendHealth()
  await refreshAll()
  pollTimer = window.setInterval(refreshAll, 3000)
  healthPollTimer = window.setInterval(() => {
    void checkBackendHealth(true)
  }, 5000)
}

function dispose() {
  closePreviewStream()
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
  if (healthPollTimer) {
    window.clearInterval(healthPollTimer)
    healthPollTimer = undefined
  }
  initialized = false
}

export function useJobHelpStore() {
  const accountSummary = computed(() =>
    platforms.map((platform) => ({
      platform,
      total: allAccounts.value.filter((account) => account.platform === platform).length,
      defaultAccount: allAccounts.value.find((account) => account.platform === platform && account.is_default) ?? null,
    })),
  )
  const filteredAccounts = computed(() => {
    if (accountFilter.value === 'all') return allAccounts.value
    return allAccounts.value.filter((account) => account.platform === accountFilter.value)
  })

  return {
    API_ORIGIN,
    meta,
    platforms,
    pageSize,
    panels,
    forms,
    formStateMeta,
    dashboardFilters,
    draftInputs,
    pagination,
    busy,
    allAccounts,
    errorMessage,
    actionMessage,
    accountFilter,
    filteredAccounts,
    accountSummary,
    editingAccountId,
    savingAccount,
    browserConfigLoading,
    browserConfigAccountId,
    imageUploading,
    imagePreviewUrls,
    backendServiceStatus,
    backendServiceMessage,
    accountForm,
    confirmDialog,
    toasts,
    accountDisplayName,
    formatTime,
    toneForStatus,
    statusLabel,
    displayReason,
    addTag,
    removeTag,
    applyDefaultCompanyBlacklist,
    clearCompanyBlacklist,
  clearJobTitleBlacklist,
    handleImageSelected,
    saveFormConfig,
    exportFormConfig,
    importFormConfig,
    applyTaskRunConfig,
    applyDashboardFilters,
    resetDashboardFilters,
    initialize,
    dispose,
    refreshAll,
    loadPlatform,
    loadAccounts,
    handleStart,
    handleStop,
    handleConfirmLogin,
    promptLoginConfirmation,
    changeJobsPage,
    startCreateAccount,
    startEditAccount,
    configureAccountBrowser,
    openAccountBrowserForSite,
    promptConfigureCreatedAccount,
    submitAccount,
    handleSetDefault,
    resolveConfirmDialog,
    syncPreviewStream,
    closePreviewStream,
    pushToast,
  }
}
