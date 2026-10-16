<script setup lang="ts">
/* 性能分析: 汇总性能相关分析工具 (sar / 网络抓包 / blktrace / 中断计数变化) */
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PerfSarPanel from '@/components/PerfSarPanel.vue'
import PerfNetcap from '@/components/PerfNetcap.vue'
import PerfBlktrace from '@/components/PerfBlktrace.vue'
import PerfInterrupts from '@/components/PerfInterrupts.vue'

type TabKey = 'sar' | 'netcap' | 'blktrace' | 'interrupt'
const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'sar', label: 'sar 性能分析', icon: '📈' },
  { key: 'netcap', label: '网络抓包分析', icon: '🔎' },
  { key: 'blktrace', label: 'blktrace 分析', icon: '💽' },
  { key: 'interrupt', label: '中断计数变化', icon: '⚡' },
]

const route = useRoute()
const router = useRouter()
const tab = ref<TabKey>('sar')

onMounted(() => {
  const q = route.query.tab as string | undefined
  if (q && TABS.some((t) => t.key === q)) tab.value = q as TabKey
})

watch(tab, (v) => {
  /* 同步到地址栏, 便于分享/刷新保持 (hash 路由下不触发重载) */
  if (route.query.tab !== v) router.replace({ query: { ...route.query, tab: v } })
})
</script>

<template>
  <div class="perf-tabs">
    <button
      v-for="t in TABS"
      :key="t.key"
      class="perf-tab"
      :class="{ 'is-active': tab === t.key }"
      @click="tab = t.key"
    >
      <span class="ico">{{ t.icon }}</span>{{ t.label }}
    </button>
  </div>

  <PerfSarPanel v-if="tab === 'sar'" />
  <PerfNetcap v-else-if="tab === 'netcap'" />
  <PerfBlktrace v-else-if="tab === 'blktrace'" />
  <PerfInterrupts v-else />
</template>

<style scoped>
.perf-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}
.perf-tab {
  border: none;
  background: transparent;
  padding: 10px 16px;
  font: inherit;
  font-size: 14px;
  color: var(--text-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.perf-tab:hover {
  color: var(--primary);
}
.perf-tab.is-active {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 600;
}
.ico {
  font-size: 15px;
}
</style>