<script setup lang="ts">
import { computed } from 'vue'

import type { StatsBucket } from '../types'

const props = defineProps<{
  items: StatsBucket[]
}>()

const rankedItems = computed(() => {
  const total = props.items.reduce((sum, item) => sum + item.value, 0)
  const max = Math.max(1, ...props.items.map((item) => item.value), 1)
  return props.items.slice(0, 10).map((item, index) => ({
    ...item,
    index: index + 1,
    width: `${Math.max(18, (item.value / max) * 100)}%`,
    ratio: total ? `${((item.value / total) * 100).toFixed(1)}%` : '0%',
  }))
})
</script>

<template>
  <article class="city-ranking-card">
    <h4>城市热度排行</h4>
    <div v-if="rankedItems.length" class="city-ranking-list">
      <article v-for="item in rankedItems" :key="item.label" class="city-ranking-row">
        <div class="city-ranking-meta">
          <span class="city-ranking-index">{{ String(item.index).padStart(2, '0') }}</span>
          <strong>{{ item.label }}</strong>
          <small>{{ item.value }} 条</small>
        </div>
        <div class="city-ranking-track">
          <span class="city-ranking-bar" :style="{ width: item.width }"></span>
        </div>
        <span class="city-ranking-ratio">{{ item.ratio }}</span>
      </article>
    </div>
    <p v-else class="city-ranking-empty">暂无城市分布数据</p>
  </article>
</template>

<style scoped>
.city-ranking-card {
  min-height: 100%;
  padding: 6px 0 0;
}

.city-ranking-card h4 {
  margin: 0 0 14px;
  color: #172554;
  font-size: 0.98rem;
  letter-spacing: -0.03em;
}

.city-ranking-list {
  display: grid;
  gap: 12px;
}

.city-ranking-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(120px, 0.9fr) 56px;
  gap: 12px;
  align-items: center;
}

.city-ranking-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.city-ranking-meta strong {
  min-width: 0;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.city-ranking-meta small,
.city-ranking-ratio {
  color: #64748b;
  font-size: 0.84rem;
}

.city-ranking-index {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 34px;
  min-height: 28px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.1);
  color: #2563eb;
  font-size: 0.8rem;
  font-weight: 800;
}

.city-ranking-track {
  height: 10px;
  border-radius: 999px;
  background: #e8f1ff;
  overflow: hidden;
}

.city-ranking-bar {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #2563eb, #60a5fa);
}

.city-ranking-empty {
  margin: 0;
  color: #64748b;
}
</style>
