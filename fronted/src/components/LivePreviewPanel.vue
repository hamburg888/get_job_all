<script setup lang="ts">
import { computed } from 'vue'
import { Alert, Card, Empty, Tooltip } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

import type { LogItem, PreviewState } from '../types'

const props = defineProps<{
  imageUrl: string
  preview: PreviewState
  logs: LogItem[]
  formatTime: (value: string | null | undefined) => string
}>()

const chatLogs = computed(() =>
  props.logs.filter((log) => /(chat|greeting|follow_up)/i.test(log.event_type)).slice(0, 8),
)

const runtimeLogs = computed(() => props.logs.slice(0, 10))
</script>

<template>
  <section class="live-shell">
    <div class="live-top-grid">
      <Card :bordered="false" class="live-card live-card--frame">
        <template #title>
          <div class="live-card-title">
            <span class="live-kicker">Live</span>
            <div class="live-title-heading">
              <h3>实时操作画面</h3>
              <Tooltip title="当前浏览器画面会在这里持续更新，方便观察自动化操作是否稳定。">
                <ExclamationCircleOutlined class="live-inline-tip" />
              </Tooltip>
            </div>
          </div>
        </template>

        <div v-if="imageUrl" class="live-frame-wrap">
          <img class="live-frame" :src="imageUrl" alt="实时预览画面" />
        </div>
        <Empty v-else description="暂无实时画面">
          <template #image>
            <div class="live-empty-image">Preview</div>
          </template>
          <p class="live-empty-copy">{{ preview.message }}</p>
        </Empty>
      </Card>

      <div class="live-side-stack">
        <Card :bordered="false" class="live-card">
          <template #title>
            <div class="live-card-title">
              <span class="live-kicker">State</span>
              <div class="live-title-heading">
                <h3>预览状态</h3>
                <Tooltip title="这里只展示实时预览本身的状态，不再重复显示“当前步骤”。">
                  <ExclamationCircleOutlined class="live-inline-tip" />
                </Tooltip>
              </div>
            </div>
          </template>

          <div class="live-state-grid">
            <article class="live-state-tile">
              <span>预览开关</span>
              <strong>{{ preview.enabled ? '已开启' : '未开启' }}</strong>
            </article>
            <article class="live-state-tile">
              <span>隐藏窗口模式</span>
              <strong>{{ preview.headless_enabled ? '已开启' : '未开启' }}</strong>
            </article>
            <article class="live-state-tile">
              <span>截图间隔</span>
              <strong>{{ preview.interval_ms }} ms</strong>
            </article>
            <article class="live-state-tile">
              <span>更新时间</span>
              <strong>{{ formatTime(preview.updated_at) }}</strong>
            </article>
          </div>

          <Alert
            class="live-alert"
            type="info"
            show-icon
            message="说明"
            description="只有在“隐藏窗口模式 + 开启实时观看”同时启用时，才会持续显示实时画面。"
          />
        </Card>

        <Card :bordered="false" class="live-card">
          <template #title>
            <div class="live-card-title">
              <span class="live-kicker">Chat</span>
              <div class="live-title-heading">
                <h3>会话事件</h3>
                <Tooltip title="优先展示聊天相关事件，方便观察是否真的进入会话、发送常用语和图片。">
                  <ExclamationCircleOutlined class="live-inline-tip" />
                </Tooltip>
              </div>
            </div>
          </template>

          <div v-if="chatLogs.length" class="live-log-list live-log-list--compact">
            <article v-for="log in chatLogs" :key="log.id" class="live-log-card">
              <div class="live-log-meta">
                <span>{{ formatTime(log.created_at) }}</span>
                <strong>{{ log.event_type }}</strong>
              </div>
              <p>{{ log.message }}</p>
            </article>
          </div>
          <Empty v-else description="暂无会话事件" />
        </Card>
      </div>
    </div>

    <Card :bordered="false" class="live-card">
      <template #title>
        <div class="live-card-title">
          <span class="live-kicker">Logs</span>
          <div class="live-title-heading">
            <h3>实时日志</h3>
            <Tooltip title="按时间顺序展示最近的运行日志，便于回看操作链路。">
              <ExclamationCircleOutlined class="live-inline-tip" />
            </Tooltip>
          </div>
        </div>
      </template>

      <div v-if="runtimeLogs.length" class="live-log-list live-log-list--runtime">
        <article v-for="log in runtimeLogs" :key="log.id" class="live-log-card">
          <div class="live-log-meta">
            <span>{{ formatTime(log.created_at) }}</span>
            <strong>{{ log.event_type }}</strong>
          </div>
          <p>{{ log.message }}</p>
        </article>
      </div>
      <Empty v-else description="暂无实时日志" />
    </Card>
  </section>
</template>

<style scoped>
.live-shell,
.live-side-stack {
  display: grid;
  gap: 18px;
}

.live-top-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.8fr);
  gap: 18px;
}

.live-card {
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.live-card-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.live-title-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
}

.live-card-title h3 {
  margin: 0;
  font-size: 1.08rem;
  letter-spacing: -0.03em;
  color: #172554;
}

.live-inline-tip {
  color: #94a3b8;
  font-size: 0.96rem;
  cursor: help;
}

.live-kicker {
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

.live-frame-wrap {
  min-height: 420px;
  border-radius: 22px;
  overflow: hidden;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #eff6ff, #f8fbff);
}

.live-frame {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.live-empty-image {
  width: 112px;
  height: 112px;
  border-radius: 24px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.14), rgba(96, 165, 250, 0.24));
  color: #1d4ed8;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.live-empty-copy {
  margin: 0;
  color: #64748b;
  line-height: 1.7;
}

.live-state-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.live-state-tile {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 6px;
}

.live-state-tile span {
  color: #64748b;
  font-size: 0.84rem;
}

.live-state-tile strong {
  color: #0f172a;
  font-size: 1.02rem;
}

.live-alert {
  margin-top: 16px;
  border-radius: 18px;
}

.live-log-list {
  display: grid;
  gap: 12px;
  min-width: 0;
}

.live-log-list--compact {
  max-height: 430px;
  overflow: auto;
  padding-right: 2px;
}

.live-log-list--runtime {
  max-height: 520px;
  overflow-y: auto;
  overflow-x: hidden;
  padding-right: 4px;
}

.live-log-card {
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid #e2e8f0;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  min-width: 0;
  overflow: hidden;
}

.live-log-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #64748b;
  font-size: 0.82rem;
  min-width: 0;
}

.live-log-meta strong {
  color: #1e3a8a;
  flex-shrink: 0;
}

.live-log-card p {
  margin: 8px 0 0;
  color: #475569;
  line-height: 1.65;
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

:deep(.live-card .ant-card-head) {
  border-bottom: 0;
  min-height: auto;
  padding: 18px 20px 0;
}

:deep(.live-card .ant-card-body) {
  padding: 18px 20px 20px;
}

@media (max-width: 1180px) {
  .live-top-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .live-state-grid {
    grid-template-columns: 1fr;
  }
}
</style>
