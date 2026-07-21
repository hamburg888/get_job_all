<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button, Descriptions, Modal, Pagination, Tag } from 'ant-design-vue'
import { EyeOutlined, HistoryOutlined, ReloadOutlined } from '@ant-design/icons-vue'

import type { Platform, StartTaskPayload, TaskRunRecord } from '../types'
import { fetchTaskRuns } from '../api'

const DescriptionsItem = Descriptions.Item

const props = defineProps<{
  open: boolean
  platform: Platform
  formatTime: (value: string | null | undefined) => string
}>()

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void
  (event: 'restore', config: Partial<StartTaskPayload>): void
}>()

const taskRuns = ref<TaskRunRecord[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 8
const detailRun = ref<TaskRunRecord | null>(null)

const pageCount = computed(() => Math.max(1, Math.ceil(taskRuns.value.length / pageSize)))
const pagedRuns = computed(() => {
  const start = (page.value - 1) * pageSize
  return taskRuns.value.slice(start, start + pageSize)
})

async function load() {
  loading.value = true
  try {
    taskRuns.value = await fetchTaskRuns(props.platform, 200)
  } catch {
    taskRuns.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      page.value = 1
      detailRun.value = null
      void load()
    }
  },
)

function close() {
  emit('update:open', false)
}

function onRestore(run: TaskRunRecord) {
  const config = (run.config_json || {}) as Record<string, unknown>
  const payload: Partial<StartTaskPayload> = {}
  const knownKeys: (keyof StartTaskPayload)[] = [
    'account_id', 'search_terms', 'use_manual_entry_url', 'manual_entry_url', 'keywords', 'cities',
    'enable_company_blacklist', 'company_blacklist', 'enable_job_title_blacklist', 'job_title_blacklist',
    'salary_min', 'salary_max', 'salary_floor_flexible_enabled', 'salary_floor_min', 'salary_floor_required_max',
    'cooldown_days', 'cross_account_dedupe_enabled', 'dedupe_match_by_title', 'dedupe_match_by_company',
    'headless_enabled', 'live_preview_enabled', 'live_preview_interval_ms', 'delivery_interval_seconds',
    'max_deliveries_per_run', 'auto_send_chat', 'use_chat_template', 'chat_template', 'enable_common_phrases',
    'common_phrase_indexes', 'enable_image_resume', 'image_resume_path', 'auto_agree_wechat_card',
    'boss_first_greeting_only', 'enable_follow_up_reply', 'boss_reply_rules', 'boss_active_statuses', 'remark',
  ]
  for (const key of knownKeys) {
    if (key in config && config[key] !== undefined) {
      // @ts-expect-error dynamic assignment from known keys
      payload[key] = config[key]
    }
  }
  emit('restore', payload)
  close()
}

function salaryText(run: TaskRunRecord): string {
  const c = run.config_json || {}
  const min = (c as Record<string, unknown>).salary_min
  const max = (c as Record<string, unknown>).salary_max
  if (typeof min === 'number' && typeof max === 'number') return `${min}-${max}K`
  return '-'
}

function citiesText(run: TaskRunRecord): string {
  const c = run.config_json?.cities
  return Array.isArray(c) ? (c as string[]).join(', ') || '-' : '-'
}

function statusColor(status: string): string {
  if (status === 'running') return 'processing'
  if (status === 'done' || status === 'success') return 'success'
  if (status === 'failed' || status === 'error') return 'error'
  if (status === 'stopped') return 'warning'
  return 'default'
}

const detailItems = computed(() => {
  const run = detailRun.value
  if (!run) return []
  const c = (run.config_json || {}) as Record<string, unknown>
  const items: Array<{ label: string; value: string }> = [
    { label: '账号', value: run.account_name || '-' },
    { label: '入口关键词/链接', value: run.keyword || '-' },
    { label: '搜索词', value: Array.isArray(c.search_terms) ? (c.search_terms as string[]).join(', ') || '-' : '-' },
    { label: '入口链接', value: typeof c.manual_entry_url === 'string' ? c.manual_entry_url || '-' : '-' },
    { label: '薪资区间', value: salaryText(run) },
    { label: '投递城市', value: citiesText(run) },
    { label: '关键词过滤', value: Array.isArray(c.keywords) ? (c.keywords as string[]).join(', ') || '-' : '-' },
    { label: '公司黑名单', value: Array.isArray(c.company_blacklist) ? (c.company_blacklist as string[]).join(', ') || '-' : '-' },
    { label: '岗位标题黑名单', value: Array.isArray(c.job_title_blacklist) ? (c.job_title_blacklist as string[]).join(', ') || '-' : '-' },
    { label: '薪资下限放宽', value: c.salary_floor_flexible_enabled ? '已开启' : '未开启' },
    { label: '投递间隔(秒)', value: String(c.delivery_interval_seconds ?? '-') },
    { label: '单次上限', value: String(c.max_deliveries_per_run ?? '-') },
    { label: '冷却天数', value: String(c.cooldown_days ?? '-') },
    { label: '跨账号去重', value: c.cross_account_dedupe_enabled ? '已开启' : '未开启' },
    { label: '无头模式', value: c.headless_enabled ? '已开启' : '未开启' },
    { label: '聊天动作', value: c.auto_send_chat ? '已启用' : '未启用' },
    { label: '任务备注', value: typeof c.remark === 'string' ? c.remark || '-' : '-' },
  ]
  return items
})
</script>

<template>
  <Modal
    :open="open"
    title="任务历史"
    width="92%"
    :footer="null"
    destroy-on-close
    @update:open="emit('update:open', $event)"
  >
    <div class="task-history-toolbar">
      <Button size="small" :loading="loading" @click="load">
        <template #icon><ReloadOutlined /></template>
        刷新
      </Button>
      <span class="task-history-count">共 {{ taskRuns.length }} 条</span>
    </div>

    <div class="task-history-table-wrap">
      <table class="task-history-table">
        <thead>
          <tr>
            <th>平台</th>
            <th>入口关键词</th>
            <th>状态</th>
            <th>薪资区间</th>
            <th>投递城市</th>
            <th>投递数</th>
            <th>失败数</th>
            <th>开始时间</th>
            <th>账号</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="10">加载中...</td>
          </tr>
          <tr v-else-if="!pagedRuns.length">
            <td colspan="10">暂无任务记录</td>
          </tr>
          <tr v-for="run in pagedRuns" :key="run.id">
            <td>{{ run.platform }}</td>
            <td class="task-history-keyword" :title="run.keyword">{{ run.keyword || '-' }}</td>
            <td><Tag :color="statusColor(run.status)">{{ run.status || '-' }}</Tag></td>
            <td>{{ salaryText(run) }}</td>
            <td>{{ citiesText(run) }}</td>
            <td>{{ run.delivered_count }}</td>
            <td>{{ run.failed_count }}</td>
            <td>{{ formatTime(run.started_at) }}</td>
            <td>{{ run.account_name || '-' }}</td>
            <td class="task-history-actions">
              <Button type="link" size="small" @click="detailRun = run">
                <template #icon><EyeOutlined /></template>
                详情
              </Button>
              <Button type="link" size="small" @click="onRestore(run)">
                <template #icon><HistoryOutlined /></template>
                恢复配置
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="task-history-pagination">
      <Pagination
        :current="page"
        :page-size="pageSize"
        :total="taskRuns.length"
        show-less-items
        @change="(p: number) => page = p"
      />
    </div>

    <Modal
      :open="!!detailRun"
      title="任务配置详情"
      width="640px"
      :footer="null"
      @update:open="(v: boolean) => { if (!v) detailRun = null }"
    >
      <div class="task-history-detail-body">
        <Descriptions v-if="detailRun" :column="1" size="small" bordered>
          <DescriptionsItem v-for="item in detailItems" :key="item.label" :label="item.label">
            {{ item.value }}
          </DescriptionsItem>
          <DescriptionsItem label="已处理">{{ detailRun.processed_count }}</DescriptionsItem>
          <DescriptionsItem label="开始时间">{{ formatTime(detailRun.started_at) }}</DescriptionsItem>
          <DescriptionsItem label="结束时间">{{ formatTime(detailRun.finished_at) }}</DescriptionsItem>
        </Descriptions>
      </div>
      <div class="task-history-detail-footer">
        <Button type="primary" @click="onRestore(detailRun!)">
          <template #icon><HistoryOutlined /></template>
          恢复此配置到表单
        </Button>
      </div>
    </Modal>
  </Modal>
</template>

<style scoped>
.task-history-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.task-history-count {
  color: #64748b;
  font-size: 0.86rem;
}

.task-history-table-wrap {
  width: 100%;
  overflow-x: auto;
}

.task-history-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.84rem;
  min-width: 860px;
}

.task-history-table th,
.task-history-table td {
  padding: 9px 11px;
  text-align: left;
  border-bottom: 1px solid #e2e8f0;
  white-space: nowrap;
}

.task-history-table th {
  color: #64748b;
  font-weight: 600;
  background: #f8fafc;
}

.task-history-table td {
  color: #334155;
}

.task-history-keyword {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-history-actions {
  display: flex;
  gap: 4px;
  white-space: nowrap;
}

.task-history-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.task-history-detail-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.task-history-detail-body {
  max-height: 60vh;
  overflow-y: auto;
  overflow-x: hidden;
}

:deep(.task-history-detail-body .ant-descriptions) {
  min-width: 100%;
}
</style>
