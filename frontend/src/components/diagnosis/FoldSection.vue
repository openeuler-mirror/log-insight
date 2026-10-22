<script setup lang="ts">
/* 可折叠小节: 小箭头 + 标题, 点击整行展开/收起 */
import { ref } from 'vue'
import FoldChevron from './FoldChevron.vue'

const props = defineProps<{ title: string; defaultOpen?: boolean }>()
const open = ref(!!props.defaultOpen)
</script>

<template>
  <div class="fold-sec">
    <button class="fold-head" type="button" @click="open = !open">
      <FoldChevron :open="open" />
      <span class="fold-title">{{ title }}</span>
      <slot name="meta" />
    </button>
    <div v-if="open" class="fold-body">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.fold-sec {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fold-head {
  display: flex;
  align-items: center;
  gap: 4px;
  align-self: flex-start;
  padding: 2px 8px 2px 0;
  border: none;
  background: transparent;
  color: var(--text-3);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
}
.fold-head:hover {
  color: var(--text-1);
}
.fold-title {
  line-height: 18px;
}
.fold-body {
  padding-left: 22px;
}
</style>
