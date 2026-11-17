<script setup lang="ts">
/* 通用可关闭错误提示条: 图标 + 文案 + × 主动关闭 */
import Icon from '@/components/Icon.vue'

defineProps<{ text: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="ea">
    <Icon name="alert" :size="13" class="ea-ico" />
    <span class="ea-text">{{ text }}</span>
    <button class="ea-close" title="关闭" @click="emit('close')">×</button>
  </div>
</template>

<style scoped>
.ea {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--danger-soft);
  color: var(--danger-deep);
  font-size: 12.5px;
  line-height: 1.6;
}
.ea-ico {
  flex: none;
  margin-top: 2px;
}
.ea-text {
  flex: 1;
  min-width: 0;
  word-break: break-all;
}
.ea-close {
  flex: none;
  border: none;
  background: transparent;
  padding: 0 2px;
  color: inherit;
  font-size: 15px;
  line-height: 1.6;
  cursor: pointer;
  opacity: 0.55;
}
.ea-close:hover {
  opacity: 1;
}
</style>
