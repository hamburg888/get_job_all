<script setup lang="ts">
import { computed } from 'vue'
import { Alert, Button, Card, Tooltip } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

import type { Platform, TaskStatus } from '../types'

const props = defineProps<{
  platform: Platform
  taskStatus: TaskStatus | null
  overviewTime: string
  showLoginCard: boolean
  busy: boolean
  canStart: boolean
  canStop: boolean
  formatTime: (value: string | null | undefined) => string
  toneForStatus: (status?: string) => string
  statusLabel: (status?: string) => string
  compact?: boolean
}>()

const emit = defineEmits<{
  (event: 'start'): void
  (event: 'stop'): void
  (event: 'confirm-login'): void
}>()

function toneClass(status?: string) {
  return `status-tone--${props.toneForStatus(status)}`
}

const stepStartedAt = computed(() => props.formatTime(props.taskStatus?.current_step_started_at))
</script>

<template>
  <section class="status-shell">
    <Card :bordered="false" class="status-shell-card">
      <template #title>
        <div class="status-header">
          <div class="status-header-block">
            <span class="status-kicker">Status</span>
            <div class="status-title-heading">
              <h3>运行状态</h3>
              <Tooltip title="配置、分析、实时三块页面共享同一份任务状态与执行进度。">
                <ExclamationCircleOutlined class="status-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </div>
      </template>

      <template #extra>
        <div class="status-actions">
          <Button v-if="canStart" type="primary" class="status-action-main" :loading="busy" @click="emit('start')">
            {{ busy ? '提交中...' : '开启任务' }}
          </Button>
          <Button v-else danger class="status-action-main" :loading="busy" :disabled="!canStop" @click="emit('stop')">
            {{ busy ? '提交中...' : '停止任务' }}
          </Button>
          <Button v-if="showLoginCard" :disabled="busy" @click="emit('confirm-login')">确认已登录</Button>
        </div>
      </template>

      <div v-if="compact" class="status-compact">
        <span :class="['status-badge', toneClass(taskStatus?.status)]">{{ statusLabel(taskStatus?.status) }}</span>
        <strong class="status-compact-step" :title="taskStatus?.current_step ?? ''">{{ taskStatus?.current_step ?? '待命' }}</strong>
        <div class="status-compact-stats">
          <span>已处理 <strong>{{ taskStatus?.processed_count ?? 0 }}</strong></span>
          <span>成功 <strong>{{ taskStatus?.delivered_count ?? 0 }}</strong></span>
          <span>失败 <strong>{{ taskStatus?.failed_count ?? 0 }}</strong></span>
        </div>
        <span class="status-compact-time">{{ stepStartedAt }}</span>
      </div>
      <div v-else class="status-grid">
        <article class="status-panel">
          <div class="status-panel-head">
            <span>任务状态</span>
            <span :class="['status-badge', toneClass(taskStatus?.status)]">{{ statusLabel(taskStatus?.status) }}</span>
          </div>
          <strong class="status-highlight">{{ taskStatus?.task_run_id ? `任务 #${taskStatus.task_run_id}` : '等待任务' }}</strong>
          <p class="status-copy">
            {{ showLoginCard ? '当前任务等待人工登录确认，浏览器会保持打开。' : '顶部状态会随执行过程同步刷新。' }}
          </p>
          <div class="status-meta">
            <span>上次启动</span>
            <strong>{{ formatTime(taskStatus?.started_at ?? overviewTime) }}</strong>
          </div>
        </article>

        <article class="status-panel status-panel--focus">
          <div class="status-panel-head">
            <span>当前步骤</span>
            <span class="status-run-id">{{ taskStatus?.task_run_id ? `#${taskStatus.task_run_id}` : '待命' }}</span>
          </div>
          <strong class="status-step-text" :title="taskStatus?.current_step ?? '暂无任务'">
            {{ taskStatus?.current_step ?? '暂无任务' }}
          </strong>
          <div class="status-step-time">{{ stepStartedAt }}</div>
        </article>

        <article class="status-panel">
          <div class="status-panel-head">
            <span>结果统计</span>
            <span class="status-total">{{ taskStatus?.processed_count ?? 0 }}</span>
          </div>
          <div class="status-result-grid">
            <div class="status-result-tile">
              <span>成功</span>
              <strong>{{ taskStatus?.delivered_count ?? 0 }}</strong>
            </div>
            <div class="status-result-tile">
              <span>失败</span>
              <strong>{{ taskStatus?.failed_count ?? 0 }}</strong>
            </div>
            <div class="status-result-tile status-result-tile--wide">
              <span>已处理</span>
              <strong>{{ taskStatus?.processed_count ?? 0 }}</strong>
            </div>
          </div>
        </article>
      </div>

      <Alert
        v-if="showLoginCard"
        class="status-alert"
        type="info"
        show-icon
        message="登录提醒"
        description="浏览器会保持打开。请先在对应账号缓存窗口完成登录，再点击“确认已登录”继续执行。"
      />
    </Card>
  </section>
</template>

<style scoped>
.status-shell-card {
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.status-header h3 {
  margin: 0;
  font-size: 1.12rem;
  letter-spacing: -0.03em;
  color: #172554;
}

.status-header-block {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.status-title-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
  flex-wrap: nowrap;
}

.status-title-heading h3 {
  white-space: nowrap;
}

.status-inline-tip {
  color: #94a3b8;
  font-size: 0.96rem;
  cursor: help;
}

.status-kicker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: auto;
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
  font-size: 0.74rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.status-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.status-action-main {
  min-width: 148px;
  height: 46px;
  padding-inline: 22px;
  border-radius: 14px;
  font-size: 1rem;
  font-weight: 700;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.16);
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.status-compact {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  padding: 10px 14px;
  border-radius: 16px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
}

.status-compact-step {
  flex: 1;
  min-width: 160px;
  color: #0f172a;
  font-size: 0.92rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.status-compact-stats {
  display: flex;
  gap: 16px;
  color: #64748b;
  font-size: 0.84rem;
}

.status-compact-stats strong {
  color: #0f172a;
  margin-left: 4px;
}

.status-compact-time {
  color: #94a3b8;
  font-size: 0.8rem;
  white-space: nowrap;
}

.status-panel {
  min-height: 166px;
  padding: 16px 17px;
  border-radius: 22px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.status-panel--focus {
  background: linear-gradient(180deg, #eff6ff, #f8fbff);
}

.status-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #64748b;
  font-size: 0.84rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.status-badge,
.status-run-id,
.status-total {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
}

.status-badge {
  border: 1px solid transparent;
}

.status-tone--success {
  background: rgba(34, 197, 94, 0.14);
  color: #15803d;
  border-color: rgba(34, 197, 94, 0.2);
}

.status-tone--warning {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.2);
}

.status-tone--danger {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.2);
}

.status-tone--neutral {
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
  border-color: rgba(148, 163, 184, 0.18);
}

.status-run-id,
.status-total {
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
}

.status-highlight {
  font-size: 1.16rem;
  letter-spacing: -0.04em;
  color: #0f172a;
  line-height: 1.35;
}

.status-copy {
  margin: 0;
  color: #64748b;
  line-height: 1.62;
  font-size: 0.9rem;
}

.status-meta {
  margin-top: auto;
  min-height: 54px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 4px;
}

.status-meta span {
  color: #64748b;
  font-size: 0.84rem;
}

.status-meta strong {
  color: #0f172a;
  font-size: 1rem;
}

.status-step-text {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  min-height: 64px;
  color: #0f172a;
  font-size: 1.04rem;
  line-height: 1.62;
  letter-spacing: -0.03em;
}

.status-step-time {
  margin-top: auto;
  min-height: 54px;
  display: flex;
  align-items: flex-end;
  color: #1d4ed8;
  font-size: 1.42rem;
  font-weight: 800;
  letter-spacing: 0.04em;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.status-result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.status-result-tile {
  padding: 13px 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid #e2e8f0;
  display: grid;
  gap: 4px;
}

.status-result-tile--wide {
  grid-column: 1 / -1;
}

.status-result-tile span {
  color: #64748b;
  font-size: 0.84rem;
}

.status-result-tile strong {
  color: #0f172a;
  font-size: 1.24rem;
}

.status-alert {
  margin-top: 18px;
  border-radius: 18px;
}

:deep(.status-shell-card .ant-card-head) {
  border-bottom: 0;
  min-height: auto;
  padding: 18px 20px 0;
}

:deep(.status-shell-card .ant-card-body) {
  padding: 18px 20px 20px;
}

@media (max-width: 1120px) {
  .status-grid {
    grid-template-columns: 1fr;
  }

  .status-panel {
    min-height: auto;
  }
}

@media (max-width: 720px) {
  .status-actions {
    justify-content: stretch;
  }

  .status-actions :deep(.ant-btn) {
    flex: 1 1 100%;
  }
}
</style>
