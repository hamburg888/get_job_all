<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Button, Card, Form, Input, Modal, Select, Switch, Table } from 'ant-design-vue'

import { useJobHelpStore } from '../composables/useJobHelpStore'
import type { AccountItem } from '../types'

const store = useJobHelpStore()
const FormItem = Form.Item
const TextArea = Input.TextArea

const editorVisible = ref(false)

const summaryItems = computed(() =>
  store.accountSummary.value.map((item) => ({
    ...item,
    label: store.meta[item.platform].name,
    defaultName: item.defaultAccount ? store.accountDisplayName(item.defaultAccount) : '未设置默认账号',
  })),
)

const modalTitle = computed(() => (store.editingAccountId.value ? '编辑账号' : '新增账号'))

const accountColumns = [
  { title: '平台', dataIndex: 'platform', key: 'platform', width: 96 },
  { title: '账号', dataIndex: 'username', key: 'username', width: 200 },
  { title: '手机号', dataIndex: 'phone', key: 'phone', width: 150 },
  { title: '备注', dataIndex: 'remark', key: 'remark', width: 200 },
  { title: '今日投递', dataIndex: 'today_delivered_count', key: 'today_delivered_count', width: 108 },
  { title: '累计投递', dataIndex: 'total_delivered_count', key: 'total_delivered_count', width: 108 },
  { title: '启用', dataIndex: 'is_active', key: 'is_active', width: 86 },
  { title: '默认', dataIndex: 'is_default', key: 'is_default', width: 86 },
  { title: '操作', key: 'actions', width: 280 },
] as const

function openCreateModal() {
  store.startCreateAccount()
  editorVisible.value = true
}

function openEditModal(account: AccountItem) {
  store.startEditAccount(account)
  editorVisible.value = true
}

function closeEditor() {
  editorVisible.value = false
  store.startCreateAccount(store.accountForm.platform)
}

async function submitEditor() {
  const result = await store.submitAccount()
  if (!store.errorMessage.value) {
    editorVisible.value = false
    if (result.createdAccount) {
      await nextTick()
      await store.promptConfigureCreatedAccount(result.createdAccount)
    }
  }
}

onMounted(() => {
  if (!store.allAccounts.value.length) {
    void store.loadAccounts()
  }
})
</script>

<template>
  <section class="accounts-shell">
    <Card :bordered="false" class="accounts-hero-card">
      <div class="accounts-hero">
        <div>
          <span class="accounts-kicker">Accounts</span>
          <h2>浏览器缓存账号</h2>
          <p>每个账号绑定独立缓存目录，任务启动时选择账号；开启历史投递去重后，当前账号和其他账号的历史记录都会基于这里参与判断。</p>
        </div>
        <div class="accounts-hero-actions">
          <Button type="primary" @click="openCreateModal">新增账号</Button>
          <Button @click="store.refreshAll">刷新</Button>
        </div>
      </div>

      <div class="accounts-summary-grid">
        <article v-for="item in summaryItems" :key="item.platform" class="accounts-summary-card">
          <span>{{ item.label }}</span>
          <strong>{{ item.total }}</strong>
          <small>默认账号：{{ item.defaultName }}</small>
        </article>
      </div>
    </Card>

    <Card :bordered="false" class="accounts-card">
      <div class="accounts-card-header">
        <div class="accounts-card-title">
          <span class="accounts-kicker">List</span>
          <div>
            <h3>账号列表</h3>
            <p>账号信息直接在列表中展开，编辑、登录配置和设为默认都收进每一行操作列。</p>
          </div>
        </div>

        <div class="accounts-filter-tabs">
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'all' }]" @click="store.accountFilter.value = 'all'">全部</button>
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'zhilian' }]" @click="store.accountFilter.value = 'zhilian'">智联</button>
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'qiancheng' }]" @click="store.accountFilter.value = 'qiancheng'">前程</button>
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'liepin' }]" @click="store.accountFilter.value = 'liepin'">猎聘</button>
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'boss' }]" @click="store.accountFilter.value = 'boss'">Boss</button>
          <button :class="['accounts-filter-tab', { active: store.accountFilter.value === 'yupao' }]" @click="store.accountFilter.value = 'yupao'">鱼泡</button>
        </div>
      </div>

      <div class="accounts-table-wrap">
        <Table
          class="accounts-desktop-table"
          :columns="accountColumns"
          :data-source="store.filteredAccounts.value.map((item) => ({ ...item, key: item.id }))"
          :pagination="false"
          size="middle"
          :scroll="{ x: 'max-content' }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'platform'">
              <span class="accounts-platform-pill">{{ store.meta[record.platform].shortName }}</span>
            </template>

            <template v-else-if="column.key === 'username'">
              <div class="accounts-name-cell">
                <strong>{{ record.username || '未命名账号' }}</strong>
                <small>{{ record.profile_dir }}</small>
              </div>
            </template>

            <template v-else-if="column.key === 'remark'">
              {{ record.remark || '-' }}
            </template>

            <template v-else-if="column.key === 'is_active'">
              <span :class="['accounts-flag', record.is_active ? 'accounts-flag--success' : 'accounts-flag--muted']">
                {{ record.is_active ? '启用' : '停用' }}
              </span>
            </template>

            <template v-else-if="column.key === 'is_default'">
              <span :class="['accounts-flag', record.is_default ? 'accounts-flag--success' : 'accounts-flag--muted']">
                {{ record.is_default ? '默认' : '-' }}
              </span>
            </template>

            <template v-else-if="column.key === 'actions'">
              <div class="accounts-row-actions accounts-row-actions--table">
                <Button size="small" @click="openEditModal(record)">编辑</Button>
                <Button
                  size="small"
                  :loading="store.browserConfigLoading.value && store.browserConfigAccountId.value === record.id"
                  @click="store.configureAccountBrowser(record)"
                >
                  登录配置
                </Button>
                <Button size="small" :disabled="record.is_default" @click="store.handleSetDefault(record)">设为默认</Button>
              </div>
            </template>
          </template>
        </Table>
      </div>

      <div class="accounts-mobile-list">
        <article v-for="record in store.filteredAccounts.value" :key="record.id" class="accounts-mobile-card">
          <div class="accounts-mobile-head">
            <span class="accounts-platform-pill">{{ store.meta[record.platform].shortName }}</span>
            <div class="accounts-mobile-badges">
              <span :class="['accounts-flag', record.is_active ? 'accounts-flag--success' : 'accounts-flag--muted']">
                {{ record.is_active ? '启用' : '停用' }}
              </span>
              <span :class="['accounts-flag', record.is_default ? 'accounts-flag--success' : 'accounts-flag--muted']">
                {{ record.is_default ? '默认' : '非默认' }}
              </span>
            </div>
          </div>

          <div class="accounts-name-cell">
            <strong>{{ record.username || '未命名账号' }}</strong>
            <small>{{ record.profile_dir }}</small>
          </div>

          <div class="accounts-mobile-meta">
            <div class="accounts-mobile-meta-item">
              <span>手机号</span>
              <strong>{{ record.phone || '-' }}</strong>
            </div>
            <div class="accounts-mobile-meta-item">
              <span>备注</span>
              <strong>{{ record.remark || '-' }}</strong>
            </div>
            <div class="accounts-mobile-meta-item">
              <span>今日投递</span>
              <strong>{{ record.today_delivered_count }}</strong>
            </div>
            <div class="accounts-mobile-meta-item">
              <span>累计投递</span>
              <strong>{{ record.total_delivered_count }}</strong>
            </div>
          </div>

          <div class="accounts-row-actions">
            <Button size="small" @click="openEditModal(record)">编辑</Button>
            <Button
              size="small"
              :loading="store.browserConfigLoading.value && store.browserConfigAccountId.value === record.id"
              @click="store.configureAccountBrowser(record)"
            >
              登录配置
            </Button>
            <Button size="small" :disabled="record.is_default" @click="store.handleSetDefault(record)">设为默认</Button>
          </div>
        </article>
      </div>
    </Card>

    <Modal
      :open="editorVisible"
      :title="modalTitle"
      :confirm-loading="store.savingAccount.value"
      ok-text="保存"
      cancel-text="取消"
      width="720px"
      @ok="submitEditor"
      @cancel="closeEditor"
    >
      <Form layout="vertical">
        <div class="accounts-form-grid">
          <FormItem label="来源平台">
            <Select
              v-model:value="store.accountForm.platform"
              :options="[
                { value: 'zhilian', label: '智联招聘' },
                { value: 'qiancheng', label: '前程无忧' },
                { value: 'liepin', label: '猎聘' },
                { value: 'boss', label: 'Boss 直聘' },
                { value: 'yupao', label: '鱼泡网' },
              ]"
            />
          </FormItem>

          <FormItem label="手机号">
            <Input v-model:value="store.accountForm.phone" placeholder="账号手机号" />
          </FormItem>

          <FormItem label="用户名">
            <Input v-model:value="store.accountForm.username" placeholder="显示名称" />
          </FormItem>

          <FormItem label="备注" class="accounts-span-full">
            <TextArea v-model:value="store.accountForm.remark" :rows="4" placeholder="例如 主投前端 / 校招号 / 备用号" />
          </FormItem>
        </div>

        <div class="accounts-switch-grid">
          <div class="accounts-switch-card">
            <div>
              <strong>默认账号</strong>
              <p>启动任务时优先被选中。</p>
            </div>
            <Switch v-model:checked="store.accountForm.is_default" />
          </div>

          <div class="accounts-switch-card">
            <div>
              <strong>启用状态</strong>
              <p>停用后仍保留缓存目录，但不作为常规投递账号。</p>
            </div>
            <Switch v-model:checked="store.accountForm.is_active" />
          </div>
        </div>
      </Form>
    </Modal>
  </section>
</template>

<style scoped>
.accounts-shell {
  display: grid;
  gap: 18px;
  min-width: 0;
  width: 100%;
  max-width: 100%;
}

.accounts-hero-card,
.accounts-card {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  border-radius: 24px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.accounts-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: flex-start;
}

.accounts-hero > :first-child {
  min-width: 0;
}

.accounts-kicker {
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

.accounts-hero h2,
.accounts-card-title h3 {
  margin: 10px 0 0;
  color: #172554;
  letter-spacing: -0.04em;
}

.accounts-hero p,
.accounts-card-title p,
.accounts-switch-card p {
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.65;
}

.accounts-hero-actions,
.accounts-row-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.accounts-hero-actions {
  justify-content: flex-end;
  align-self: start;
}

.accounts-summary-grid {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.accounts-summary-card {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 6px;
}

.accounts-summary-card span,
.accounts-summary-card small {
  color: #64748b;
}

.accounts-summary-card strong {
  font-size: 1.4rem;
  color: #0f172a;
}

.accounts-card-title {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.accounts-card-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: start;
  margin-bottom: 18px;
}

.accounts-filter-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  max-width: 100%;
  justify-content: flex-end;
  align-self: start;
}

.accounts-filter-tab {
  border: 0;
  border-radius: 999px;
  padding: 8px 14px;
  background: #eef5ff;
  color: #64748b;
  cursor: pointer;
  font-weight: 700;
}

.accounts-filter-tab.active {
  background: linear-gradient(135deg, #2563eb, #60a5fa);
  color: #fff;
}

.accounts-platform-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
  font-size: 0.82rem;
  font-weight: 800;
}

.accounts-name-cell {
  display: grid;
  gap: 4px;
}

.accounts-name-cell strong {
  color: #0f172a;
}

.accounts-name-cell small {
  color: #64748b;
  word-break: break-all;
}

.accounts-flag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 54px;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
}

.accounts-flag--success {
  background: rgba(34, 197, 94, 0.14);
  color: #15803d;
}

.accounts-flag--muted {
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
}

.accounts-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.accounts-table-wrap {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
}

.accounts-desktop-table {
  display: block;
  min-width: 0;
  width: 100%;
}

.accounts-row-actions--table {
  flex-wrap: nowrap;
}

.accounts-mobile-list {
  display: none;
  gap: 12px;
}

.accounts-mobile-card {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: grid;
  gap: 12px;
}

.accounts-mobile-head,
.accounts-mobile-badges {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.accounts-mobile-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.accounts-mobile-meta-item {
  display: grid;
  gap: 4px;
}

.accounts-mobile-meta-item span {
  color: #64748b;
  font-size: 0.82rem;
}

.accounts-mobile-meta-item strong {
  color: #0f172a;
  word-break: break-word;
}

.accounts-span-full {
  grid-column: 1 / -1;
}

.accounts-switch-grid {
  display: grid;
  gap: 12px;
  margin-top: 6px;
}

.accounts-switch-card {
  padding: 16px;
  border-radius: 18px;
  border: 1px solid #dbeafe;
  background: linear-gradient(180deg, #ffffff, #f8fbff);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

:deep(.ant-card-body) {
  padding: 18px 20px 20px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}

:deep(.ant-form-item-label > label) {
  color: #334155;
  font-weight: 600;
}

:deep(.ant-input),
:deep(.ant-input-textarea textarea),
:deep(.ant-select-selector) {
  border-radius: 14px !important;
  border-color: #d9e8ff !important;
  box-shadow: none !important;
}

:deep(.ant-table-wrapper) {
  min-width: 0;
  width: max-content;
  min-width: 100%;
}

:deep(.ant-table-content) {
  overflow-x: auto !important;
  overflow-y: hidden !important;
  max-width: 100%;
}

:deep(.ant-table-container) {
  min-width: 1320px;
}

:deep(.ant-table-cell) {
  white-space: nowrap;
}

:deep(.ant-table-cell .accounts-name-cell small) {
  white-space: normal;
}

@media (max-width: 1480px) {
  .accounts-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .accounts-form-grid {
    grid-template-columns: 1fr;
  }

  .accounts-hero,
  .accounts-card-header {
    grid-template-columns: 1fr;
  }

  .accounts-hero-actions,
  .accounts-filter-tabs {
    justify-content: flex-start;
  }
}

@media (max-width: 900px) {
  .accounts-summary-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .accounts-desktop-table {
    display: none;
  }

  .accounts-mobile-list {
    display: grid;
  }

  .accounts-card-title {
    flex-direction: column;
  }
}

@media (max-width: 640px) {
  .accounts-mobile-meta {
    grid-template-columns: 1fr;
  }

  .accounts-hero-actions,
  .accounts-row-actions,
  .accounts-switch-card {
    flex-direction: column;
  }

  :deep(.ant-modal) {
    max-width: calc(100vw - 24px);
  }

  :deep(.ant-modal-content) {
    margin-inline: 0;
  }
}
</style>
