<script setup lang="ts">
defineProps<{
  items: Array<{
    id: number
    message: string
    tone: 'info' | 'success' | 'warning' | 'danger'
  }>
}>()
</script>

<template>
  <div v-if="items.length" class="toast-stack">
    <article v-for="item in items" :key="item.id" :class="['toast-item', `toast-${item.tone}`]">
      {{ item.message }}
    </article>
  </div>
</template>
