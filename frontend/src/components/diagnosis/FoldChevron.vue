<script setup lang="ts">
/* macOS 风格折叠箭头: 纯视觉元素, 点击由外层按钮处理 */
defineProps<{ open: boolean }>()
</script>

<template>
  <span class="o-fold" :class="{ 'is-open': open }" aria-hidden="true">
    <svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6"
      stroke-linecap="round" stroke-linejoin="round">
      <path d="M3.5 1.8 6.8 5 3.5 8.2" />
    </svg>
  </span>
</template>
