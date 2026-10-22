<script setup lang="ts">
/* 输入框上方的任务 dock: 诊断步骤与工具进度常驻展示 */
import { computed, ref } from 'vue'
import FoldChevron from './FoldChevron.vue'
import type { PlanBlock, ToolBlock } from '@/api/diagnosis'
import Icon, { type IconName } from '@/components/Icon.vue'

const STEP_MARK: Record<string, IconName> = { completed: 'check', in_progress: 'activity' }

const props = defineProps<{
  plan: PlanBlock | null
  tools: ToolBlock[]
  running: boolean
}>()

const expanded = ref(false)

const entries = computed(() => props.plan?.entries ?? [])
const doneCount = computed(() => entries.value.filter((e) => e.status === 'completed').length)
const doing = computed(() => entries.value.find((e) => e.status === 'in_progress'))
const toolsDone = computed(() => props.tools.filter((t) => t.status === 'done').length)
const visible = computed(() => props.running || entries.value.length > 0 || props.tools.length > 0)
</script>

<template>
  <div v-if="visible" class="dock" :class="{ run: running }">
    <button class="head" type="button" @click="expanded = !expanded">
      <span class="dot" :class="{ run: running }"></span>
      <span v-if="doing" class="now">{{ doing.content }}</span>
      <span v-else-if="running" class="now">正在处理…</span>
      <span v-else class="now">
        {{ entries.length ? `任务 ${doneCount}/${entries.length} 完成` : '本轮已完成' }}
      </span>
      <span v-if="tools.length" class="meta">{{ tools.length }} 个工具 · {{ toolsDone }} 完成</span>
      <FoldChevron :open="expanded" />
    </button>

    <div v-if="expanded" class="body">
      <ol v-if="entries.length" class="steps">
        <li v-for="(entry, i) in entries" :key="i" :class="entry.status">
          <span class="mark">
            <Icon :name="STEP_MARK[entry.status] ?? 'circle'" :size="12" />
          </span>
          <span class="text">{{ entry.content }}</span>
        </li>
      </ol>
      <div v-if="tools.length" class="tools">
        <span
          v-for="tool in tools"
          :key="tool.id"
          class="tool"
          :class="tool.status"
          :title="tool.output || tool.input"
        >
          {{ tool.name || tool.title || '工具' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dock {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}
.dock.run {
  border-color: var(--primary);
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 10px 5px 6px;
  border: none;
  background: transparent;
  font: inherit;
  font-size: 12px;
  color: var(--text-2);
  text-align: left;
  cursor: pointer;
}
.head:hover {
  background: rgba(15, 23, 42, 0.04);
}
.dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
}
.dot.run {
  background: var(--primary);
  animation: pulse 1.2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
.now {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  flex: none;
  font-size: 11px;
  color: var(--text-4);
}
.body {
  border-top: 1px solid var(--border);
  padding: 8px 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.steps {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-3);
}
.steps li {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.steps li.in_progress {
  color: var(--text-1);
  font-weight: 500;
}
.steps li.completed {
  color: var(--text-3);
  text-decoration: line-through;
}
.mark {
  flex: none;
  width: 12px;
  text-align: center;
  display: inline-flex;
  align-items: center;
}
.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tool {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 11px;
  color: var(--text-3);
}
.tool.running {
  border-color: var(--primary);
  color: var(--primary);
}
.tool.failed {
  border-color: var(--danger);
  color: var(--danger);
}
</style>
