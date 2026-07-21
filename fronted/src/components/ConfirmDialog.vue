<script setup lang="ts">
defineProps<{
  visible: boolean
  title: string
  message: string
  confirmText: string
  cancelText: string
}>()

const emit = defineEmits<{
  (event: 'resolve', value: boolean): void
}>()
</script>

<template>
  <div v-if="visible" class="modal-overlay" @click.self="emit('resolve', false)">
    <section class="modal-card">
      <div class="modal-copy">
        <h3>{{ title }}</h3>
        <p>{{ message }}</p>
      </div>
      <div class="modal-actions">
        <button class="secondary" @click="emit('resolve', false)">{{ cancelText }}</button>
        <button class="primary" @click="emit('resolve', true)">{{ confirmText }}</button>
      </div>
    </section>
  </div>
</template>
