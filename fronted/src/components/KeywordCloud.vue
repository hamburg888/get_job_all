<script setup lang="ts">
import * as echarts from 'echarts'
import 'echarts-wordcloud'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { Tooltip } from 'ant-design-vue'
import { ExclamationCircleOutlined } from '@ant-design/icons-vue'

import type { StatsBucket } from '../types'

const props = defineProps<{
  items: StatsBucket[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const chartInstance = shallowRef<echarts.ECharts | null>(null)
let resizeObserver: ResizeObserver | null = null

const palette = ['#1d4ed8', '#2563eb', '#3b82f6', '#0f766e', '#0284c7', '#4f46e5', '#0369a1', '#1e40af']

const displayItems = computed(() => props.items.slice(0, 48))

const headlineItems = computed(() => displayItems.value.slice(0, 3))

const chartOption = computed<Record<string, unknown>>(() => {
  const values = displayItems.value.map((item) => item.value)
  const max = Math.max(1, ...values)
  const min = Math.min(...values, max)
  const fontMin = 18
  const fontMax = 52
  const range = Math.max(1, max - min)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(15, 23, 42, 0.92)',
      borderWidth: 0,
      padding: [10, 14],
      textStyle: {
        color: '#e2e8f0',
        fontSize: 12,
      },
      formatter: (params: { name?: string; value?: number }) => `${params.name ?? ''}<br/>出现 ${params.value ?? 0} 次`,
    },
    series: [
      {
        type: 'wordCloud',
        shape: 'circle',
        left: 'center',
        top: 'center',
        width: '100%',
        height: '100%',
        sizeRange: [fontMin, fontMax],
        rotationRange: [-12, 12],
        rotationStep: 12,
        gridSize: 10,
        drawOutOfBound: false,
        layoutAnimation: true,
        textStyle: {
          fontFamily: "'Microsoft YaHei', 'PingFang SC', sans-serif",
          fontWeight: 700,
        },
        emphasis: {
          focus: 'self',
          textStyle: {
            shadowBlur: 18,
            shadowColor: 'rgba(37, 99, 235, 0.32)',
          },
        },
        data: displayItems.value.map((item, index) => {
          const ratio = (item.value - min) / range
          const fontSize = Math.round(fontMin + ratio * (fontMax - fontMin))
          return {
            name: item.label,
            value: item.value,
            textStyle: {
              color: palette[index % palette.length],
              fontSize,
            },
          }
        }),
      },
    ],
  }
})

async function renderChart() {
  await nextTick()
  if (!chartEl.value || !displayItems.value.length) return

  if (chartInstance.value && chartInstance.value.getDom() !== chartEl.value) {
    chartInstance.value.dispose()
    chartInstance.value = null
  }

  if (!chartInstance.value) {
    chartInstance.value = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  }

  if (!resizeObserver && typeof ResizeObserver !== 'undefined' && chartEl.value) {
    resizeObserver = new ResizeObserver(() => {
      chartInstance.value?.resize()
    })
    resizeObserver.observe(chartEl.value)
  }

  chartInstance.value.setOption(chartOption.value, true)
  chartInstance.value.resize()
}

function disposeChart() {
  resizeObserver?.disconnect()
  resizeObserver = null
  chartInstance.value?.dispose()
  chartInstance.value = null
}

onMounted(async () => {
  await renderChart()
})

watch(
  () => props.items,
  async (items) => {
    if (!items.length) {
      disposeChart()
      return
    }
    await renderChart()
  },
  { deep: true },
)

onBeforeUnmount(() => {
  disposeChart()
})
</script>

<template>
  <article class="keyword-cloud-card">
    <div class="keyword-cloud-header">
      <div class="keyword-cloud-copy">
        <h4>
          岗位标签词云
          <Tooltip title="词云来源于岗位标签字段，并已过滤学历、经验等泛标签。">
            <ExclamationCircleOutlined class="keyword-cloud-tip" />
          </Tooltip>
        </h4>
      </div>
      <div v-if="headlineItems.length" class="keyword-cloud-badges">
        <span v-for="item in headlineItems" :key="item.label" class="keyword-cloud-badge">
          {{ item.label }} · {{ item.value }}
        </span>
      </div>
    </div>

    <div v-if="displayItems.length" class="keyword-cloud-surface">
      <div ref="chartEl" class="keyword-cloud-canvas"></div>
    </div>
    <p v-else class="keyword-empty">暂无词云数据</p>
  </article>
</template>

<style scoped>
.keyword-cloud-card {
  min-height: 100%;
  padding: 6px 0 0;
  display: grid;
  gap: 14px;
}

.keyword-cloud-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.keyword-cloud-copy h4 {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #172554;
  font-size: 0.98rem;
  letter-spacing: -0.03em;
}

.keyword-cloud-tip {
  color: #94a3b8;
  font-size: 0.9rem;
  cursor: help;
  transition: color 0.2s ease;
}

.keyword-cloud-tip:hover {
  color: #2563eb;
}

.keyword-cloud-badges {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.keyword-cloud-badge {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.1);
  color: #1d4ed8;
  font-size: 0.8rem;
  font-weight: 700;
}

.keyword-cloud-surface {
  position: relative;
  min-height: 300px;
  border-radius: 24px;
  overflow: hidden;
  border: 1px solid rgba(191, 219, 254, 0.8);
  background:
    radial-gradient(circle at top, rgba(96, 165, 250, 0.22), transparent 38%),
    radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.14), transparent 40%),
    linear-gradient(180deg, #ffffff 0%, #f6faff 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.keyword-cloud-surface::after {
  content: '';
  position: absolute;
  inset: 14px;
  border-radius: 18px;
  border: 1px dashed rgba(96, 165, 250, 0.22);
  pointer-events: none;
}

.keyword-cloud-canvas {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 300px;
}

.keyword-empty {
  margin: 0;
  color: #64748b;
}

@media (max-width: 760px) {
  .keyword-cloud-surface,
  .keyword-cloud-canvas {
    min-height: 260px;
    height: 260px;
  }
}
</style>
