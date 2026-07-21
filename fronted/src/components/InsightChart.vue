<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'

import type { StatsBucket } from '../types'

use([CanvasRenderer, BarChart, PieChart, GridComponent, TitleComponent, TooltipComponent, LegendComponent])

const props = defineProps<{
  title: string
  type: 'bar' | 'bar-horizontal' | 'pie'
  items: StatsBucket[]
}>()

const option = computed(() => {
  if (props.type === 'pie') {
    return {
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, textStyle: { color: '#667487' } },
      series: [
        {
          type: 'pie',
          radius: ['42%', '68%'],
          data: props.items.map((item) => ({ name: item.label, value: item.value })),
          label: { color: '#18202a' },
        },
      ],
    }
  }

  if (props.type === 'bar-horizontal') {
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 88, right: 16, top: 16, bottom: 16 },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#667487' },
        splitLine: { lineStyle: { color: 'rgba(28, 42, 61, 0.08)' } },
      },
      yAxis: {
        type: 'category',
        data: props.items.map((item) => item.label),
        axisLabel: { color: '#667487', width: 72, overflow: 'truncate' },
        axisLine: { lineStyle: { color: 'rgba(28, 42, 61, 0.12)' } },
      },
      series: [
        {
          data: props.items.map((item) => item.value),
          type: 'bar',
          barWidth: 16,
          itemStyle: {
            borderRadius: [0, 10, 10, 0],
            color: '#2563eb',
          },
        },
      ],
      animationDuration: 450,
    }
  }

  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 36, right: 12, top: 16, bottom: 28 },
    xAxis: {
      type: 'category',
      data: props.items.map((item) => item.label),
      axisLabel: { color: '#667487' },
      axisLine: { lineStyle: { color: 'rgba(28, 42, 61, 0.12)' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#667487' },
      splitLine: { lineStyle: { color: 'rgba(28, 42, 61, 0.08)' } },
    },
    series: [
      {
        data: props.items.map((item) => item.value),
        type: 'bar',
        barWidth: 28,
        itemStyle: {
          borderRadius: [10, 10, 0, 0],
          color: '#1c77d2',
        },
      },
    ],
    animationDuration: 450,
  }
})
</script>

<template>
  <article class="chart-card">
    <h4>{{ title }}</h4>
    <v-chart v-if="items.length" class="chart-canvas" :option="option" autoresize />
    <p v-else class="empty">暂无图表数据</p>
  </article>
</template>
