<script setup lang="ts">
defineProps<{
  title: string
  message: string
}>()
</script>

<template>
  <div class="modal-overlay">
    <section class="modal-card modal-card--loading">
      <div class="loading-spinner"></div>
      <div class="modal-copy">
        <h3>{{ title }}</h3>
        <p>{{ message }}</p>
      </div>
    </section>
  </div>
</template>
