<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Button, Card, Form, Input, InputNumber, Switch, message } from 'ant-design-vue'

import { fetchLLMConfig, updateLLMConfig, testLLMConfig } from '../api'
import type { LLMConfig, LLMConfigUpdatePayload, LLMTestResult } from '../types'

const FormItem = Form.Item
const TextArea = Input.TextArea

const config = reactive<LLMConfigUpdatePayload>({
  base_url: '',
  api_key: '',
  model: '',
  temperature: 0.7,
  enabled: false,
})

const maskedKey = ref('')
const loading = ref(false)
const testing = ref(false)
const testResult = ref<LLMTestResult | null>(null)

async function loadConfig() {
  loading.value = true
  try {
    const data: LLMConfig = await fetchLLMConfig()
    config.base_url = data.base_url
    config.api_key = ''
    config.model = data.model
    config.temperature = data.temperature
    config.enabled = data.enabled
    maskedKey.value = data.api_key_masked
  } catch (error) {
    message.error(error instanceof Error ? error.message : '加载配置失败')
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  loading.value = true
  try {
    const data: LLMConfig = await updateLLMConfig(config)
    maskedKey.value = data.api_key_masked
    config.api_key = ''
    message.success('LLM 配置已保存')
  } catch (error) {
    message.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    loading.value = false
  }
}

async function testConnection() {
  if (loading.value) {
    message.warning('请先保存配置再测试连接')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const result: LLMTestResult = await testLLMConfig()
    testResult.value = result
    if (result.success) {
      message.success(result.message)
    } else {
      message.error(result.message)
    }
  } catch (error) {
    testResult.value = { success: false, message: error instanceof Error ? error.message : '测试失败' }
    message.error(testResult.value.message)
  } finally {
    testing.value = false
  }
}

onMounted(() => {
  void loadConfig()
})
</script>

<template>
  <section class="settings-shell">
    <Card :bordered="false" class="settings-hero-card">
      <div class="settings-hero">
        <div>
          <span class="settings-kicker">Settings</span>
          <h2>AI 助手设置</h2>
          <p>配置 OpenAI 兼容的 LLM 接口后，分析面板可基于投递数据与简历生成薪资区间、投递时机等智能建议。api_key 仅保存在本地，不会上传到第三方。</p>
        </div>
        <div class="settings-hero-actions">
          <Button type="primary" :loading="loading" @click="saveConfig">保存配置</Button>
          <Button :loading="testing" @click="testConnection">测试连接</Button>
        </div>
      </div>
    </Card>

    <Card :bordered="false" class="settings-card">
      <Form layout="vertical">
        <div class="settings-form-grid">
          <FormItem label="启用 AI 建议" class="settings-switch-cell">
            <div class="settings-switch-line">
              <Switch v-model:checked="config.enabled" />
              <span class="settings-switch-hint">{{ config.enabled ? '已开启' : '已关闭' }}</span>
            </div>
          </FormItem>

          <FormItem label="API Base URL" class="settings-span-full">
            <Input v-model:value="config.base_url" placeholder="例如 https://api.openai.com/v1" />
          </FormItem>

          <FormItem label="API Key" class="settings-span-full">
            <Input v-model:value="config.api_key" type="password" autocomplete="off" :placeholder="maskedKey ? `已保存 (${maskedKey})，留空则不修改` : 'sk-...'" />
          </FormItem>

          <FormItem label="模型名称" class="settings-span-full">
            <Input v-model:value="config.model" placeholder="例如 gpt-4o-mini / deepseek-chat" />
          </FormItem>

          <FormItem label="Temperature" class="settings-switch-cell">
            <InputNumber v-model:value="config.temperature" :min="0" :max="2" :step="0.1" style="width: 100%" />
          </FormItem>
        </div>
      </Form>

      <div v-if="testResult" :class="['settings-test-result', testResult.success ? 'settings-test-result--ok' : 'settings-test-result--err']">
        <strong>{{ testResult.success ? '连接成功' : '连接失败' }}</strong>
        <span>{{ testResult.message }}</span>
      </div>
    </Card>
  </section>
</template>

<style scoped>
.settings-shell {
  display: grid;
  gap: 18px;
  min-width: 0;
  width: 100%;
}

.settings-hero-card,
.settings-card {
  width: 100%;
  min-width: 0;
  border-radius: 20px;
  box-shadow: 0 16px 36px rgba(29, 78, 146, 0.08);
}

.settings-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: flex-start;
}

.settings-hero > :first-child {
  min-width: 0;
}

.settings-kicker {
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

.settings-hero h2 {
  margin: 10px 0 0;
  color: #172554;
  letter-spacing: -0.04em;
}

.settings-hero p {
  margin: 6px 0 0;
  color: #64748b;
  line-height: 1.65;
}

.settings-hero-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-self: start;
}

.settings-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-span-full {
  grid-column: 1 / -1;
}

.settings-switch-cell {
  display: flex;
  flex-direction: column;
}

.settings-switch-line {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 32px;
}

.settings-switch-hint {
  color: #64748b;
  font-size: 0.88rem;
}

.settings-test-result {
  margin-top: 16px;
  padding: 14px 18px;
  border-radius: 14px;
  display: flex;
  gap: 10px;
  align-items: baseline;
}

.settings-test-result--ok {
  background: rgba(34, 197, 94, 0.1);
  color: #15803d;
}

.settings-test-result--err {
  background: rgba(239, 68, 68, 0.1);
  color: #b91c1c;
}

@media (max-width: 880px) {
  .settings-hero,
  .settings-form-grid {
    grid-template-columns: 1fr;
  }

  .settings-hero-actions {
    justify-content: flex-start;
  }
}

:deep(.ant-input),
:deep(.ant-input-password input),
:deep(.ant-input-number-input) {
  border-radius: 12px !important;
}
</style>
