<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Alert, Button, Card, Collapse, Descriptions, Empty, Form, Image, Input, InputNumber, Select, Switch, Tag, Tooltip, Upload } from 'ant-design-vue'
import { ExclamationCircleOutlined, GlobalOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons-vue'

import { createBossReplyRule, createDefaultBossReplyRules } from '../types'
import type { BossReplyRule, Platform, AccountItem, StartTaskPayload } from '../types'

const FormItem = Form.Item
const TextArea = Input.TextArea
const DescriptionsItem = Descriptions.Item
const CheckableTag = Tag.CheckableTag
const CollapsePanel = Collapse.Panel
const importInput = ref<HTMLInputElement | null>(null)

const bossActiveStatusOptions = [
  { value: '不限', text: '不限' },
  { value: '在线', text: '在线' },
  { value: '刚刚活跃', text: '刚刚活跃' },
  { value: '今日活跃', text: '今日活跃' },
  { value: '3日内活跃', text: '3日内活跃' },
  { value: '本周活跃', text: '本周活跃' },
  { value: '本月活跃', text: '本月活跃' },
  { value: '半年前活跃', text: '半年前活跃' },
] as const

function toggleBossActiveStatus(status: string, currentValues: string[]) {
  if (status === '不限') {
    currentValues.splice(0, currentValues.length, '不限')
    return
  }
  const nextValues = currentValues.filter((item) => item !== '不限')
  const exists = nextValues.includes(status)
  const finalValues = exists ? nextValues.filter((item) => item !== status) : [...nextValues, status]
  currentValues.splice(0, currentValues.length, ...(finalValues.length ? finalValues : ['不限']))
}

const replyRuleDrafts = reactive<Record<string, string>>({})

function addReplyRule(form: StartTaskPayload) {
  form.boss_reply_rules.push(
    createBossReplyRule({
      label: '新规则',
      keywords: [],
      reply_template: '',
    }),
  )
}

function resetReplyRules(form: StartTaskPayload) {
  form.boss_reply_rules.splice(0, form.boss_reply_rules.length, ...createDefaultBossReplyRules())
}

function removeReplyRule(form: StartTaskPayload, ruleId: string) {
  form.boss_reply_rules = form.boss_reply_rules.filter((rule) => rule.id !== ruleId)
  delete replyRuleDrafts[ruleId]
}

function addRuleKeyword(rule: BossReplyRule) {
  const value = (replyRuleDrafts[rule.id] || '').trim()
  if (!value) return
  if (!rule.keywords.includes(value)) {
    rule.keywords.push(value)
  }
  replyRuleDrafts[rule.id] = ''
}

function removeRuleKeyword(rule: BossReplyRule, keyword: string) {
  rule.keywords = rule.keywords.filter((item) => item !== keyword)
}

const props = defineProps<{
  platform: Platform
  form: StartTaskPayload
  accounts: AccountItem[]
  draftInputs: { searchTerm: string; keyword: string; city: string; commonPhraseIndex: string; companyBlacklist: string; jobTitleBlacklist: string }
  accountDisplayName: (account: Pick<AccountItem, 'username' | 'phone'>) => string
  openAccountBrowserLoading: boolean
  openAccountBrowserAccountId: number | null
  imagePreviewUrl: string
  imageUploading: boolean
  formMeta: { dirty: boolean; lastSavedAt: string | null; lastModifiedAt: string | null }
}>()

const emit = defineEmits<{
  (event: 'add-tag', payload: { field: 'search_terms' | 'keywords' | 'cities' | 'company_blacklist' | 'job_title_blacklist' | 'common_phrase_indexes'; source: 'searchTerm' | 'keyword' | 'city' | 'companyBlacklist' | 'jobTitleBlacklist' | 'commonPhraseIndex' }): void
  (event: 'remove-tag', payload: { field: 'search_terms' | 'keywords' | 'cities' | 'company_blacklist' | 'job_title_blacklist' | 'common_phrase_indexes'; value: string | number }): void
  (event: 'apply-default-company-blacklist'): void
  (event: 'clear-company-blacklist'): void
  (event: 'clear-job-title-blacklist'): void
  (event: 'image-selected', file: File | null): void
  (event: 'save-config'): void
  (event: 'export-config'): void
  (event: 'import-config', file: File | null): void
  (event: 'open-account-browser', account: AccountItem | null): void
}>()

const isBoss = computed(() => props.platform === 'boss')
const isQiancheng = computed(() => props.platform === 'qiancheng')
const isLiepin = computed(() => props.platform === 'liepin')
const isYupao = computed(() => props.platform === 'yupao')
const isSearchPlatform = computed(() => isQiancheng.value)
const supportsChat = computed(() => isBoss.value || isLiepin.value || isYupao.value)
const bossChatDerivedEnabled = computed(() => Boolean(
  props.form.use_chat_template
  || props.form.enable_common_phrases
  || props.form.enable_image_resume
  || props.form.auto_agree_wechat_card
  || props.form.enable_follow_up_reply,
))
const chatActionsSummary = computed(() => {
  if (isBoss.value) return bossChatDerivedEnabled.value ? '已按子功能启用' : '未启用任何聊天子功能'
  return props.form.auto_send_chat ? '已开启' : '未开启'
})
const supportsImageResume = computed(() => isBoss.value)
const entrySectionTitle = computed(() => (isSearchPlatform.value ? '账号与搜索' : '账号与入口'))
const entrySectionTooltip = computed(() =>
  isSearchPlatform.value ? '先确定执行账号与搜索词，再决定投递节奏与上限。' : '先确定执行账号和入口链接，再决定投递节奏与上限。',
)
const entryUrlPlaceholder = computed(() => {
  if (isBoss.value) {
    return '粘贴已配置好筛选条件的岗位列表链接，例如 https://www.zhipin.com/web/geek/job?...'
  }
  if (isLiepin.value) {
    return '粘贴猎聘已配置好筛选条件的职位列表链接，例如 https://www.liepin.com/zhaopin/?...'
  }
  if (isYupao.value) {
    return '粘贴鱼泡岗位列表链接，例如 https://www.yupao.com/zhaogong/a76_rmgz/'
  }
  if (isQiancheng.value) {
    return '前程无忧平台会固定从 https://we.51job.com/pc/search 打开，这里无需手动填写'
  }
  return '粘贴已配置好筛选条件的职位列表链接，例如 https://sou.zhaopin.com/?jl=...'
})
const qianchengSearchTerm = computed({
  get: () => props.form.search_terms[0] ?? '',
  set: (value: string) => {
    props.form.search_terms = value ? [value] : []
  },
})
const qianchengSearchPlaceholder = computed(
  () =>
    isLiepin.value
      ? '例如 全栈开发、Java后端、产品经理；留空则打开默认搜索页 https://www.liepin.com/zhaopin/'
      : '例如 java、后端开发、测试工程师；留空则打开默认搜索页 https://we.51job.com/pc/search',
)
const keywordFieldLabel = computed(() => '岗位标题关键词')
const keywordFieldPlaceholder = computed(() =>
  isSearchPlatform.value ? '输入岗位标题筛选词后点击添加，例如 Java、后端、测试' : '输入筛选词后点击添加',
)
const companyBlacklistSummary = computed(() => {
  if (!props.form.enable_company_blacklist) return '未开启'
  return `已开启 / ${props.form.company_blacklist.length} 项`
})
const companyBlacklistChecklistDescription = computed(() =>
  props.form.enable_company_blacklist
    ? `当前已启用公司黑名单，会按公司名称做模糊匹配并跳过命中项；当前共 ${props.form.company_blacklist.length} 项。`
    : '如需屏蔽特定公司，可开启公司黑名单并维护名单。支持模糊匹配，不区分大小写。',
)
const jobTitleBlacklistSummary = computed(() => {
  if (!props.form.enable_job_title_blacklist) return '未开启'
  return `已开启 / ${props.form.job_title_blacklist.length} 项`
})
const jobTitleBlacklistChecklistDescription = computed(() =>
  props.form.enable_job_title_blacklist
    ? `当前已启用岗位标题黑名单，会按岗位标题做模糊匹配并跳过命中项；当前共 ${props.form.job_title_blacklist.length} 项。`
    : '如需屏蔽特定岗位标题，可开启岗位标题黑名单并维护名单。支持模糊匹配，不区分大小写。',
)
const summaryEntryLabel = computed(() => (isSearchPlatform.value ? '搜索词' : '入口链接'))
const summaryEntryValue = computed(() =>
  isSearchPlatform.value ? qianchengSearchTerm.value.trim() || '默认搜索页' : props.form.manual_entry_url ? '已填写' : '待填写',
)
const entryChecklistTitle = computed(() => (isSearchPlatform.value ? '2. 搜索词' : '2. 入口 URL'))
const entryChecklistDescription = computed(() =>
  isLiepin.value
    ? '请直接粘贴猎聘职位列表 URL，建议使用你已经设置好筛选条件的列表页链接，减少运行过程中再次切换筛选。'
    : isYupao.value
    ? '请直接粘贴鱼泡岗位列表 URL。当前接入按手动列表页运行，不走站内搜索。'
    : isQiancheng.value
    ? '可直接填写一个搜索词，例如 java；如果留空，任务会先打开 https://we.51job.com/pc/search，再从默认搜索页继续执行。'
    : '建议使用已设置好筛选条件的岗位列表链接，减少运行中再次切换筛选的波动。'
)
const imageSwitchTitle = computed(() => (isLiepin.value ? '开启简历图片发送' : '开启图片发送'))
const imageSwitchTooltip = computed(() =>
  isLiepin.value
    ? '继续聊打开聊天窗后，会把下方已上传的图片作为简历图片发出去。'
    : '发送时会使用下方已选择的图片文件，例如简历图或作品图。',
)
const imageFieldLabel = computed(() => (isLiepin.value ? '简历图片' : '聊天图片'))
const imageEmptyText = computed(() => (isLiepin.value ? '尚未选择简历图片文件' : '尚未选择图片文件'))
const chatChecklistDescription = computed(() =>
  isLiepin.value
    ? '如果要在继续聊后自动发送简历图片或追加一条消息，请同时检查开关、模板和素材文件。'
    : isYupao.value
    ? '鱼泡先尝试完成沟通动作；如果开启“沟通后自动发消息”，会在聊天输入框可用时补发一条消息。'
    : 'Boss 不再需要总开关：只要开启模板、常用语、图片、同意微信或自动跟进任一功能，就会在投递后执行对应聊天动作。',
)
const replyRuleActiveKeys = ref<string[]>([])
const replyRuleCollapseInitialized = ref(false)

watch(
  () => props.platform,
  () => {
    replyRuleActiveKeys.value = []
    replyRuleCollapseInitialized.value = false
  },
  { immediate: true },
)

watch(
  () => props.form.boss_reply_rules.map((rule) => rule.id),
  (ids, previousIds = []) => {
    const validIds = new Set(ids)
    const nextOpenKeys = replyRuleActiveKeys.value.filter((id) => validIds.has(id))
    if (!replyRuleCollapseInitialized.value) {
      replyRuleActiveKeys.value = nextOpenKeys
      replyRuleCollapseInitialized.value = true
      return
    }

    const addedIds = ids.filter((id) => !previousIds.includes(id))

    for (const id of addedIds) {
      if (!nextOpenKeys.includes(id)) {
        nextOpenKeys.push(id)
      }
    }

    replyRuleActiveKeys.value = nextOpenKeys
  },
  { immediate: true },
)

const accountOptions = computed(() =>
  props.accounts.map((account) => ({
    value: account.id,
    label: `${props.accountDisplayName(account)} / 今日${account.today_delivered_count} / 累计${account.total_delivered_count}`,
  })),
)

const selectedAccount = computed(() => props.accounts.find((item) => item.id === props.form.account_id) ?? null)
const openBrowserButtonLoading = computed(
  () => props.openAccountBrowserLoading && props.openAccountBrowserAccountId === props.form.account_id,
)

function beforeImageUpload(file: File) {
  emit('image-selected', file)
  return false
}

function clearSelectedImage() {
  emit('image-selected', null)
}

function removeClosableTag(
  field: 'search_terms' | 'keywords' | 'cities' | 'company_blacklist' | 'common_phrase_indexes',
  value: string | number,
  event: Event,
) {
  event.preventDefault()
  emit('remove-tag', { field, value })
}

function closeRuleKeyword(rule: BossReplyRule, keyword: string, event: Event) {
  event.preventDefault()
  removeRuleKeyword(rule, keyword)
}

function openImportDialog() {
  importInput.value?.click()
}

function handleImportChange(event: Event) {
  const target = event.target as HTMLInputElement | null
  const file = target?.files?.[0] ?? null
  emit('import-config', file)
  if (target) {
    target.value = ''
  }
}
</script>

<template>
  <section class="boss-config-shell">
    <input ref="importInput" type="file" accept="application/json,.json" class="boss-hidden-input" @change="handleImportChange" />

    <div class="boss-config-main">
      <Card :bordered="false" class="boss-config-card">
        <template #title>
          <div class="boss-card-title">
            <span class="boss-card-kicker">Entry</span>
            <div class="boss-title-heading">
              <h3>{{ entrySectionTitle }}</h3>
              <Tooltip :title="entrySectionTooltip">
                <ExclamationCircleOutlined class="boss-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </template>

        <Form layout="vertical">
          <div class="boss-form-grid boss-form-grid--two">
            <FormItem label="投递账号">
              <div class="boss-account-row">
                <Select
                  v-model:value="form.account_id"
                  :options="accountOptions"
                  placeholder="请选择用于投递的账号"
                  show-search
                  option-filter-prop="label"
                  class="boss-account-select"
                />
                <Tooltip title="使用该账号的 Cookie 打开对应的招聘网站">
                  <Button
                    type="primary"
                    ghost
                    :disabled="!selectedAccount || openBrowserButtonLoading"
                    :loading="openBrowserButtonLoading"
                    @click="selectedAccount && emit('open-account-browser', selectedAccount)"
                  >
                    <template #icon><GlobalOutlined /></template>
                    打开浏览器
                  </Button>
                </Tooltip>
              </div>
            </FormItem>

            <FormItem v-if="isSearchPlatform" label="搜索词" class="boss-span-two">
              <Input
                v-model:value="qianchengSearchTerm"
                :placeholder="qianchengSearchPlaceholder"
              />
            </FormItem>

            <FormItem v-else label="入口 URL" class="boss-span-two">
              <Input
                v-model:value="form.manual_entry_url"
                :placeholder="entryUrlPlaceholder"
              />
            </FormItem>

            <FormItem label="最低薪资 (K)">
              <InputNumber v-model:value="form.salary_min" class="boss-input-number" :min="0" placeholder="例如 5" />
            </FormItem>

            <FormItem label="最高薪资 (K)">
              <InputNumber v-model:value="form.salary_max" class="boss-input-number" :min="0" placeholder="例如 20" />
            </FormItem>

            <FormItem class="boss-span-two">
              <template #label>
                <span class="boss-setting-label">
                  <span>薪资下限放宽</span>
                  <Tooltip title="开启后，若岗位最低薪资低于设定容忍值但最高薪资仍达到要求，则视为可投递。">
                    <ExclamationCircleOutlined class="boss-inline-tip" />
                  </Tooltip>
                </span>
              </template>
              <div class="boss-salary-floor-box">
                <div class="boss-salary-floor-header">
                  <strong>启用薪资下限放宽</strong>
                  <Switch v-model:checked="form.salary_floor_flexible_enabled" />
                </div>
                <div v-if="form.salary_floor_flexible_enabled" class="boss-salary-floor-fields">
                  <div class="boss-salary-floor-field">
                    <span>最低薪资低于此值需判断</span>
                    <InputNumber v-model:value="form.salary_floor_min" class="boss-input-number" :min="0" placeholder="例如 10" />
                    <small>K</small>
                  </div>
                  <div class="boss-salary-floor-field">
                    <span>最高薪资不得低于</span>
                    <InputNumber v-model:value="form.salary_floor_required_max" class="boss-input-number" :min="0" placeholder="例如 15" />
                    <small>K</small>
                  </div>
                </div>
              </div>
            </FormItem>

            <FormItem label="投递间隔 (秒)">
              <InputNumber v-model:value="form.delivery_interval_seconds" class="boss-input-number" :min="0" />
            </FormItem>

            <FormItem label="单次投递成功上限">
              <InputNumber v-model:value="form.max_deliveries_per_run" class="boss-input-number" :min="1" />
            </FormItem>

            <FormItem label="本次任务备注" class="boss-span-two">
              <TextArea
                v-model:value="form.remark"
                :rows="2"
                :maxlength="200"
                show-count
                auto-size
                placeholder="备注本次投递目标或特殊情况，例如：优先深圳南山 Java 岗、避开外包"
              />
            </FormItem>
          </div>
        </Form>
      </Card>

      <Card :bordered="false" class="boss-config-card">
        <template #title>
          <div class="boss-card-title">
            <span class="boss-card-kicker">Delivery</span>
            <div class="boss-title-heading">
              <h3>投递策略</h3>
              <Tooltip title="城市、岗位标题关键词、公司黑名单和活跃状态">
                <ExclamationCircleOutlined class="boss-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </template>

        <Form layout="vertical">
          <div class="boss-form-grid boss-form-grid--single">
            <FormItem label="城市">
              <div class="boss-tag-editor">
                <Input
                  v-model:value="draftInputs.city"
                  placeholder="输入城市后点击添加"
                  @pressEnter="emit('add-tag', { field: 'cities', source: 'city' })"
                />
                <Button @click="emit('add-tag', { field: 'cities', source: 'city' })">
                  <template #icon><PlusOutlined /></template>
                  添加
                </Button>
              </div>
              <div class="boss-tag-list">
                <Tag
                  v-for="item in form.cities"
                  :key="item"
                  closable
                  class="boss-pill-tag"
                  @close="removeClosableTag('cities', item, $event)"
                >
                  {{ item }}
                </Tag>
              </div>
            </FormItem>

            <FormItem>
              <template #label>
                <span class="boss-setting-label">
                  <span>{{ keywordFieldLabel }}</span>
                  <Tooltip
                    v-if="!isSearchPlatform"
                    title='如“Java”将只匹配 Java 相关标题的岗位，其余一律跳过。不添加则投递全部。'
                  >
                    <ExclamationCircleOutlined class="boss-inline-tip" />
                  </Tooltip>
                </span>
              </template>
              <div class="boss-tag-editor">
                <Input
                  v-model:value="draftInputs.keyword"
                  :placeholder="keywordFieldPlaceholder"
                  @pressEnter="emit('add-tag', { field: 'keywords', source: 'keyword' })"
                />
                <Button @click="emit('add-tag', { field: 'keywords', source: 'keyword' })">
                  <template #icon><PlusOutlined /></template>
                  添加
                </Button>
              </div>
              <div class="boss-tag-list">
                <Tag
                  v-for="item in form.keywords"
                  :key="item"
                  closable
                  class="boss-pill-tag"
                  @close="removeClosableTag('keywords', item, $event)"
                >
                  {{ item }}
                </Tag>
              </div>
            </FormItem>

            <FormItem>
              <template #label>
                <span class="boss-setting-label">
                  <span>公司黑名单</span>
                  <Tooltip title="开启后命中的公司会被直接跳过。支持模糊匹配，不区分大小写。">
                    <ExclamationCircleOutlined class="boss-inline-tip" />
                  </Tooltip>
                </span>
              </template>
              <div class="boss-blacklist-box">
                <div class="boss-blacklist-header">
                  <strong>启用黑名单过滤</strong>
                  <Switch v-model:checked="form.enable_company_blacklist" />
                </div>

                <div class="boss-blacklist-actions">
                  <Button @click="emit('apply-default-company-blacklist')">外包/劳务派遣等配置</Button>
                  <Button danger ghost :disabled="form.company_blacklist.length === 0" @click="emit('clear-company-blacklist')">
                    清空全部
                  </Button>
                </div>

                <div class="boss-tag-editor">
                  <Input
                    v-model:value="draftInputs.companyBlacklist"
                    placeholder="输入公司名关键词后点击添加，例如 软通动力、FESCO"
                    @pressEnter="emit('add-tag', { field: 'company_blacklist', source: 'companyBlacklist' })"
                  />
                  <Button @click="emit('add-tag', { field: 'company_blacklist', source: 'companyBlacklist' })">
                    <template #icon><PlusOutlined /></template>
                    添加
                  </Button>
                </div>

                <div class="boss-tag-list">
                  <Tag
                    v-for="item in form.company_blacklist"
                    :key="item"
                    closable
                    class="boss-pill-tag"
                    @close="removeClosableTag('company_blacklist', item, $event)"
                  >
                    {{ item }}
                  </Tag>
                </div>
              </div>
            </FormItem>

            <FormItem>
              <template #label>
                <span class="boss-setting-label">
                  <span>岗位标题黑名单</span>
                  <Tooltip title="开启后命中的岗位标题会被直接跳过。支持模糊匹配，不区分大小写。">
                    <ExclamationCircleOutlined class="boss-inline-tip" />
                  </Tooltip>
                </span>
              </template>
              <div class="boss-blacklist-box">
                <div class="boss-blacklist-header">
                  <strong>启用黑名单过滤</strong>
                  <Switch v-model:checked="form.enable_job_title_blacklist" />
                </div>

                <div class="boss-blacklist-actions">
                  <Button danger ghost :disabled="form.job_title_blacklist.length === 0" @click="emit('clear-job-title-blacklist')">
                    清空全部
                  </Button>
                </div>

                <div class="boss-tag-editor">
                  <Input
                    v-model:value="draftInputs.jobTitleBlacklist"
                    placeholder="输入岗位标题关键词后点击添加，例如 实习、外派、电销"
                    @pressEnter="emit('add-tag', { field: 'job_title_blacklist', source: 'jobTitleBlacklist' })"
                  />
                  <Button @click="emit('add-tag', { field: 'job_title_blacklist', source: 'jobTitleBlacklist' })">
                    <template #icon><PlusOutlined /></template>
                    添加
                  </Button>
                </div>

                <div class="boss-tag-list">
                  <Tag
                    v-for="item in form.job_title_blacklist"
                    :key="item"
                    closable
                    class="boss-pill-tag"
                    @close="removeClosableTag('job_title_blacklist', item, $event)"
                  >
                    {{ item }}
                  </Tag>
                </div>
              </div>
            </FormItem>

            <FormItem v-if="isBoss" label="在线筛选">
              <div class="boss-checkable-tags">
                <CheckableTag
                  v-for="option in bossActiveStatusOptions"
                  :key="option.value"
                  :checked="form.boss_active_statuses.includes(option.value)"
                  class="boss-checkable-tag"
                  @change="toggleBossActiveStatus(option.value, form.boss_active_statuses)"
                >
                  {{ option.text }}
                </CheckableTag>
              </div>
            </FormItem>
          </div>
        </Form>
      </Card>

      <Card v-if="supportsChat" :bordered="false" class="boss-config-card">
        <template #title>
          <div class="boss-card-title">
            <span class="boss-card-kicker">Chat</span>
            <div class="boss-title-heading">
              <h3>{{ isLiepin ? '继续聊后追加消息' : isYupao ? '沟通后自动发消息' : '聊天动作' }}</h3>
              <Tooltip :title="isLiepin ? '猎聘会先发起继续聊，再按这里的配置决定是否追加一条自定义消息。' : isYupao ? '鱼泡会先尝试打开沟通入口，再按这里的配置决定是否补发一条消息。' : 'Boss 不再提供总开关；模板、常用语、图片、同意微信、自动跟进各自独立控制。'">
                <ExclamationCircleOutlined class="boss-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </template>

        <div class="boss-setting-grid">
          <div v-if="!isBoss" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>{{ isLiepin ? '追加一条消息' : '沟通后自动发消息' }}</strong>
                <Tooltip :title="isLiepin ? '关闭时只发起继续聊，不再额外发送自定义消息。' : '关闭时只尝试打开鱼泡沟通入口，不再额外发送消息。'">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.auto_send_chat" />
          </div>

          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>{{ isLiepin ? '使用自定义模板' : '开启自定义模板' }}</strong>
                <Tooltip title="支持 {job_title}、{company_name} 两个变量。关闭时会使用默认首聊文案。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.use_chat_template" />
          </div>

          <div v-if="isBoss" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>开启常用语发送</strong>
                <Tooltip title="会按你下面配置的序号，逐条点击 Boss 页面里的常用语项。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.enable_common_phrases" />
          </div>

          <div v-if="supportsImageResume" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>{{ imageSwitchTitle }}</strong>
                <Tooltip :title="imageSwitchTooltip">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.enable_image_resume" />
          </div>

          <div v-if="isBoss" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>自动同意微信交换</strong>
                <Tooltip title="检测到微信名片确认卡片时，自动点击“同意”。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.auto_agree_wechat_card" />
          </div>

          <div v-if="isBoss" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>仅首次聊天</strong>
                <Tooltip title="开启后只发送投递后的首聊，不再自动响应 HR 后续新消息。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.boss_first_greeting_only" />
          </div>

          <div v-if="isBoss" class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>开启自动跟进回复</strong>
                <Tooltip title="命中规则关键词时自动回复。若同时开启“仅首次聊天”，运行时仍会以首次聊天优先。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.enable_follow_up_reply" />
          </div>
        </div>

        <Form layout="vertical">
          <div class="boss-form-grid boss-form-grid--single">
            <FormItem label="聊天模板">
              <TextArea
                v-model:value="form.chat_template"
                :disabled="(!isBoss && !form.auto_send_chat) || !form.use_chat_template"
                :rows="4"
                :placeholder="isLiepin ? '例如：您好，我刚刚关注了贵司的 {job_title} 岗位，期待进一步沟通，谢谢。' : '例如：您好，我正在关注贵司的 {job_title} 岗位，希望进一步沟通。'"
              />
            </FormItem>

            <FormItem v-if="isBoss" label="常用语序号">
              <div class="boss-tag-editor">
                <Input
                  v-model:value="draftInputs.commonPhraseIndex"
                  :disabled="!form.enable_common_phrases"
                  placeholder="输入序号后点击添加"
                  type="number"
                  @pressEnter="emit('add-tag', { field: 'common_phrase_indexes', source: 'commonPhraseIndex' })"
                />
                <Button
                  :disabled="!form.enable_common_phrases"
                  @click="emit('add-tag', { field: 'common_phrase_indexes', source: 'commonPhraseIndex' })"
                >
                  <template #icon><PlusOutlined /></template>
                  添加
                </Button>
              </div>
              <div class="boss-tag-list">
                <Tag
                  v-for="item in form.common_phrase_indexes"
                  :key="item"
                  closable
                  class="boss-pill-tag"
                  @close="removeClosableTag('common_phrase_indexes', item, $event)"
                >
                  第 {{ item }} 条
                </Tag>
              </div>
            </FormItem>

            <FormItem v-if="supportsImageResume" :label="imageFieldLabel">
              <div class="boss-upload-row">
                <Upload
                  accept="image/*"
                  :before-upload="beforeImageUpload"
                  :show-upload-list="false"
                  :disabled="!form.enable_image_resume"
                >
                  <Button :disabled="!form.enable_image_resume">
                    <template #icon><UploadOutlined /></template>
                    {{ isLiepin ? '选择简历图片' : '选择图片' }}
                  </Button>
                </Upload>
                <Button
                  danger
                  ghost
                  :disabled="!form.image_resume_path"
                  @click="clearSelectedImage"
                >
                  清空
                </Button>
              </div>
              <div class="boss-upload-meta">
                <span>{{ form.image_resume_path || imageEmptyText }}</span>
                <span v-if="imageUploading">图片上传中...</span>
              </div>
              <div v-if="imagePreviewUrl" class="boss-upload-preview">
                <Image
                  :src="imagePreviewUrl"
                  alt="图片预览"
                  class="boss-upload-image"
                  :width="72"
                  :height="72"
                  :preview="{ src: imagePreviewUrl }"
                />
              </div>
            </FormItem>
          </div>
        </Form>

        <div v-if="isBoss" class="boss-rules-section">
          <div class="boss-section-header">
            <div class="boss-title-heading">
              <h4>自动回复规则</h4>
              <Tooltip title="命中关键词后自动回复。支持 {job_title}、{company_name}、{latest_message}。">
                <ExclamationCircleOutlined class="boss-inline-tip" />
              </Tooltip>
            </div>
            <div class="boss-section-actions">
              <Button :disabled="!form.enable_follow_up_reply" @click="resetReplyRules(form)">恢复默认</Button>
              <Button type="primary" :disabled="!form.enable_follow_up_reply" @click="addReplyRule(form)">
                <template #icon><PlusOutlined /></template>
                新增规则
              </Button>
            </div>
          </div>

          <Collapse
            v-if="form.boss_reply_rules.length"
            v-model:activeKey="replyRuleActiveKeys"
            class="boss-rule-collapse"
            expand-icon-position="end"
          >
            <CollapsePanel
              v-for="(rule, index) in form.boss_reply_rules"
              :key="rule.id"
              class="boss-rule-panel"
              :class="{ 'boss-rule-panel--disabled': !rule.enabled }"
            >
              <template #header>
                <div class="boss-rule-panel-head">
                  <div class="boss-rule-heading">
                    <span class="boss-rule-index">规则 {{ String(index + 1).padStart(2, '0') }}</span>
                    <strong class="boss-rule-name">{{ rule.label || `未命名规则 ${String(index + 1).padStart(2, '0')}` }}</strong>
                  </div>
                  <div class="boss-rule-summary">
                    <span :class="['boss-rule-state-pill', rule.enabled ? 'boss-rule-state-pill--on' : 'boss-rule-state-pill--off']">
                      {{ rule.enabled ? '启用中' : '已停用' }}
                    </span>
                    <span class="boss-rule-summary-pill">{{ rule.keywords.length }} 个关键词</span>
                  </div>
                </div>
              </template>

              <template #extra>
                <div class="boss-rule-panel-extra" @click.stop>
                  <div class="boss-inline-switch">
                    <span>启用</span>
                    <Switch v-model:checked="rule.enabled" :disabled="!form.enable_follow_up_reply" />
                  </div>
                  <Button danger ghost :disabled="!form.enable_follow_up_reply" @click="removeReplyRule(form, rule.id)">
                    删除
                  </Button>
                </div>
              </template>

              <div class="boss-rule-card" :class="{ 'boss-rule-card--disabled': !rule.enabled }">
                <div class="boss-rule-layout">
                  <FormItem label="规则名称">
                    <Input
                      v-model:value="rule.label"
                      :disabled="!form.enable_follow_up_reply"
                      placeholder="例如：微信沟通"
                    />
                  </FormItem>

                  <FormItem label="回复内容" class="boss-rule-message">
                    <TextArea
                      v-model:value="rule.reply_template"
                      :disabled="!form.enable_follow_up_reply"
                      :rows="5"
                      placeholder="例如：可以的，关于 {job_title} 的详细情况我们可以继续沟通。"
                    />
                  </FormItem>

                  <FormItem label="匹配关键词" class="boss-rule-keywords">
                    <div class="boss-tag-editor">
                      <Input
                        v-model:value="replyRuleDrafts[rule.id]"
                        :disabled="!form.enable_follow_up_reply"
                        placeholder="输入关键词后点击添加，例如 加微信"
                        @pressEnter="addRuleKeyword(rule)"
                      />
                      <Button :disabled="!form.enable_follow_up_reply" @click="addRuleKeyword(rule)">
                        <template #icon><PlusOutlined /></template>
                        添加
                      </Button>
                    </div>
                    <div class="boss-tag-list">
                      <Tag
                        v-for="keyword in rule.keywords"
                        :key="keyword"
                        closable
                        class="boss-pill-tag"
                        @close="closeRuleKeyword(rule, keyword, $event)"
                      >
                        {{ keyword }}
                      </Tag>
                    </div>
                  </FormItem>
                </div>
              </div>
            </CollapsePanel>
          </Collapse>
          <Empty v-else description="当前还没有自动回复规则，点击“新增规则”即可添加。" />
        </div>
      </Card>

      <Card :bordered="false" class="boss-config-card">
        <template #title>
          <div class="boss-card-title">
            <span class="boss-card-kicker">Runtime</span>
            <div class="boss-title-heading">
              <h3>去重与实时观看</h3>
              <Tooltip title="这里控制去重策略、隐藏窗口模式和实时预览。">
                <ExclamationCircleOutlined class="boss-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </template>

        <Form layout="vertical">
          <div class="boss-form-grid boss-form-grid--two">
            <FormItem label="冷却天数">
              <InputNumber v-model:value="form.cooldown_days" class="boss-input-number" :min="0" />
            </FormItem>

            <FormItem label="截图间隔 (ms)">
              <InputNumber v-model:value="form.live_preview_interval_ms" class="boss-input-number" :min="500" :step="100" />
            </FormItem>
          </div>
        </Form>

        <div class="boss-setting-grid boss-setting-grid--runtime">
          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>历史投递去重</strong>
                <Tooltip title="开启后会使用历史投递记录去重，当前账号和其他账号的命中记录都会参与判断。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.cross_account_dedupe_enabled" />
          </div>

          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>同标题同公司</strong>
                <Tooltip title="只有岗位标题和公司名都与历史投递记录一致时才跳过。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.dedupe_match_by_title" />
          </div>

          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>单公司匹配</strong>
                <Tooltip title="只要历史上投递过该公司任一岗位，这次就直接跳过，不再检查标题。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.dedupe_match_by_company" />
          </div>

          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>隐藏窗口模式</strong>
                <Tooltip title="开启后浏览器不会直接占用桌面前台区域。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.headless_enabled" />
          </div>

          <div class="boss-setting-card">
            <div class="boss-setting-copy">
              <span class="boss-setting-label">
                <strong>开启实时观看</strong>
                <Tooltip title="在实时页持续推送任务截图与状态。通常会配合隐藏窗口模式一起使用。">
                  <ExclamationCircleOutlined class="boss-inline-tip" />
                </Tooltip>
              </span>
            </div>
            <Switch v-model:checked="form.live_preview_enabled" />
          </div>
        </div>
      </Card>
    </div>

    <aside class="boss-config-sidebar">
      <Card :bordered="false" class="boss-side-card">
        <template #title>
          <div class="boss-card-title boss-card-title--compact">
            <span class="boss-card-kicker">Summary</span>
            <div>
              <h3>当前配置摘要</h3>
            </div>
          </div>
        </template>

        <Descriptions :column="1" size="small" class="boss-summary-descriptions">
          <DescriptionsItem :label="summaryEntryLabel">{{ summaryEntryValue }}</DescriptionsItem>
          <DescriptionsItem label="薪资区间">{{ form.salary_min ?? '-' }}K ~ {{ form.salary_max ?? '-' }}K</DescriptionsItem>
          <DescriptionsItem label="城市数">{{ form.cities.length }}</DescriptionsItem>
          <DescriptionsItem label="关键词数">{{ form.keywords.length }}</DescriptionsItem>
          <DescriptionsItem label="公司黑名单">{{ companyBlacklistSummary }}</DescriptionsItem>
          <DescriptionsItem label="岗位标题黑名单">{{ jobTitleBlacklistSummary }}</DescriptionsItem>
          <DescriptionsItem v-if="supportsChat" :label="isBoss ? '聊天动作' : (isLiepin ? '追加消息' : '自动发消息')">{{ chatActionsSummary }}</DescriptionsItem>
          <DescriptionsItem label="任务备注">{{ form.remark || '-' }}</DescriptionsItem>
          <DescriptionsItem v-if="isBoss" label="首聊模式">{{ form.boss_first_greeting_only ? '仅首次聊天' : '允许后续跟进' }}</DescriptionsItem>
          <DescriptionsItem v-if="isBoss" label="常用语条数">{{ form.common_phrase_indexes.length }}</DescriptionsItem>
        </Descriptions>
      </Card>

      <Card :bordered="false" class="boss-side-card">
        <template #title>
          <div class="boss-card-title boss-card-title--compact">
            <span class="boss-card-kicker">Checklist</span>
            <div>
              <h3>执行前检查</h3>
            </div>
          </div>
        </template>

        <div class="boss-checklist">
          <Alert
            message="1. 账号"
            description="先确认当前账号缓存已经登录，避免启动后卡在等待人工确认。"
            type="info"
            show-icon
          />
          <Alert
            :message="entryChecklistTitle"
            :description="entryChecklistDescription"
            type="info"
            show-icon
          />
          <Alert
            v-if="supportsChat"
            :message="isLiepin ? '3. 继续聊附加消息' : isYupao ? '3. 沟通后自动发消息' : '3. 聊天子功能'"
            :description="chatChecklistDescription"
            type="warning"
            show-icon
          />
          <Alert
            v-if="form.enable_company_blacklist || form.company_blacklist.length > 0"
            message="公司黑名单"
            :description="companyBlacklistChecklistDescription"
            :type="form.enable_company_blacklist ? 'warning' : 'info'"
            show-icon
          />
          <Alert
            v-if="form.enable_job_title_blacklist || form.job_title_blacklist.length > 0"
            message="岗位标题黑名单"
            :description="jobTitleBlacklistChecklistDescription"
            :type="form.enable_job_title_blacklist ? 'warning' : 'info'"
            show-icon
          />
          <Alert
            v-if="isBoss && form.cross_account_dedupe_enabled"
            message="4. 去重说明"
            description="开启后会按冷却期检查历史投递记录；可选择“同标题同公司”精确去重，或开启“单公司匹配”后按公司维度整组跳过。"
            type="info"
            show-icon
          />
        </div>
      </Card>

      <Card :bordered="false" class="boss-side-card boss-side-card--actions">
        <template #title>
          <div class="boss-card-title boss-card-title--compact">
            <span class="boss-card-kicker">Preset</span>
            <div>
              <h3>配置存档</h3>
            </div>
          </div>
        </template>

        <div class="boss-preset-actions">
          <Button type="primary" @click="emit('save-config')">保存配置</Button>
          <Button @click="emit('export-config')">导出配置</Button>
          <Button @click="openImportDialog">导入配置</Button>
        </div>

        <div class="boss-preset-meta">
          <div>
            <span>最后修改</span>
            <strong>{{ formMeta.lastModifiedAt ? new Date(formMeta.lastModifiedAt).toLocaleString('zh-CN', { hour12: false }) : '暂无' }}</strong>
          </div>
          <div>
            <span>上次保存</span>
            <strong>{{ formMeta.lastSavedAt ? new Date(formMeta.lastSavedAt).toLocaleString('zh-CN', { hour12: false }) : '尚未保存' }}</strong>
          </div>
          <div :class="['boss-preset-state-card', formMeta.dirty ? 'boss-preset-state-card--dirty' : 'boss-preset-state-card--clean']">
            <span>当前状态</span>
            <strong :class="['boss-preset-status', formMeta.dirty ? 'boss-preset-status--dirty' : 'boss-preset-status--clean']">
              {{ formMeta.dirty ? '有未保存改动' : '已与本地保存一致' }}
            </strong>
          </div>
        </div>
      </Card>
    </aside>
  </section>
</template>

<style scoped>
.boss-hidden-input {
  display: none;
}

.boss-config-shell {
  display: grid;
  grid-template-columns: minmax(0, 1.34fr) 320px;
  gap: 18px;
  align-items: start;
}

.boss-config-main,
.boss-config-sidebar,
.boss-checklist,
.boss-rule-list {
  display: grid;
  gap: 18px;
}

.boss-config-card,
.boss-side-card {
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.boss-config-sidebar {
  align-content: start;
  align-items: start;
}

.boss-side-card {
  width: 100%;
  align-self: start;
}

.boss-card-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.boss-title-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
}

.boss-card-title--compact {
  gap: 10px;
}

.boss-card-title h3 {
  margin: 0;
  font-size: 1.08rem;
  letter-spacing: -0.03em;
  color: #172554;
}

.boss-card-kicker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 70px;
  height: 28px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
  font-size: 0.76rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.boss-form-grid {
  display: grid;
  gap: 14px;
}

.boss-form-grid--two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.boss-form-grid--single {
  grid-template-columns: 1fr;
}

.boss-span-two {
  grid-column: 1 / -1;
}

.boss-account-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.boss-account-select {
  flex: 1;
  min-width: 0;
}

.boss-input-number,
.boss-input-number--full {
  width: 100%;
}

.boss-tag-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

.boss-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.boss-pill-tag {
  margin-inline-end: 0;
  padding: 5px 10px;
  border-radius: 999px;
  background: #eff6ff;
  border-color: #bfdbfe;
  color: #1d4ed8;
}

.boss-blacklist-box {
  display: grid;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
}

.boss-salary-floor-box {
  display: grid;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
}

.boss-salary-floor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.boss-salary-floor-header strong {
  color: #0f172a;
}

.boss-salary-floor-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.boss-salary-floor-field {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.boss-salary-floor-field span {
  color: #64748b;
  font-size: 0.84rem;
  white-space: nowrap;
}

.boss-salary-floor-field small {
  color: #64748b;
}

.boss-blacklist-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.boss-blacklist-header strong {
  color: #0f172a;
}

.boss-blacklist-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.boss-checkable-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.boss-checkable-tag {
  margin-inline-end: 0;
  padding: 7px 12px;
  border-radius: 999px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
}

.boss-checkable-tag.ant-tag-checkable-checked {
  background: linear-gradient(135deg, #2563eb, #60a5fa);
  color: #fff;
}

.boss-setting-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.boss-setting-grid--runtime {
  margin-bottom: 0;
}

.boss-setting-card {
  min-height: 82px;
  padding: 15px 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.boss-setting-copy {
  min-width: 0;
}

.boss-setting-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.boss-setting-card strong {
  display: block;
  color: #0f172a;
  line-height: 1.5;
}

.boss-inline-tip {
  flex: 0 0 auto;
  color: #94a3b8;
  font-size: 0.92rem;
  cursor: help;
  transition: color 0.2s ease;
}

.boss-inline-tip:hover {
  color: #2563eb;
}

.boss-upload-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.boss-upload-meta {
  display: grid;
  gap: 4px;
  margin-top: 10px;
  color: #64748b;
  font-size: 0.9rem;
}

.boss-upload-preview {
  margin-top: 12px;
  display: inline-flex;
  padding: 6px;
  border-radius: 16px;
  border: 1px solid #dbeafe;
  background: #f8fbff;
}

.boss-upload-image {
  width: 72px;
}

.boss-rules-section {
  margin-top: 6px;
}

.boss-section-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.boss-section-header h4 {
  margin: 0;
  color: #172554;
  font-size: 1rem;
}

.boss-section-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.boss-preset-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.boss-preset-meta {
  margin-top: 14px;
  display: grid;
  gap: 10px;
}

.boss-preset-meta div {
  padding: 12px 14px;
  border-radius: 16px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 4px;
}

.boss-preset-meta span {
  color: #64748b;
  font-size: 0.82rem;
}

.boss-preset-meta strong {
  color: #0f172a;
  font-size: 0.95rem;
}

.boss-preset-state-card {
  border-width: 1px;
}

.boss-preset-state-card--clean {
  border-color: rgba(34, 197, 94, 0.2);
  background: linear-gradient(180deg, rgba(240, 253, 244, 0.95), #ffffff);
}

.boss-preset-state-card--dirty {
  border-color: rgba(245, 158, 11, 0.26);
  background: linear-gradient(180deg, rgba(255, 247, 237, 0.98), #ffffff);
}

.boss-preset-status {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 0.86rem;
  font-weight: 800;
  line-height: 1;
}

.boss-preset-status--clean {
  color: #15803d;
  background: rgba(34, 197, 94, 0.12);
}

.boss-preset-status--dirty {
  color: #c2410c;
  background: rgba(245, 158, 11, 0.14);
}

.boss-rule-collapse {
  display: grid;
  gap: 12px;
  background: transparent;
}

:deep(.boss-rule-collapse.ant-collapse) {
  border: 0;
  background: transparent;
}

.boss-rule-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 0;
}

.boss-rule-panel-extra {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-right: 8px;
}

.boss-rule-name {
  color: #0f172a;
  font-size: 0.98rem;
  line-height: 1.35;
  letter-spacing: -0.02em;
}

.boss-rule-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.boss-rule-state-pill,
.boss-rule-summary-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 700;
}

.boss-rule-state-pill--on {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.boss-rule-state-pill--off {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.boss-rule-summary-pill {
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
}

.boss-rule-card {
  padding: 4px 2px 2px;
}

.boss-rule-card--disabled {
  opacity: 0.68;
}

.boss-rule-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.boss-rule-heading {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.boss-rule-heading small {
  color: #64748b;
}

.boss-rule-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: fit-content;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0.05em;
}

.boss-rule-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.boss-inline-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 14px;
  background: #eff6ff;
  color: #1e3a8a;
}

.boss-rule-layout {
  display: grid;
  grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
  gap: 14px;
}

.boss-rule-message,
.boss-rule-keywords {
  margin-bottom: 0;
}

:deep(.boss-rule-card .ant-form-item) {
  margin-bottom: 0;
  padding: 14px;
  border-radius: 18px;
  background: #f8fbff;
  border: 1px solid #e0ecff;
}

:deep(.boss-rule-card .ant-form-item-label > label) {
  color: #0f172a;
  font-weight: 700;
}

:deep(.boss-rule-collapse .ant-collapse-item) {
  border: 1px solid #dbeafe;
  border-radius: 22px;
  overflow: hidden;
  background: linear-gradient(180deg, #ffffff, #f7fbff);
}

:deep(.boss-rule-collapse .ant-collapse-header) {
  align-items: center !important;
  padding: 16px 18px !important;
}

:deep(.boss-rule-collapse .ant-collapse-content) {
  border-top: 1px solid #e0ecff;
  background: transparent;
}

:deep(.boss-rule-collapse .ant-collapse-content-box) {
  padding: 16px 18px 18px !important;
}

:deep(.boss-rule-collapse .ant-collapse-expand-icon) {
  padding-inline-end: 10px !important;
}

:deep(.boss-summary-descriptions .ant-descriptions-row > th),
:deep(.boss-summary-descriptions .ant-descriptions-row > td) {
  padding-bottom: 12px;
  text-align: left;
  vertical-align: top;
}

:deep(.boss-summary-descriptions .ant-descriptions-item-label) {
  color: #64748b;
}

:deep(.boss-summary-descriptions .ant-descriptions-item-content) {
  color: #0f172a;
  font-weight: 700;
}

:deep(.boss-checklist .ant-alert) {
  border-radius: 18px;
}

:deep(.ant-card-head) {
  border-bottom: 0;
  min-height: auto;
  padding: 18px 20px 0;
}

:deep(.ant-card-body) {
  padding: 18px 20px 20px;
}

:deep(.boss-upload-image .ant-image),
:deep(.boss-upload-image .ant-image-img) {
  width: 72px !important;
  height: 72px !important;
  border-radius: 12px;
  border: 1px solid #dbeafe;
  object-fit: cover;
  display: block;
}

:deep(.boss-upload-image .ant-image-mask) {
  border-radius: 12px;
}

:deep(.ant-form-item-label > label) {
  color: #334155;
  font-weight: 600;
}

:deep(.ant-input),
:deep(.ant-input-number),
:deep(.ant-select-selector),
:deep(.ant-input-affix-wrapper),
:deep(.ant-input-number-input) {
  border-radius: 14px !important;
}

:deep(.ant-input),
:deep(.ant-input-number),
:deep(.ant-select-selector),
:deep(.ant-input-textarea textarea) {
  border-color: #d9e8ff !important;
  box-shadow: none !important;
}

:deep(.ant-input:focus),
:deep(.ant-input-focused),
:deep(.ant-input-number-focused),
:deep(.ant-select-focused .ant-select-selector),
:deep(.ant-input-textarea textarea:focus) {
  border-color: #60a5fa !important;
  box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.16) !important;
}

@media (max-width: 1240px) {
  .boss-config-shell,
  .boss-rule-layout {
    grid-template-columns: 1fr;
  }

  .boss-config-sidebar {
    order: -1;
  }
}

@media (max-width: 860px) {
  .boss-form-grid--two,
  .boss-setting-grid {
    grid-template-columns: 1fr;
  }

  .boss-blacklist-header {
    flex-direction: column;
    align-items: stretch;
  }

  .boss-tag-editor {
    grid-template-columns: 1fr;
  }
}
</style>
