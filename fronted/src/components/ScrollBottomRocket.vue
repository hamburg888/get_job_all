<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const visible = ref(false)
let container: HTMLElement | null = null

function onScroll() {
  if (!container) return
  // show once the user has scrolled down away from the top
  visible.value = container.scrollTop > 360
}

function scrollToTop() {
  if (!container) return
  container.scrollTo({ top: 0, behavior: 'smooth' })
}

onMounted(() => {
  container = document.querySelector('.content-area')
  if (!container) return
  container.addEventListener('scroll', onScroll, { passive: true })
  onScroll()
})

onBeforeUnmount(() => {
  if (container) container.removeEventListener('scroll', onScroll)
  container = null
})
</script>

<template>
  <transition name="rocket-fade">
    <button
      v-if="visible"
      type="button"
      class="scroll-top-rocket"
      title="回到顶部"
      aria-label="回到顶部"
      @click="scrollToTop"
    >
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path
          d="M12 4.2c.5 0 .9.2 1.2.6l5.2 6.3c.4.5.4 1.2-.1 1.6-.5.4-1.2.4-1.6-.1l-3.3-4v9.8c0 .6-.5 1.1-1.1 1.1s-1.1-.5-1.1-1.1V8.6l-3.3 4c-.4.5-1.1.6-1.6.1-.5-.4-.5-1.1-.1-1.6L10.8 4.8c.3-.4.7-.6 1.2-.6z"
          fill="currentColor"
        />
      </svg>
    </button>
  </transition>
</template>

<style scoped>
.scroll-top-rocket {
  position: fixed;
  right: 24px;
  bottom: 88px;
  z-index: 50;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  border: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(145deg, #3b82f6, #2563eb);
  box-shadow: 0 10px 26px rgba(37, 99, 235, 0.38);
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.scroll-top-rocket:hover {
  transform: translateY(-2px) scale(1.05);
  box-shadow: 0 14px 32px rgba(37, 99, 235, 0.46);
}

.scroll-top-rocket:active {
  transform: translateY(0) scale(0.97);
}

.rocket-fade-enter-active,
.rocket-fade-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}

.rocket-fade-enter-from,
.rocket-fade-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.8);
}
</style>
