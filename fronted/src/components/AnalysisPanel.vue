<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, Card, DatePicker, Empty, Segmented, Select, Table, Tooltip } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

import InsightChart from './InsightChart.vue'
import CityRanking from './CityRanking.vue'
import KeywordCloud from './KeywordCloud.vue'
import type { AccountItem, DashboardFilters, JobItem, LogItem, Platform, PlatformOverview, PlatformStats, TaskStatus, AIAdvice } from '../types'
import { fetchAIAdvice } from '../api'

const props = defineProps<{
  platform: Platform
  overview: PlatformOverview | null
  taskStatus: TaskStatus | null
  jobs: JobItem[]
  jobsTotal: number
  currentPage: number
  pageCount: number
  logs: LogItem[]
  stats: PlatformStats
  accounts: AccountItem[]
  filters: DashboardFilters
  formatTime: (value: string | null | undefined) => string
  toneForStatus: (status?: string) => string
  statusLabel: (status?: string) => string
  displayReason: (status: string | undefined, reason: string | null | undefined) => string
}>()

const emit = defineEmits<{
  (event: 'change-page', nextPage: number): void
  (event: 'apply-filters'): void
  (event: 'reset-filters'): void
}>()

const filtersExpanded = ref(true)
const rangeMode = ref<string>('all')

type RangePreset = 'all' | 'week' | 'month' | 'last_month' | 'last_30_days' | 'custom'

const rangeOptions: { label: string; value: RangePreset }[] = [
  { label: '全部', value: 'all' },
  { label: '本周', value: 'week' },
  { label: '本月', value: 'month' },
  { label: '上月', value: 'last_month' },
  { label: '近30天', value: 'last_30_days' },
  { label: '自定义', value: 'custom' },
]

function formatDate(value: Date): string {
  const y = value.getFullYear()
  const m = String(value.getMonth() + 1).padStart(2, '0')
  const d = String(value.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function computeRange(preset: RangePreset): { from: string; to: string } {
  if (preset === 'all') return { from: '', to: '' }
  const today = new Date()
  if (preset === 'week') {
    const monday = new Date(today)
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7))
    const sunday = new Date(monday)
    sunday.setDate(monday.getDate() + 6)
    return { from: formatDate(monday), to: formatDate(sunday) }
  }
  if (preset === 'month') {
    const start = new Date(today.getFullYear(), today.getMonth(), 1)
    const end = new Date(today.getFullYear(), today.getMonth() + 1, 0)
    return { from: formatDate(start), to: formatDate(end) }
  }
  if (preset === 'last_month') {
    const start = new Date(today.getFullYear(), today.getMonth() - 1, 1)
    const end = new Date(today.getFullYear(), today.getMonth(), 0)
    return { from: formatDate(start), to: formatDate(end) }
  }
  // last_30_days
  const start = new Date(today)
  start.setDate(today.getDate() - 29)
  return { from: formatDate(start), to: formatDate(today) }
}

function onRangeModeChange(value: string | number) {
  const preset = String(value) as RangePreset
  rangeMode.value = preset
  if (preset === 'custom') {
    filtersExpanded.value = true
    return
  }
  const { from, to } = computeRange(preset)
  props.filters.date_from = from
  props.filters.date_to = to
  emit('apply-filters')
}

function onResetFilters() {
  rangeMode.value = 'all'
  emit('reset-filters')
}

const rangeLabel = computed(() => {
  if (rangeMode.value === 'custom') return ''
  const from = props.filters.date_from
  const to = props.filters.date_to
  if (from && to) return `${from} ~ ${to}`
  if (from) return `从 ${from}`
  if (to) return `截至 ${to}`
  return ''
})

const isBoss = computed(() => props.platform === 'boss')
const isChatPlatform = computed(() => props.platform === 'boss' || props.platform === 'liepin' || props.platform === 'yupao')

const tableColumns = computed(() => {
  const columns = [
    { title: '状态', dataIndex: 'latest_status', key: 'latest_status', width: 110, align: 'center' as const },
    { title: '岗位', dataIndex: 'title', key: 'title', width: 300 },
    { title: '公司', dataIndex: 'company_name', key: 'company_name', width: 180 },
    { title: '账号', dataIndex: 'account_name', key: 'account_name', width: 140 },
    { title: '投递时间', dataIndex: 'delivered_at', key: 'delivered_at', width: 180 },
    { title: '薪资', dataIndex: 'salary_text', key: 'salary_text', width: 120 },
    { title: '经验', dataIndex: 'experience_text', key: 'experience_text', width: 120 },
    { title: '学历', dataIndex: 'education_text', key: 'education_text', width: 120 },
    { title: '出错原因', dataIndex: 'latest_reason', key: 'latest_reason', width: 240 },
  ]
  if (isChatPlatform.value) {
    columns.splice(1, 0, {
      title: '发送状态',
      dataIndex: 'latest_send_status',
      key: 'latest_send_status',
      width: 140,
      align: 'center' as const,
    })
  }
  return columns
})

import dayjs from 'dayjs'

const aiAdvice = ref<AIAdvice | null>(null)
const aiLoading = ref(false)
const aiError = ref("")
const taskRuns = ref<TaskRunRecord[]>([])
const taskRunsLoading = ref(false)
const taskRunsModalOpen = ref(false)
const taskRunsPage = ref(1)
const taskRunsPageSize = 8
const taskRunsPageCount = computed(() => Math.max(1, Math.ceil(taskRuns.value.length / taskRunsPageSize)))
const pagedTaskRuns = computed(() => {
  const start = (taskRunsPage.value - 1) * taskRunsPageSize
  return taskRuns.value.slice(start, start + taskRunsPageSize)
})

function openTaskRunsModal() {
  taskRunsModalOpen.value = true
  taskRunsPage.value = 1
  if (!taskRuns.value.length) {
    void loadTaskRuns()
  }
}
type AiDateRange = [dayjs.Dayjs, dayjs.Dayjs]
const aiDateRange = ref<AiDateRange>([dayjs().subtract(7, 'day'), dayjs()])
const aiReportType = ref<'daily' | 'weekly' | 'strategy'>('strategy')

const aiRangePresets = [
  { label: '近1天', value: [dayjs(), dayjs()] as AiDateRange },
  { label: '近7天', value: [dayjs().subtract(7, 'day'), dayjs()] as AiDateRange },
  { label: '近30天', value: [dayjs().subtract(30, 'day'), dayjs()] as AiDateRange },
  { label: '本月', value: [dayjs().startOf('month'), dayjs()] as AiDateRange },
  { label: '上月', value: [dayjs().subtract(1, 'month').startOf('month'), dayjs().subtract(1, 'month').endOf('month')] as AiDateRange },
]

const aiReportTypeOptions = [
  { label: '综合策略', value: 'strategy' },
  { label: '日报', value: 'daily' },
  { label: '周报', value: 'weekly' },
]

async function generateAIAdvice() {
  aiLoading.value = true
  aiError.value = ""
  try {
    const range = aiDateRange.value
    const dateFrom = range?.[0]?.format('YYYY-MM-DD') ?? dayjs().format('YYYY-MM-DD')
    const dateTo = range?.[1]?.format('YYYY-MM-DD') ?? dateFrom
    aiAdvice.value = await fetchAIAdvice({
      platform: props.platform,
      report_type: aiReportType.value,
      date_from: dateFrom,
      date_to: dateTo,
    })
  } catch (error) {
    aiError.value = error instanceof Error ? error.message : "生成建议失败"
  } finally {
    aiLoading.value = false
  }
}



const statusOptions = [
  { label: '投递成功', value: 'delivered' },
  { label: '失败', value: 'failed' },
  { label: '已跳过', value: 'skipped' },
  { label: '待处理', value: 'pending' },
]

const sendStatusOptions = [
  { label: '已发送', value: 'sent' },
  { label: '发送失败', value: 'failed' },
  { label: '待发送', value: 'pending' },
  { label: '未发送', value: 'skipped' },
]

const accountOptions = computed(() =>
  props.accounts.map((account) => ({
    label: `${account.username || account.phone || '未命名账号'} / 今日${account.today_delivered_count}`,
    value: account.id,
  })),
)

const activeFilterCount = computed(() => {
  let count = 0
  if (props.filters.account_id) count += 1
  if (props.filters.statuses.length) count += 1
  if (isChatPlatform.value && props.filters.send_statuses.length) count += 1
  if (props.filters.date_from || props.filters.date_to) count += 1
  return count
})

const sendSummary = computed(() => {
  const summary = {
    sent: 0,
    failed: 0,
    pending: 0,
    skipped: 0,
  }

  for (const job of props.jobs) {
    const status = job.latest_send_status
    if (status === 'sent') summary.sent += 1
    else if (status === 'failed') summary.failed += 1
    else if (status === 'pending') summary.pending += 1
    else summary.skipped += 1
  }

  return summary
})

const tableData = computed(() => props.jobs.map((job) => ({ ...job, key: job.id })))

function sendStatusLabel(status: string | null | undefined) {
  const mapping: Record<string, string> = {
    pending: '待发送',
    sent: '已发送',
    failed: '发送失败',
    skipped: '未发送',
  }
  return mapping[status ?? ''] ?? '-'
}

function toneClass(status?: string | null) {
  return `analysis-tone--${props.toneForStatus(status ?? undefined)}`
}

function sendToneClass(status: string | null | undefined) {
  if (status === 'sent') return 'analysis-tone--success'
  if (status === 'failed') return 'analysis-tone--danger'
  if (status === 'pending') return 'analysis-tone--warning'
  return 'analysis-tone--neutral'
}
</script>

<template>
  <section class="analysis-shell">
    <Card :bordered="false" class="analysis-card analysis-card--filters">
      <template #title>
        <div class="analysis-card-title">
          <span class="analysis-kicker">Filters</span>
          <div class="analysis-card-heading">
            <h3>当前页面数据筛选</h3>
            <Tooltip title="这里的筛选会同时影响当前分析页的指标、画像、投递记录和日志。">
              <ExclamationCircleOutlined class="analysis-title-tip" />
            </Tooltip>
          </div>
        </div>
      </template>

      <div class="analysis-filter-bar">
        <div class="analysis-filter-left">
          <div class="analysis-time-bar">
            <span class="analysis-time-label">时间</span>
            <Segmented
              :value="rangeMode"
              :options="rangeOptions"
              size="small"
              @change="onRangeModeChange"
            />
            <span v-if="rangeLabel" class="analysis-time-range">{{ rangeLabel }}</span>
          </div>
          <div class="analysis-filter-summary">
            <span>已启用 {{ activeFilterCount }} 项筛选</span>
            <strong>{{ activeFilterCount ? '当前页数据已按条件收敛' : '当前为全量数据' }}</strong>
          </div>
        </div>
        <div class="analysis-filter-actions">
          <Button @click="filtersExpanded = !filtersExpanded">{{ filtersExpanded ? '收起筛选' : '展开筛选' }}</Button>
          <Button type="primary" @click="emit('apply-filters')">应用筛选</Button>
          <Button @click="onResetFilters">重置</Button>
        </div>
      </div>

      <div v-if="filtersExpanded" class="analysis-filter-grid">
        <label class="analysis-filter-item">
          <span>账号</span>
          <Select
            v-model:value="filters.account_id"
            :options="accountOptions"
            allow-clear
            placeholder="全部账号"
          />
        </label>

        <label class="analysis-filter-item">
          <span>投递状态</span>
          <Select
            v-model:value="filters.statuses"
            :options="statusOptions"
            mode="multiple"
            allow-clear
            placeholder="全部状态"
          />
        </label>

        <label v-if="isChatPlatform" class="analysis-filter-item">
          <span>发送状态</span>
          <Select
            v-model:value="filters.send_statuses"
            :options="sendStatusOptions"
            mode="multiple"
            allow-clear
            placeholder="全部发送状态"
          />
        </label>

        <label v-if="rangeMode === 'custom'" class="analysis-filter-item">
          <span>开始日期</span>
          <input v-model="filters.date_from" type="date" />
        </label>

        <label v-if="rangeMode === 'custom'" class="analysis-filter-item">
          <span>结束日期</span>
          <input v-model="filters.date_to" type="date" />
        </label>
      </div>
    </Card>

    <div class="analysis-top-grid">
      <Card v-if="isBoss" :bordered="false" class="analysis-card">
        <template #title>
          <div class="analysis-card-title">
            <span class="analysis-kicker">Overview</span>
            <div class="analysis-card-heading">
              <h3>核心指标</h3>
            </div>
          </div>
        </template>

        <div class="analysis-stat-grid">
          <article class="analysis-stat-card">
            <span>累计岗位</span>
            <strong>{{ overview?.total_jobs ?? 0 }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>成功投递</span>
            <strong>{{ overview?.delivered_jobs ?? 0 }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>异常/失败</span>
            <strong>{{ overview?.failed_jobs ?? 0 }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>已处理岗位</span>
            <strong>{{ taskStatus?.processed_count ?? 0 }}</strong>
          </article>
        </div>
      </Card>

      <Card v-if="isChatPlatform" :bordered="false" class="analysis-card">
        <template #title>
          <div class="analysis-card-title">
            <span class="analysis-kicker">Chat</span>
            <div class="analysis-card-heading">
              <h3>聊天发送状态</h3>
            </div>
          </div>
        </template>

        <div class="analysis-stat-grid">
          <article class="analysis-stat-card">
            <span>已发送</span>
            <strong>{{ sendSummary.sent }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>发送失败</span>
            <strong>{{ sendSummary.failed }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>待发送</span>
            <strong>{{ sendSummary.pending }}</strong>
          </article>
          <article class="analysis-stat-card">
            <span>未发送</span>
            <strong>{{ sendSummary.skipped }}</strong>
          </article>
        </div>
      </Card>
    </div>

    <Card :bordered="false" class="analysis-card">
      <template #title>
        <div class="analysis-card-title">
          <span class="analysis-kicker">Distribution</span>
          <div class="analysis-card-heading">
            <h3>投递画像</h3>
          </div>
        </div>
      </template>

      <div class="analysis-chart-grid">
        <InsightChart title="薪资分布" type="bar" :items="stats.salary_buckets" />
        <CityRanking :items="stats.city_buckets" />
        <InsightChart title="经验分布" type="pie" :items="stats.experience_buckets" />
        <KeywordCloud :items="stats.keyword_buckets" />
      </div>
    </Card>

    <Card :bordered="false" class="analysis-card">
      <template #title>
        <div class="analysis-card-title">
          <span class="analysis-kicker">Records</span>
          <div class="analysis-card-heading">
            <h3>投递记录</h3>
          </div>
        </div>
      </template>

      <Table
        :columns="tableColumns"
        :data-source="tableData"
        :pagination="false"
        size="middle"
        :scroll="{ x: isChatPlatform ? 1620 : 1480 }"
        class="analysis-table"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'latest_status'">
            <span :class="['analysis-status-tag', toneClass(record.latest_status)]">
              {{ statusLabel(record.latest_status) }}
            </span>
          </template>

          <template v-else-if="column.key === 'latest_send_status'">
            <span :class="['analysis-status-tag', sendToneClass(record.latest_send_status)]">
              {{ sendStatusLabel(record.latest_send_status) }}
            </span>
          </template>

          <template v-else-if="column.key === 'title'">
            <div class="analysis-title-cell">
              <a :href="record.detail_url" target="_blank" rel="noreferrer">{{ record.title }}</a>
              <small>{{ record.platform_job_id }}</small>
            </div>
          </template>

          <template v-else-if="column.key === 'account_name'">
            {{ record.account_name ?? '-' }}
          </template>

          <template v-else-if="column.key === 'delivered_at'">
            {{ formatTime(record.delivered_at) }}
          </template>

          <template v-else-if="column.key === 'latest_reason'">
            <Tooltip :title="displayReason(record.latest_status, record.latest_reason)" placement="topLeft">
              <span class="analysis-reason-text">
                {{ displayReason(record.latest_status, record.latest_reason) }}
              </span>
            </Tooltip>
          </template>
        </template>
      </Table>

      <div class="analysis-table-footer">
        <span>共 {{ jobsTotal }} 条 / 第 {{ currentPage }} / {{ pageCount }} 页</span>
        <Pagination
          size="small"
          :current="currentPage"
          :total="jobsTotal"
          :page-size="10"
          :show-size-changer="false"
          @change="emit('change-page', $event)"
        />
      </div>
    </Card>

    <Card :bordered="false" class="analysis-card">
      <template #title>
        <div class="analysis-card-title">
          <span class="analysis-kicker">AI</span>
          <div class="analysis-card-heading">
            <h3>AI 智能建议</h3>
          </div>
        </div>
      </template>

      <div class="analysis-ai-controls">
        <div class="analysis-ai-control-row">
          <label class="analysis-ai-control-label">分析数据日期</label>
          <DatePicker.RangePicker
            v-model:value="aiDateRange"
            :presets="aiRangePresets"
            :allow-clear="false"
            size="middle"
          />
        </div>
        <div class="analysis-ai-control-row">
          <label class="analysis-ai-control-label">报告类型</label>
          <Select
            v-model:value="aiReportType"
            :options="aiReportTypeOptions"
            size="middle"
            style="min-width: 140px"
          />
        </div>
        <div class="analysis-ai-control-actions">
          <Button type="primary" :loading="aiLoading" @click="generateAIAdvice">生成建议</Button>
        </div>
      </div>
      <p class="analysis-ai-hint">基于所选日期范围内的投递数据、任务历史与简历生成薪资区间与投递时机建议</p>

      <div v-if="aiError" class="analysis-ai-error">{{ aiError }}</div>

      <div v-if="aiAdvice" class="analysis-ai-grid">
        <article class="analysis-ai-block">
          <span>薪资区间建议</span>
          <p>{{ aiAdvice.salary_suggestion || '-' }}</p>
        </article>
        <article class="analysis-ai-block">
          <span>投递时机建议</span>
          <p>{{ aiAdvice.timing_suggestion || '-' }}</p>
        </article>
        <article class="analysis-ai-block">
          <span>简历优化建议</span>
          <p>{{ aiAdvice.resume_suggestion || '-' }}</p>
        </article>
        <article class="analysis-ai-block analysis-ai-block--full">
          <span>综合建议</span>
          <p>{{ aiAdvice.advice_text || '-' }}</p>
        </article>
      </div>
    </Card>

  </section>
</template>

<style scoped>
.analysis-shell,
.analysis-top-grid {
  display: grid;
  gap: 18px;
  min-width: 0;
}

.analysis-top-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.analysis-card {
  min-width: 0;
  overflow: hidden;
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

:deep(.analysis-card .ant-card-head) {
  border-bottom: 0;
  min-height: auto;
  padding: 22px 22px 0;
}

:deep(.analysis-card .ant-card-body) {
  padding: 20px 22px 22px;
}

.analysis-card-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.analysis-card-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
}

.analysis-card-title h3 {
  margin: 0;
  font-size: 1.08rem;
  letter-spacing: -0.03em;
  color: #172554;
}

.analysis-title-tip {
  color: #94a3b8;
  font-size: 0.92rem;
  cursor: help;
  transition: color 0.2s ease;
}

.analysis-title-tip:hover {
  color: #2563eb;
}

.analysis-kicker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 76px;
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

.analysis-filter-bar,
.analysis-filter-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.analysis-filter-summary {
  display: grid;
  gap: 4px;
}

.analysis-filter-left {
  display: grid;
  gap: 10px;
}

.analysis-time-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.analysis-time-label {
  color: #334155;
  font-weight: 600;
}

.analysis-time-range {
  color: #64748b;
  font-size: 0.84rem;
}

.analysis-filter-summary span {
  color: #64748b;
  font-size: 0.84rem;
}

.analysis-filter-summary strong {
  color: #0f172a;
}

.analysis-filter-grid {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.analysis-filter-item {
  display: grid;
  gap: 8px;
}

.analysis-filter-item span {
  color: #334155;
  font-weight: 600;
}

.analysis-filter-item input {
  width: 100%;
  min-height: 40px;
  padding: 0 12px;
  border-radius: 14px;
  border: 1px solid #d9e8ff;
  background: #fff;
  color: #0f172a;
}

.analysis-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.analysis-stat-card {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 6px;
}

.analysis-stat-card span {
  color: #64748b;
  font-size: 0.84rem;
}

.analysis-stat-card strong {
  font-size: 1.28rem;
  color: #0f172a;
  letter-spacing: -0.04em;
}

.analysis-chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  min-width: 0;
}

.analysis-table {
  min-width: 0;
}

:deep(.analysis-table .ant-spin-nested-loading),
:deep(.analysis-table .ant-spin-container),
:deep(.analysis-table .ant-table),
:deep(.analysis-table .ant-table-container) {
  min-width: 0;
  max-width: 100%;
}

:deep(.analysis-table .ant-table-content),
:deep(.analysis-table .ant-table-body) {
  overflow-x: auto !important;
  max-width: 100%;
}

.analysis-status-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  border: 1px solid transparent;
}

.analysis-tone--success {
  background: rgba(34, 197, 94, 0.14);
  color: #15803d;
  border-color: rgba(34, 197, 94, 0.2);
}

.analysis-tone--warning {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.2);
}

.analysis-tone--danger {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.2);
}

.analysis-tone--neutral {
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
  border-color: rgba(148, 163, 184, 0.18);
}

.analysis-title-cell {
  display: grid;
  gap: 6px;
}

.analysis-title-cell a {
  color: #1d4ed8;
  text-decoration: none;
}

.analysis-title-cell small,
.analysis-send-cell small {
  color: #64748b;
  line-height: 1.5;
}

.analysis-reason-text {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  color: #475569;
  line-height: 1.6;
  word-break: break-word;
  cursor: help;
}

.analysis-table-footer {
  margin-top: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  color: #64748b;
}

@media (max-width: 1240px) {
  .analysis-top-grid,
  .analysis-chart-grid {
    grid-template-columns: 1fr;
  }

  .analysis-filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .analysis-stat-grid,
  .analysis-filter-grid {
    grid-template-columns: 1fr;
  }
}

.analysis-ai-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}


.analysis-ai-controls {
  display: flex;
  gap: 14px;
  align-items: flex-end;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.analysis-ai-control-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.analysis-ai-control-label {
  color: #64748b;
  font-size: 0.82rem;
  font-weight: 600;
}

.analysis-ai-control-actions {
  align-self: flex-end;
}

.analysis-ai-hint {
  color: #64748b;
  font-size: 0.84rem;
}

.analysis-ai-error {
  color: #ef4444;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(239, 68, 68, 0.08);
  margin-bottom: 14px;
}

.analysis-ai-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.analysis-ai-block {
  padding: 16px;
  border-radius: 14px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 8px;
}

.analysis-ai-block--full {
  grid-column: 1 / -1;
}

.analysis-ai-block span {
  color: #2563eb;
  font-size: 0.84rem;
  font-weight: 700;
}

.analysis-ai-block p {
  margin: 0;
  color: #334155;
  line-height: 1.6;
  white-space: pre-wrap;
}

.analysis-history-wrap {
  width: 100%;
  overflow-x: auto;
}

.analysis-history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
  min-width: 720px;
}

.analysis-history-table th,
.analysis-history-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #e2e8f0;
  white-space: nowrap;
}

.analysis-history-table th {
  color: #64748b;
  font-weight: 600;
  background: #f8fafc;
}

.analysis-history-table td {
  color: #334155;
}

@media (max-width: 900px) {
  .analysis-ai-grid {
    grid-template-columns: 1fr;
  }
}

</style>
