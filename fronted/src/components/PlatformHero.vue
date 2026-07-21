<script setup lang="ts">
import { Button, Card, Tooltip } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

import type { Platform } from '../types'
import type { SectionView } from '../composables/useJobHelpStore'
import type { AccountItem } from '../types'

defineProps<{
  platform: Platform
  section: SectionView
  meta: { name: string; description: string }
  account: AccountItem | null
  accountDisplayName: (account: Pick<AccountItem, 'username' | 'phone'>) => string
}>()

const emit = defineEmits<{
  (event: 'change-section', section: SectionView): void
  (event: 'refresh'): void
  (event: 'open-history'): void
}>()
</script>

<template>
  <section class="platform-hero-shell">
    <Card :bordered="false" class="platform-hero-card">
      <template #title>
        <h1 class="platform-hero-title">{{ meta.name }}</h1>
      </template>

      <template #extra>
        <div class="platform-hero-actions">
          <div class="platform-hero-switch">
            <Button :type="section === 'config' ? 'primary' : 'default'" @click="emit('change-section', 'config')">配置</Button>
            <Button :type="section === 'analysis' ? 'primary' : 'default'" @click="emit('change-section', 'analysis')">分析</Button>
            <Button :type="section === 'live' ? 'primary' : 'default'" @click="emit('change-section', 'live')">实时</Button>
          </div>
          <Button @click="emit('open-history')">任务历史</Button>
          <Button @click="emit('refresh')">刷新数据</Button>
        </div>
      </template>

      <div class="platform-hero-layout">
        <div class="platform-hero-copy">
          <div class="platform-hero-heading">
            <h2 class="platform-hero-section">{{ section === 'config' ? '执行配置台' : section === 'analysis' ? '投递分析台' : '实时观察台' }}</h2>
            <Tooltip :title="meta.description">
              <ExclamationCircleOutlined class="platform-hero-inline-tip" />
            </Tooltip>
          </div>
        </div>

        <div v-if="account" class="platform-hero-account">
          <span class="platform-hero-label">当前账号</span>
          <strong>{{ accountDisplayName(account) }}</strong>
          <p>{{ account.remark || '暂无备注' }}</p>
          <div class="platform-hero-metrics">
            <span>今日 {{ account.today_delivered_count }}</span>
            <span>累计 {{ account.total_delivered_count }}</span>
          </div>
        </div>
      </div>
    </Card>
  </section>
</template>

<style scoped>
.platform-hero-card {
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.platform-hero-title {
  margin: 0;
  font-size: 2.35rem;
  letter-spacing: -0.05em;
  color: #172554;
  line-height: 1.08;
}

.platform-hero-layout {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: stretch;
}

.platform-hero-copy {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.platform-hero-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 30px;
}

.platform-hero-section {
  margin: 0;
  font-size: 1.08rem;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #475569;
  line-height: 1.35;
}

.platform-hero-inline-tip {
  color: #94a3b8;
  font-size: 0.96rem;
  cursor: help;
}

.platform-hero-account {
  min-width: 260px;
  padding: 16px 18px;
  border-radius: 20px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  align-content: center;
  gap: 6px;
}

.platform-hero-account strong {
  color: #0f172a;
  font-size: 1.04rem;
}

.platform-hero-account p {
  margin: 0;
  color: #64748b;
}

.platform-hero-label {
  color: #64748b;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.platform-hero-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
  color: #475569;
  font-size: 0.9rem;
}

.platform-hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: flex-end;
}

.platform-hero-switch {
  display: inline-flex;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.platform-hero-switch :deep(.ant-btn) {
  min-width: 72px;
  border-radius: 14px;
}

:deep(.platform-hero-card .ant-card-head) {
  border-bottom: 0;
  min-height: auto;
  padding: 18px 20px 0;
}

:deep(.platform-hero-card .ant-card-body) {
  padding: 10px 20px 20px;
}

@media (max-width: 980px) {
  .platform-hero-layout {
    flex-direction: column;
  }

  .platform-hero-title {
    font-size: 2rem;
  }

  .platform-hero-account {
    min-width: 0;
  }
}

@media (max-width: 720px) {
  .platform-hero-actions,
  .platform-hero-switch {
    width: 100%;
  }

  .platform-hero-actions :deep(.ant-btn) {
    flex: 1 1 0;
  }
}
</style>
