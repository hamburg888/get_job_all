<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Alert, Button, Card, Checkbox, Empty, Input, Select, Skeleton, Tag, Timeline } from 'ant-design-vue'
import { EditOutlined, ExclamationCircleOutlined, ImportOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons-vue'

import { fetchResumeCenter, importResume, syncResume, updateMasterResume } from '../api'
import { useJobHelpStore } from '../composables/useJobHelpStore'
import type { MasterResume, MasterResumeUpdatePayload, Platform, ResumeCenterData, ResumeSyncFieldSelection } from '../types'

const { TextArea } = Input

const store = useJobHelpStore()
const resumeSyncPlatforms: Platform[] = ['liepin', 'boss', 'zhilian', 'qiancheng']
const loading = ref(false)
const importLoading = ref(false)
const syncLoading = ref(false)
const saveLoading = ref(false)
const editingResume = ref(false)
const center = ref<ResumeCenterData | null>(null)
const errorMessage = ref('')
const tagDraft = reactive({
  skill: '',
  certificate: '',
  language: '',
})
const profileDraft = reactive({
  experienceText: '',
  ageText: '',
  degreeText: '',
  jobStatusText: '',
  arrivalText: '',
  contactName: '',
  phone: '',
  email: '',
  wechat: '',
})

const form = reactive<{
  sourcePlatform: Platform
  sourceAccountId: number | null
  targetPlatforms: Platform[]
}>({
  sourcePlatform: 'liepin',
  sourceAccountId: null,
  targetPlatforms: ['boss', 'zhilian', 'qiancheng'],
})
const targetAccountMap = reactive<Record<Platform, number | null>>({
  boss: null,
  zhilian: null,
  qiancheng: null,
  liepin: null,
  yupao: null,
})
const syncFieldSelection = reactive<ResumeSyncFieldSelection>({
  basic_info: false,
  summary: true,
  job_preferences: true,
  work_experiences: true,
  project_experiences: true,
  education_experiences: true,
  certificates: true,
  skills: true,
  languages: true,
  attachments: true,
})

const masterResume = computed<MasterResume | null>(() => center.value?.master_resume ?? null)
const sourceAccounts = computed(() => store.allAccounts.value.filter((account) => account.platform === form.sourcePlatform))
const sourceAccountOptions = computed(() =>
  sourceAccounts.value.map((account) => ({
    value: account.id,
    label: `${store.accountDisplayName(account)} / 今日${account.today_delivered_count} / 累计${account.total_delivered_count}`,
  })),
)
const sourcePlatformOptions = computed(() =>
  resumeSyncPlatforms.map((platform) => ({
    value: platform,
    label: store.meta[platform].name,
  })),
)
const targetPlatformOptions = computed(() =>
  resumeSyncPlatforms.map((platform) => ({
    value: platform,
    label: store.meta[platform].name,
    support: center.value?.platform_support?.[platform]?.sync ?? false,
  })),
)
const targetAccountOptions = computed(
  () =>
    Object.fromEntries(
      resumeSyncPlatforms.map((platform) => [
        platform,
        store.allAccounts.value
          .filter((account) => account.platform === platform)
          .map((account) => ({
            value: account.id,
            label: `${store.accountDisplayName(account)}${account.is_default ? '（默认）' : ''} / 今日${account.today_delivered_count} / 累计${account.total_delivered_count}`,
          })),
      ]),
    ) as Record<Platform, Array<{ value: number; label: string }>>,
)
const allTargetsConnected = computed(() => targetPlatformOptions.value.every((option) => option.support))
const enabledSyncFieldCount = computed(() => Object.values(syncFieldSelection).filter(Boolean).length)
const syncFieldKeys = [
  'basic_info',
  'summary',
  'job_preferences',
  'work_experiences',
  'project_experiences',
  'education_experiences',
  'certificates',
  'skills',
  'languages',
  'attachments',
] as Array<keyof ResumeSyncFieldSelection>
const selectedTargetAccountSummary = computed(
  () =>
    Object.fromEntries(
      resumeSyncPlatforms.map((platform) => {
        const accountId = targetAccountMap[platform]
        const account = store.allAccounts.value.find((item) => item.id === accountId)
        return [platform, account ? store.accountDisplayName(account) : '默认账号']
      }),
    ) as Record<Platform, string>,
)
const sourceSupportMessage = computed(() => {
  const supported = center.value?.platform_support?.[form.sourcePlatform]?.import ?? false
  return supported ? '读取该平台当前账号已填写好的简历，并覆盖本站唯一主简历。' : '该平台导入暂未接入。'
})
const masterResumeSourceAccountLabel = computed(() => {
  const resume = masterResume.value
  if (!resume) {
    return ''
  }
  if (resume.source_account_id == null) {
    return '默认账号'
  }
  const account = store.allAccounts.value.find((item) => item.id === resume.source_account_id)
  return account ? store.accountDisplayName(account) : `账号 #${resume.source_account_id}`
})
const resumeDraft = reactive<MasterResumeUpdatePayload>(createEmptyResumeDraft())

watch(
  sourceAccounts,
  (accounts) => {
    if (!accounts.length) {
      form.sourceAccountId = null
      return
    }
    if (!accounts.some((account) => account.id === form.sourceAccountId)) {
      form.sourceAccountId = accounts.find((account) => account.is_default)?.id ?? accounts[0]?.id ?? null
    }
  },
  { immediate: true },
)

watch(
  () => store.allAccounts.value,
  (accounts) => {
    for (const platform of resumeSyncPlatforms) {
      const platformAccounts = accounts.filter((account) => account.platform === platform)
      if (!platformAccounts.length) {
        targetAccountMap[platform] = null
        continue
      }
      if (!platformAccounts.some((account) => account.id === targetAccountMap[platform])) {
        targetAccountMap[platform] = platformAccounts.find((account) => account.is_default)?.id ?? platformAccounts[0]?.id ?? null
      }
    }
  },
  { immediate: true },
)

watch(
  masterResume,
  (resume) => {
    if (resume && !editingResume.value) {
      hydrateResumeDraft(resume)
    }
    if (!resume && !editingResume.value) {
      resetResumeDraft()
    }
  },
  { immediate: true },
)

function formatTime(value: string | null | undefined) {
  return store.formatTime(value)
}

function platformName(platform: string) {
  const key = platform as Platform
  return store.meta[key]?.name ?? platform
}

function syncStatusColor(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'running') return 'processing'
  if (status === 'skipped') return 'warning'
  return 'default'
}

function createEmptyResumeDraft(): MasterResumeUpdatePayload {
  return {
    full_name: '',
    headline: '',
    contact_summary: '',
    wechat_id: '',
    summary: '',
    job_preferences: {
      desired_role: '',
      salary: '',
      locations: '',
      job_type: '',
      career_stage: '',
      industry: '',
    },
    work_experiences: [],
    project_experiences: [],
    education_experiences: [],
    certificates: [],
    skills: [],
    languages: [],
    attachments: [],
    raw_snapshot: {},
  }
}

function cloneList<T>(items: T[]) {
  return items.map((item) => ({ ...(item as Record<string, unknown>) })) as T[]
}

function hydrateResumeDraft(resume: MasterResume) {
  const nextDraft = createEmptyResumeDraft()
  nextDraft.full_name = String(resume.full_name || '')
  nextDraft.headline = String(resume.headline || '')
  nextDraft.contact_summary = String(resume.contact_summary || '')
  nextDraft.wechat_id = String(resume.wechat_id || '')
  nextDraft.summary = String(resume.summary || '')
  nextDraft.job_preferences = {
    ...nextDraft.job_preferences,
    ...(resume.job_preferences || {}),
  }
  nextDraft.work_experiences = cloneList(resume.work_experiences || [])
  nextDraft.project_experiences = cloneList(resume.project_experiences || [])
  nextDraft.education_experiences = cloneList(resume.education_experiences || [])
  nextDraft.certificates = [...(resume.certificates || [])]
  nextDraft.skills = [...(resume.skills || [])]
  nextDraft.languages = [...(resume.languages || [])]
  nextDraft.attachments = cloneList(resume.attachments || [])
  nextDraft.raw_snapshot = { ...(resume.raw_snapshot || {}) }
  Object.assign(resumeDraft, nextDraft)
  hydrateStructuredProfileDraft(resume)
}

function resetResumeDraft() {
  Object.assign(resumeDraft, createEmptyResumeDraft())
  Object.assign(profileDraft, {
    experienceText: '',
    ageText: '',
    degreeText: '',
    jobStatusText: '',
    arrivalText: '',
    contactName: '',
    phone: '',
    email: '',
    wechat: '',
  })
}

function startEditResume() {
  if (masterResume.value) {
    hydrateResumeDraft(masterResume.value)
  } else {
    resetResumeDraft()
  }
  editingResume.value = true
}

function cancelEditResume() {
  editingResume.value = false
  if (masterResume.value) {
    hydrateResumeDraft(masterResume.value)
  } else {
    resetResumeDraft()
  }
}

function addArrayItem(target: Array<Record<string, unknown>>, template: Record<string, unknown>) {
  target.push({ ...template })
}

function removeArrayItem(target: Array<Record<string, unknown>>, index: number) {
  target.splice(index, 1)
}

function addTag(kind: 'skill' | 'certificate' | 'language') {
  const value = tagDraft[kind].trim()
  if (!value) return
  const list =
    kind === 'skill' ? resumeDraft.skills : kind === 'certificate' ? resumeDraft.certificates : resumeDraft.languages
  if (!list.some((item) => item.toLowerCase() === value.toLowerCase())) {
    list.push(value)
  }
  tagDraft[kind] = ''
}

function removeTag(kind: 'skill' | 'certificate' | 'language', index: number) {
  const list =
    kind === 'skill' ? resumeDraft.skills : kind === 'certificate' ? resumeDraft.certificates : resumeDraft.languages
  list.splice(index, 1)
}

function splitTokens(value: string) {
  return String(value || '')
    .split('/')
    .map((item) => item.trim())
    .filter(Boolean)
}

function hydrateStructuredProfileDraft(resume: MasterResume) {
  const structured = (resume.raw_snapshot?.manual_profile || {}) as Record<string, unknown>
  const headlineParts = splitTokens(resume.headline || '')
  const contactParts = String(resume.contact_summary || '')
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean)

  profileDraft.experienceText = String(structured.experience_text || headlineParts.find((item) => /年经验|应届|经验/.test(item)) || '')
  profileDraft.ageText = String(structured.age_text || headlineParts.find((item) => /岁/.test(item)) || '')
  profileDraft.degreeText = String(structured.degree_text || headlineParts.find((item) => /本科|大专|硕士|博士|中专|高中/.test(item)) || '')
  profileDraft.jobStatusText = String(structured.job_status_text || headlineParts.find((item) => /在校|离职|求职|在职|应届/.test(item)) || '')
  profileDraft.arrivalText = String(structured.arrival_text || headlineParts.find((item) => /到岗|周内|月内|随时/.test(item)) || '')
  profileDraft.contactName = String(structured.contact_name || contactParts.find((item) => !/@/.test(item) && !/\d{3}/.test(item)) || '')
  profileDraft.phone = String(structured.phone || contactParts.find((item) => /\d{3}/.test(item)) || '')
  profileDraft.email = String(structured.email || contactParts.find((item) => /@/.test(item)) || '')
  profileDraft.wechat = String(structured.wechat || resume.wechat_id || '')
}

function buildHeadlineFromProfileDraft() {
  return [
    profileDraft.experienceText,
    profileDraft.ageText,
    profileDraft.degreeText,
    profileDraft.jobStatusText,
    profileDraft.arrivalText,
  ]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' / ')
}

function buildContactSummaryFromProfileDraft() {
  return [profileDraft.contactName, profileDraft.phone, profileDraft.email]
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' ')
}

async function loadCenter() {
  loading.value = true
  errorMessage.value = ''
  try {
    center.value = await fetchResumeCenter()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取简历中心失败'
  } finally {
    loading.value = false
  }
}

async function handleImport() {
  importLoading.value = true
  errorMessage.value = ''
  try {
    center.value = await importResume({
      source_platform: form.sourcePlatform,
      account_id: form.sourceAccountId,
      headless_enabled: false,
    })
    store.pushToast(`${platformName(form.sourcePlatform)}简历已同步到本站`, 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入简历失败'
    store.pushToast(errorMessage.value, 'danger')
  } finally {
    importLoading.value = false
  }
}

async function handleSync() {
  if (!enabledSyncFieldCount.value) {
    errorMessage.value = '请至少勾选一项需要同步的简历内容'
    store.pushToast(errorMessage.value, 'danger')
    return
  }
  syncLoading.value = true
  errorMessage.value = ''
  try {
    const accountMap = Object.fromEntries(
      form.targetPlatforms.map((platform) => [platform, targetAccountMap[platform] ?? null]),
    )
    center.value = await syncResume({
      target_platforms: form.targetPlatforms,
      account_map: accountMap,
      field_selection: { ...syncFieldSelection },
    })
    store.pushToast('已创建主简历分发任务，请查看下方同步日志', 'info')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '同步简历失败'
    store.pushToast(errorMessage.value, 'danger')
  } finally {
    syncLoading.value = false
  }
}

async function handleSaveResume() {
  saveLoading.value = true
  errorMessage.value = ''
  try {
    const headline = buildHeadlineFromProfileDraft()
    const contactSummary = buildContactSummaryFromProfileDraft()
    const nextRawSnapshot = {
      ...(resumeDraft.raw_snapshot || {}),
      manual_profile: {
        experience_text: profileDraft.experienceText.trim(),
        age_text: profileDraft.ageText.trim(),
        degree_text: profileDraft.degreeText.trim(),
        job_status_text: profileDraft.jobStatusText.trim(),
        arrival_text: profileDraft.arrivalText.trim(),
        contact_name: profileDraft.contactName.trim(),
        phone: profileDraft.phone.trim(),
        email: profileDraft.email.trim(),
        wechat: profileDraft.wechat.trim(),
      },
    }
    center.value = await updateMasterResume({
      ...resumeDraft,
      full_name: resumeDraft.full_name.trim(),
      headline,
      contact_summary: contactSummary,
      wechat_id: profileDraft.wechat.trim(),
      summary: resumeDraft.summary.trim(),
      certificates: resumeDraft.certificates.map((item) => item.trim()).filter(Boolean),
      skills: resumeDraft.skills.map((item) => item.trim()).filter(Boolean),
      languages: resumeDraft.languages.map((item) => item.trim()).filter(Boolean),
      raw_snapshot: nextRawSnapshot,
    })
    editingResume.value = false
    store.pushToast('本站主简历已保存', 'success')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存主简历失败'
    store.pushToast(errorMessage.value, 'danger')
  } finally {
    saveLoading.value = false
  }
}

onMounted(async () => {
  await loadCenter()
})

function syncFieldLabel(key: keyof ResumeSyncFieldSelection) {
  const labels: Record<keyof ResumeSyncFieldSelection, string> = {
    basic_info: '个人基础信息',
    summary: '优势亮点',
    job_preferences: '求职期望',
    work_experiences: '工作 / 实习经历',
    project_experiences: '项目经历',
    education_experiences: '教育经历',
    certificates: '证书',
    skills: '技能',
    languages: '语言',
    attachments: '附件',
  }
  return labels[key]
}
</script>

<template>
  <section class="resume-sync-shell">
    <Card :bordered="false" class="platform-hero-card">
      <template #title>
        <h1 class="platform-hero-title">简历同步</h1>
      </template>
      <template #extra>
        <div class="platform-hero-actions">
          <Button @click="loadCenter">刷新数据</Button>
        </div>
      </template>

      <div class="platform-hero-layout">
        <div class="platform-hero-copy">
          <div class="platform-hero-heading">
            <h2 class="platform-hero-section">从平台现有简历生成本站主简历，再向其他平台分发</h2>
            <ExclamationCircleOutlined class="platform-hero-inline-tip" />
          </div>
          <p class="resume-hero-copy">
            当前主流程是“来源平台单选导入 + 目标平台多选分发”。
          </p>
        </div>

        <div class="platform-hero-account resume-hero-summary">
          <span class="platform-hero-label">当前主简历</span>
          <strong>{{ masterResume?.full_name || '尚未导入' }}</strong>
          <p>{{ masterResume?.source_label || '请选择来源平台账号并导入' }}</p>
          <div class="platform-hero-metrics">
            <span>技能 {{ masterResume?.skills.length ?? 0 }}</span>
            <span>经历 {{ masterResume?.work_experiences.length ?? 0 }}</span>
            <span>项目 {{ masterResume?.project_experiences.length ?? 0 }}</span>
          </div>
        </div>
      </div>
    </Card>

    <Alert v-if="errorMessage" class="resume-alert" type="error" show-icon :message="errorMessage" />

    <div class="resume-sync-grid">
      <div class="resume-sync-main">
        <Card :bordered="false" class="resume-card">
          <template #title>{{ editingResume ? '编辑主简历' : '主简历预览' }}</template>
          <template #extra>
            <div class="resume-card-actions">
              <Button v-if="!editingResume" @click="startEditResume">
                <template #icon><EditOutlined /></template>
                编辑
              </Button>
              <template v-else>
                <Button @click="cancelEditResume">取消</Button>
                <Button type="primary" :loading="saveLoading" @click="handleSaveResume">保存</Button>
              </template>
            </div>
          </template>

          <Skeleton v-if="loading" active :paragraph="{ rows: 10 }" />
          <div v-else-if="editingResume" class="resume-edit-stack">
            <section class="resume-preview-section">
              <h4>基础信息</h4>
              <div class="resume-form-grid">
                <label class="resume-field">
                  <span>姓名</span>
                  <Input v-model:value="resumeDraft.full_name" placeholder="请输入姓名" />
                </label>
                <label class="resume-field">
                  <span>经验</span>
                  <Input v-model:value="profileDraft.experienceText" placeholder="如：2.5年经验 / 应届生" />
                </label>
                <label class="resume-field">
                  <span>年龄</span>
                  <Input v-model:value="profileDraft.ageText" placeholder="如：24岁" />
                </label>
                <label class="resume-field">
                  <span>学历</span>
                  <Input v-model:value="profileDraft.degreeText" placeholder="如：本科" />
                </label>
                <label class="resume-field">
                  <span>求职状态</span>
                  <Input v-model:value="profileDraft.jobStatusText" placeholder="如：在校生 / 离职" />
                </label>
                <label class="resume-field">
                  <span>到岗时间</span>
                  <Input v-model:value="profileDraft.arrivalText" placeholder="如：周内到岗 / 随时到岗" />
                </label>
                <label class="resume-field resume-field-full">
                  <span>标题预览</span>
                  <Input :value="buildHeadlineFromProfileDraft()" readonly />
                </label>
                <label class="resume-field">
                  <span>联系人 / 昵称</span>
                  <Input v-model:value="profileDraft.contactName" placeholder="如：GSYingxiong" />
                </label>
                <label class="resume-field">
                  <span>手机号</span>
                  <Input v-model:value="profileDraft.phone" placeholder="如：189****2008" />
                </label>
                <label class="resume-field">
                  <span>邮箱</span>
                  <Input v-model:value="profileDraft.email" placeholder="如：demo@qq.com" />
                </label>
                <label class="resume-field">
                  <span>微信</span>
                  <Input v-model:value="profileDraft.wechat" placeholder="选填" />
                </label>
                <label class="resume-field resume-field-full">
                  <span>联系方式预览</span>
                  <Input :value="buildContactSummaryFromProfileDraft()" readonly />
                </label>
              </div>
            </section>

            <section class="resume-preview-section">
              <h4>优势亮点</h4>
              <TextArea v-model:value="resumeDraft.summary" :rows="5" placeholder="请输入个人优势、自我评价、亮点经历" />
            </section>

            <section class="resume-preview-section">
              <h4>求职期望</h4>
              <div class="resume-form-grid">
                <label class="resume-field">
                  <span>方向</span>
                  <Input v-model:value="resumeDraft.job_preferences.desired_role" placeholder="如：Java / 前端开发 / 全栈开发" />
                </label>
                <label class="resume-field">
                  <span>薪资</span>
                  <Input v-model:value="resumeDraft.job_preferences.salary" placeholder="如：12-18K" />
                </label>
                <label class="resume-field">
                  <span>城市</span>
                  <Input v-model:value="resumeDraft.job_preferences.locations" placeholder="如：广州,深圳" />
                </label>
                <label class="resume-field">
                  <span>工作性质</span>
                  <Input v-model:value="resumeDraft.job_preferences.job_type" placeholder="如：全职 / 实习" />
                </label>
                <label class="resume-field">
                  <span>阶段</span>
                  <Input v-model:value="resumeDraft.job_preferences.career_stage" placeholder="如：离职-随时到岗" />
                </label>
                <label class="resume-field">
                  <span>行业</span>
                  <Input v-model:value="resumeDraft.job_preferences.industry" placeholder="如：互联网 / 计算机软件" />
                </label>
              </div>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-headline">
                <h4>工作 / 实习经历</h4>
                <Button type="dashed" @click="addArrayItem(resumeDraft.work_experiences, { company: '', title: '', employment_type: '', date_range: '', description: '' })">
                  <template #icon><PlusOutlined /></template>
                  新增经历
                </Button>
              </div>
              <div v-if="resumeDraft.work_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in resumeDraft.work_experiences" :key="`edit-work-${index}`" class="resume-entry-card resume-edit-card">
                  <div class="resume-section-headline">
                    <strong>经历 {{ index + 1 }}</strong>
                    <Button danger size="small" @click="removeArrayItem(resumeDraft.work_experiences, index)">删除</Button>
                  </div>
                  <div class="resume-form-grid">
                    <label class="resume-field">
                      <span>公司</span>
                      <Input v-model:value="item.company" />
                    </label>
                    <label class="resume-field">
                      <span>岗位</span>
                      <Input v-model:value="item.title" />
                    </label>
                    <label class="resume-field">
                      <span>类型</span>
                      <Input v-model:value="item.employment_type" placeholder="如：全职 / 实习" />
                    </label>
                    <label class="resume-field">
                      <span>时间</span>
                      <Input v-model:value="item.date_range" placeholder="如：2024.01-2025.03" />
                    </label>
                    <label class="resume-field resume-field-full">
                      <span>描述</span>
                      <TextArea v-model:value="item.description" :rows="4" />
                    </label>
                  </div>
                </article>
              </div>
              <Empty v-else description="暂无工作经历，点击上方按钮新增" />
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-headline">
                <h4>项目经历</h4>
                <Button type="dashed" @click="addArrayItem(resumeDraft.project_experiences, { name: '', role: '', date_range: '', description: '' })">
                  <template #icon><PlusOutlined /></template>
                  新增项目
                </Button>
              </div>
              <div v-if="resumeDraft.project_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in resumeDraft.project_experiences" :key="`edit-project-${index}`" class="resume-entry-card resume-edit-card">
                  <div class="resume-section-headline">
                    <strong>项目 {{ index + 1 }}</strong>
                    <Button danger size="small" @click="removeArrayItem(resumeDraft.project_experiences, index)">删除</Button>
                  </div>
                  <div class="resume-form-grid">
                    <label class="resume-field">
                      <span>项目名</span>
                      <Input v-model:value="item.name" />
                    </label>
                    <label class="resume-field">
                      <span>角色</span>
                      <Input v-model:value="item.role" />
                    </label>
                    <label class="resume-field">
                      <span>时间</span>
                      <Input v-model:value="item.date_range" />
                    </label>
                    <label class="resume-field resume-field-full">
                      <span>描述</span>
                      <TextArea v-model:value="item.description" :rows="4" />
                    </label>
                  </div>
                </article>
              </div>
              <Empty v-else description="暂无项目经历，点击上方按钮新增" />
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-headline">
                <h4>教育经历</h4>
                <Button type="dashed" @click="addArrayItem(resumeDraft.education_experiences, { school: '', study_mode: '', degree_major: '', degree: '', major: '', date_range: '', description: '' })">
                  <template #icon><PlusOutlined /></template>
                  新增教育
                </Button>
              </div>
              <div v-if="resumeDraft.education_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in resumeDraft.education_experiences" :key="`edit-edu-${index}`" class="resume-entry-card resume-edit-card">
                  <div class="resume-section-headline">
                    <strong>教育 {{ index + 1 }}</strong>
                    <Button danger size="small" @click="removeArrayItem(resumeDraft.education_experiences, index)">删除</Button>
                  </div>
                  <div class="resume-form-grid">
                    <label class="resume-field">
                      <span>学校</span>
                      <Input v-model:value="item.school" />
                    </label>
                    <label class="resume-field">
                      <span>学制/形式</span>
                      <Input v-model:value="item.study_mode" placeholder="如：本科 / 全日制" />
                    </label>
                    <label class="resume-field">
                      <span>专业/学历</span>
                      <Input v-model:value="item.degree_major" placeholder="如：软件工程 / 本科" />
                    </label>
                    <label class="resume-field">
                      <span>时间</span>
                      <Input v-model:value="item.date_range" />
                    </label>
                    <label class="resume-field resume-field-full">
                      <span>描述</span>
                      <TextArea v-model:value="item.description" :rows="3" />
                    </label>
                  </div>
                </article>
              </div>
              <Empty v-else description="暂无教育经历，点击上方按钮新增" />
            </section>

            <section class="resume-preview-section">
              <h4>技能 / 证书 / 语言</h4>
              <div class="resume-tag-edit-grid">
                <div class="resume-tag-editor">
                  <span>技能标签</span>
                  <div class="resume-tag-input-row">
                    <Input v-model:value="tagDraft.skill" placeholder="输入后点击添加" @pressEnter="addTag('skill')" />
                    <Button @click="addTag('skill')">添加</Button>
                  </div>
                  <div class="resume-chip-list">
                    <Tag v-for="(item, index) in resumeDraft.skills" :key="`skill-${item}-${index}`" closable color="cyan" @close.prevent="removeTag('skill', index)">{{ item }}</Tag>
                  </div>
                </div>
                <div class="resume-tag-editor">
                  <span>证书标签</span>
                  <div class="resume-tag-input-row">
                    <Input v-model:value="tagDraft.certificate" placeholder="输入后点击添加" @pressEnter="addTag('certificate')" />
                    <Button @click="addTag('certificate')">添加</Button>
                  </div>
                  <div class="resume-chip-list">
                    <Tag v-for="(item, index) in resumeDraft.certificates" :key="`cert-${item}-${index}`" closable color="gold" @close.prevent="removeTag('certificate', index)">{{ item }}</Tag>
                  </div>
                </div>
                <div class="resume-tag-editor">
                  <span>语言标签</span>
                  <div class="resume-tag-input-row">
                    <Input v-model:value="tagDraft.language" placeholder="输入后点击添加" @pressEnter="addTag('language')" />
                    <Button @click="addTag('language')">添加</Button>
                  </div>
                  <div class="resume-chip-list">
                    <Tag v-for="(item, index) in resumeDraft.languages" :key="`lang-${item}-${index}`" closable color="green" @close.prevent="removeTag('language', index)">{{ item }}</Tag>
                  </div>
                </div>
              </div>
            </section>
          </div>
          <div v-else-if="masterResume" class="resume-preview-stack">
            <section class="resume-preview-section">
              <div class="resume-preview-head">
                <div>
                  <h3>{{ masterResume.full_name || '未命名主简历' }}</h3>
                  <p>{{ masterResume.headline || '暂无概述' }}</p>
                </div>
                <div class="resume-badges">
                  <Tag color="blue">{{ platformName(masterResume.source_platform) }}</Tag>
                  <Tag v-if="masterResumeSourceAccountLabel">{{ masterResumeSourceAccountLabel }}</Tag>
                  <Tag>{{ formatTime(masterResume.updated_at) }}</Tag>
                </div>
              </div>
              <div class="resume-sync-toggle-row">
                <Checkbox v-model:checked="syncFieldSelection.basic_info">{{ syncFieldLabel('basic_info') }}</Checkbox>
              </div>
              <p class="resume-contact">{{ masterResume.contact_summary || '暂无联系方式摘要' }}</p>
              <p v-if="masterResume.wechat_id" class="resume-contact">微信：{{ masterResume.wechat_id }}</p>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>优势亮点</h4>
                <Checkbox v-model:checked="syncFieldSelection.summary">{{ syncFieldLabel('summary') }}</Checkbox>
              </div>
              <p>{{ masterResume.summary || '暂无内容' }}</p>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>求职期望</h4>
                <Checkbox v-model:checked="syncFieldSelection.job_preferences">{{ syncFieldLabel('job_preferences') }}</Checkbox>
              </div>
              <div class="resume-meta-grid">
                <div class="resume-meta-card">
                  <span>方向</span>
                  <strong>{{ String(masterResume.job_preferences.desired_role || '-') }}</strong>
                </div>
                <div class="resume-meta-card">
                  <span>薪资</span>
                  <strong>{{ String(masterResume.job_preferences.salary || '-') }}</strong>
                </div>
                <div class="resume-meta-card">
                  <span>城市</span>
                  <strong>{{ String(masterResume.job_preferences.locations || '-') }}</strong>
                </div>
                <div class="resume-meta-card">
                  <span>阶段</span>
                  <strong>{{ String(masterResume.job_preferences.career_stage || '-') }}</strong>
                </div>
              </div>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>工作 / 实习经历</h4>
                <Checkbox v-model:checked="syncFieldSelection.work_experiences">{{ syncFieldLabel('work_experiences') }}</Checkbox>
              </div>
              <div v-if="masterResume.work_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in masterResume.work_experiences" :key="`work-${index}`" class="resume-entry-card">
                  <div class="resume-entry-head">
                    <strong>{{ String(item.company || '-') }}</strong>
                    <span>{{ String(item.date_range || '-') }}</span>
                  </div>
                  <p>{{ String(item.title || '-') }}{{ item.employment_type ? ` / ${String(item.employment_type)}` : '' }}</p>
                  <p>{{ String(item.description || '-') }}</p>
                </article>
              </div>
              <Empty v-else description="暂无经历数据" />
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>项目经历</h4>
                <Checkbox v-model:checked="syncFieldSelection.project_experiences">{{ syncFieldLabel('project_experiences') }}</Checkbox>
              </div>
              <div v-if="masterResume.project_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in masterResume.project_experiences" :key="`project-${index}`" class="resume-entry-card">
                  <div class="resume-entry-head">
                    <strong>{{ String(item.name || '-') }}</strong>
                    <span>{{ String(item.date_range || '-') }}</span>
                  </div>
                  <p>{{ String(item.role || '-') }}</p>
                  <p>{{ String(item.description || '-') }}</p>
                </article>
              </div>
              <Empty v-else description="暂无项目数据" />
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>教育经历</h4>
                <Checkbox v-model:checked="syncFieldSelection.education_experiences">{{ syncFieldLabel('education_experiences') }}</Checkbox>
              </div>
              <div v-if="masterResume.education_experiences.length" class="resume-entry-list">
                <article v-for="(item, index) in masterResume.education_experiences" :key="`education-${index}`" class="resume-entry-card">
                  <div class="resume-entry-head">
                    <strong>{{ String(item.school || '-') }}</strong>
                    <span>{{ String(item.date_range || '-') }}</span>
                  </div>
                  <p>{{ String(item.study_mode || '-') }} / {{ String(item.degree_major || '-') }}</p>
                  <p>{{ String(item.description || '-') }}</p>
                </article>
              </div>
              <Empty v-else description="暂无教育数据" />
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>技能标签</h4>
                <Checkbox v-model:checked="syncFieldSelection.skills">{{ syncFieldLabel('skills') }}</Checkbox>
              </div>
              <div class="resume-chip-list">
                <Tag v-for="skill in masterResume.skills" :key="skill" color="cyan">{{ skill }}</Tag>
              </div>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>证书标签</h4>
                <Checkbox v-model:checked="syncFieldSelection.certificates">{{ syncFieldLabel('certificates') }}</Checkbox>
              </div>
              <div class="resume-chip-list">
                <Tag v-for="certificate in masterResume.certificates" :key="certificate" color="gold">{{ certificate }}</Tag>
              </div>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>语言标签</h4>
                <Checkbox v-model:checked="syncFieldSelection.languages">{{ syncFieldLabel('languages') }}</Checkbox>
              </div>
              <div class="resume-chip-list">
                <Tag v-for="language in masterResume.languages" :key="language" color="green">{{ language }}</Tag>
              </div>
            </section>

            <section class="resume-preview-section">
              <div class="resume-section-toggle">
                <h4>附件</h4>
                <Checkbox v-model:checked="syncFieldSelection.attachments">{{ syncFieldLabel('attachments') }}</Checkbox>
              </div>
              <div v-if="masterResume.attachments.length" class="resume-attachment-list">
                <div v-for="(attachment, index) in masterResume.attachments" :key="`attachment-${index}`" class="resume-attachment-card">
                  <strong>{{ String(attachment.name || '-') }}</strong>
                  <span>{{ String(attachment.meta || '-') }}</span>
                </div>
              </div>
            </section>
          </div>
          <Empty v-else description="还没有主简历，先从来源平台导入一份吧" />
        </Card>
      </div>

      <div class="resume-sync-side">
        <Card :bordered="false" class="resume-card">
          <template #title>导入来源</template>
          <div class="resume-form-stack">
            <label class="resume-field">
              <span>来源平台</span>
              <Select v-model:value="form.sourcePlatform" :options="sourcePlatformOptions" />
            </label>

            <label class="resume-field">
              <span>来源账号</span>
              <Select v-model:value="form.sourceAccountId" :options="sourceAccountOptions" :allow-clear="true" />
            </label>

            <Alert :type="(center?.platform_support?.[form.sourcePlatform]?.import ?? false) ? 'info' : 'warning'" show-icon :message="sourceSupportMessage" />

            <Button type="primary" :loading="importLoading" @click="handleImport">
              <template #icon><ImportOutlined /></template>
              同步到本站
            </Button>
          </div>
        </Card>

        <Card :bordered="false" class="resume-card">
          <template #title>同步平台</template>
          <div class="resume-form-stack">
            <div class="resume-sync-selection-card">
              <div class="resume-section-headline">
                <strong>同步内容</strong>
                <Tag color="blue">已勾选 {{ enabledSyncFieldCount }} 项</Tag>
              </div>
              <p class="resume-selection-tip">只有勾中的内容才会写回目标平台。个人基础信息默认不勾选，其余默认全部勾选。</p>
              <div class="resume-sync-selection-grid">
                <label v-for="key in syncFieldKeys" :key="key" class="resume-sync-selection-item">
                  <Checkbox v-model:checked="syncFieldSelection[key]">{{ syncFieldLabel(key) }}</Checkbox>
                </label>
              </div>
            </div>

            <Checkbox.Group v-model:value="form.targetPlatforms" class="resume-target-list">
              <div v-for="option in targetPlatformOptions" :key="option.value" class="resume-target-item">
                <div class="resume-target-topline">
                  <Checkbox :value="option.value">
                    {{ option.label }}
                  </Checkbox>
                  <Tag :color="option.support ? 'success' : 'default'">{{ option.support ? '已接入' : '开发中' }}</Tag>
                </div>
                <label class="resume-target-account-field">
                  <span>同步账号</span>
                  <Select
                    v-model:value="targetAccountMap[option.value]"
                    :options="targetAccountOptions[option.value]"
                    :disabled="!form.targetPlatforms.includes(option.value) || !(targetAccountOptions[option.value]?.length)"
                    :placeholder="targetAccountOptions[option.value]?.length ? '默认账号' : '暂无可用账号'"
                    :allow-clear="false"
                  />
                  <small v-if="form.targetPlatforms.includes(option.value)" class="resume-target-account-tip">
                    当前将同步到：{{ selectedTargetAccountSummary[option.value] }}
                  </small>
                </label>
              </div>
            </Checkbox.Group>

            <!-- <Alert
              :type="allTargetsConnected ? 'success' : 'warning'"
              show-icon
              :message="
                allTargetsConnected
                  ? '当前平台均已接入真实写回同步；每个平台可单独指定目标账号，不手动调整时默认使用该平台默认账号。'
                  : '部分平台尚未接入真实写回同步，请以目标列表状态为准。'
              "
            /> -->

            <Button :loading="syncLoading" @click="handleSync">
              <template #icon><SyncOutlined /></template>
              开始同步
            </Button>
          </div>
        </Card>

        <Card :bordered="false" class="resume-card resume-log-card">
          <template #title>导入日志</template>
          <div v-if="center?.import_logs.length" class="resume-import-log-list">
            <Timeline>
              <Timeline.Item v-for="log in center?.import_logs" :key="log.id" :color="syncStatusColor(log.status)">
                <strong>{{ platformName(log.source_platform) }}</strong>
                <p>{{ log.message }}</p>
                <span>{{ formatTime(log.created_at) }}</span>
              </Timeline.Item>
            </Timeline>
          </div>
          <Empty v-else description="暂无导入记录" />
        </Card>

        <Card :bordered="false" class="resume-card resume-log-card">
          <template #title>同步日志</template>
          <div v-if="center?.sync_runs.length" class="resume-run-list resume-sync-log-list">
            <article v-for="run in center?.sync_runs" :key="run.id" class="resume-run-card">
              <div class="resume-entry-head">
                <strong>{{ run.message }}</strong>
                <Tag :color="syncStatusColor(run.status)">{{ run.status }}</Tag>
              </div>
              <p>{{ formatTime(run.created_at) }}</p>
              <div class="resume-chip-list">
                <Tag v-for="target in run.targets" :key="target.id" :color="syncStatusColor(target.status)">
                  {{ platformName(target.target_platform) }} / {{ target.status }}
                </Tag>
              </div>
            </article>
          </div>
          <Empty v-else description="暂无同步记录" />
        </Card>
      </div>
    </div>
  </section>
</template>

<style scoped>
.resume-sync-shell {
  display: grid;
  gap: 18px;
}

.resume-alert {
  border-radius: 18px;
}

.resume-hero-copy {
  margin: 0;
  color: #64748b;
  line-height: 1.7;
}

.resume-sync-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.7fr);
  gap: 18px;
  align-items: start;
}

.resume-sync-main,
.resume-sync-side,
.resume-preview-stack,
.resume-form-stack,
.resume-run-list,
.resume-entry-list {
  display: grid;
  gap: 16px;
}

.resume-card {
  border-radius: 26px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.resume-log-card :deep(.ant-card-body) {
  display: flex;
  flex-direction: column;
}

.resume-card-actions,
.resume-section-headline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.resume-edit-stack,
.resume-tag-edit-grid,
.resume-tag-editor {
  display: grid;
  gap: 16px;
}

.resume-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.resume-field-full {
  grid-column: 1 / -1;
}

.resume-preview-section {
  display: grid;
  gap: 12px;
}

.resume-section-toggle,
.resume-sync-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.resume-preview-head,
.resume-entry-head,
.resume-target-item,
.resume-target-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.resume-preview-head h3,
.resume-preview-section h4,
.resume-entry-card p,
.resume-run-card p {
  margin: 0;
}

.resume-preview-head p {
  margin: 6px 0 0;
  color: #64748b;
}

.resume-badges,
.resume-chip-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.resume-source-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.resume-contact {
  margin: 0;
  color: #475569;
}

.resume-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.resume-meta-card,
.resume-entry-card,
.resume-attachment-card,
.resume-run-card {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(39, 88, 158, 0.08);
  background: rgba(255, 255, 255, 0.8);
  display: grid;
  gap: 8px;
}

.resume-meta-card span,
.resume-entry-card span,
.resume-run-card span {
  color: #64748b;
}

.resume-meta-card strong {
  font-size: 1rem;
  letter-spacing: -0.02em;
}

.resume-entry-card p,
.resume-run-card p {
  color: #475569;
  line-height: 1.7;
}

.resume-import-log-list {
  max-height: 180px;
  overflow-y: auto;
  padding-right: 6px;
}

.resume-sync-log-list {
  max-height: 280px;
  overflow-y: auto;
  padding-right: 6px;
}

.resume-edit-card {
  width: 100%;
}

.resume-attachment-list {
  display: grid;
  gap: 10px;
}

.resume-field {
  display: grid;
  gap: 8px;
}

.resume-field > span {
  color: #475569;
  font-weight: 700;
}

.resume-target-list {
  display: grid;
  gap: 10px;
  width: 100%;
}

.resume-target-list :deep(.ant-checkbox-group) {
  display: grid;
  gap: 10px;
  width: 100%;
}

.resume-sync-selection-card {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(39, 88, 158, 0.08);
  background: rgba(247, 250, 255, 0.9);
}

.resume-sync-selection-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 14px;
}

.resume-sync-selection-item {
  min-width: 0;
}

.resume-selection-tip {
  margin: 0;
  color: #64748b;
  line-height: 1.6;
}

.resume-target-item {
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid rgba(39, 88, 158, 0.08);
  background: rgba(255, 255, 255, 0.78);
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  width: 100%;
  box-sizing: border-box;
  align-self: start;
  justify-content: normal;
  align-content: start;
  justify-items: stretch;
  align-items: stretch;
}

.resume-target-account-field {
  display: grid;
  gap: 8px;
  width: 100%;
  min-width: 0;
  justify-self: stretch;
}

.resume-target-account-field > span {
  color: #475569;
  font-weight: 700;
}

.resume-target-topline {
  width: 100%;
  min-width: 0;
  justify-self: stretch;
}

.resume-target-topline :deep(.ant-checkbox-wrapper) {
  flex: 1;
  display: flex;
  align-items: center;
  min-width: 0;
  width: 100%;
}

.resume-target-topline :deep(.ant-checkbox) {
  flex: 0 0 auto;
}

.resume-target-topline :deep(.ant-checkbox + span) {
  flex: 1;
  min-width: 0;
}

.resume-target-account-field :deep(.ant-select) {
  width: 100%;
  min-width: 0;
}

.resume-target-account-field :deep(.ant-select-selector) {
  width: 100%;
}

.resume-tag-input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.resume-target-account-tip {
  color: #64748b;
  line-height: 1.5;
}

@media (max-width: 1240px) {
  .resume-sync-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .resume-meta-grid {
    grid-template-columns: 1fr;
  }

  .resume-form-grid,
  .resume-tag-input-row,
  .resume-sync-selection-grid {
    grid-template-columns: 1fr;
  }

  .resume-preview-head,
  .resume-entry-head,
  .resume-target-item,
  .resume-target-topline,
  .resume-section-toggle,
  .resume-sync-toggle-row,
  .resume-card-actions,
  .resume-section-headline {
    flex-direction: column;
  }
}
</style>
