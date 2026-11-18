<script setup lang="ts">
/* 输入框上方的任务 dock: 工具进度与排队消息 */
import { computed, ref, watch } from 'vue'
import FoldChevron from './FoldChevron.vue'
import type { ToolBlock } from '@/api/diagnosis'
import { toolLabel } from './toolLabel'

const props = defineProps<{
  tools: ToolBlock[]
  running: boolean
  queue: { id: number; text: string; files: { name: string }[] }[]
}>()

const emit = defineEmits<{
  (e: 'remove-queued', id: number): void
  (e: 'update-queued', id: number, text: string): void
}>()

const expanded = ref(false)

const toolsDone = computed(() => props.tools.filter((t) => t.status === 'done').length)
const visible = computed(
  () => props.running || props.tools.length > 0 || props.queue.length > 0,
)

/* 排队消息行内编辑: 同一时刻只编辑一条; editing 跟随条目 id,
   队头发送 / 前面被删导致下标前移时不会写错对象 */
const editing = ref<number | null>(null)
const editText = ref('')

function startEdit(q: { id: number; text: string }) {
  editing.value = q.id
  editText.value = q.text
}
function cancelEdit() {
  editing.value = null
  editText.value = ''
}
function saveEdit() {
  if (editing.value === null) return
  const text = editText.value.trim()
  if (!text) return
  emit('update-queued', editing.value, text)
  cancelEdit()
}
/* 队列变化: 新增时自动展开; 正在编辑的条目被移除时收起编辑态 */
watch(
  () => props.queue.length,
  (len, prev) => {
    if (len > prev) expanded.value = true
    if (editing.value !== null && !props.queue.some((q) => q.id === editing.value)) cancelEdit()
  },
)
</script>

<template>
  <div v-if="visible" class="dock" :class="{ run: running }">
    <button class="head" type="button" @click="expanded = !expanded">
      <span class="dot" :class="{ run: running }"></span>
      <span v-if="running" class="now">正在处理…</span>
      <span v-else class="now">本轮已完成</span>
      <span v-if="tools.length" class="meta">{{ tools.length }} 个工具 · {{ toolsDone }} 完成</span>
      <span v-if="queue.length" class="meta meta--queue">排队 {{ queue.length }} 条</span>
      <FoldChevron :open="expanded" />
    </button>

    <div v-if="expanded" class="body">
      <div v-if="queue.length" class="queue-sec">
        <div class="queue-title">排队待发送</div>
        <div
          v-for="(q, i) in queue"
          :key="q.id"
          class="queue-item"
          :class="{ 'is-editing': editing === q.id }"
          :title="q.text"
        >
          <template v-if="editing === q.id">
            <textarea
              v-model="editText"
              class="queue-edit o-textarea"
              rows="2"
              @keydown.enter.exact.prevent="saveEdit"
              @keydown.esc="cancelEdit"
            ></textarea>
            <div class="queue-ops">
              <button type="button" class="q-btn q-btn--primary" @click="saveEdit">保存</button>
              <button type="button" class="q-btn" @click="cancelEdit">取消</button>
            </div>
          </template>
          <template v-else>
            <button type="button" class="queue-text" :title="'点击编辑'" @click="startEdit(q)">
              <span class="queue-index">{{ i + 1 }}</span>
              <span class="queue-body">{{ q.text }}</span>
            </button>
            <div class="queue-ops">
              <button type="button" class="q-btn" @click="startEdit(q)">编辑</button>
              <button type="button" class="q-btn q-btn--danger" @click="emit('remove-queued', q.id)">
                删除
              </button>
            </div>
          </template>
        </div>
      </div>

      <div v-if="tools.length" class="tools">
        <span
          v-for="tool in tools"
          :key="tool.id"
          class="tool"
          :class="tool.status"
          :title="tool.output || tool.input"
        >
          {{ toolLabel(tool.name || tool.title) }}
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
.meta--queue {
  color: var(--primary);
}
.body {
  border-top: 1px solid var(--border);
  padding: 8px 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.queue-sec {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.queue-title {
  font-size: 11px;
  color: var(--text-4);
}
.queue-item {
  align-self: stretch;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface-3);
  padding: 1px 4px 1px 8px;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
}
.queue-item.is-editing {
  align-self: stretch;
  flex-direction: column;
  align-items: stretch;
  padding: 3px 6px;
}
.queue-text {
  display: flex;
  gap: 6px;
  align-items: center;
  flex: 1;
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  font: inherit;
  font-size: 12px;
  color: var(--text-2);
  text-align: left;
  cursor: text;
}
.queue-text:hover .queue-body {
  color: var(--text-1);
}
.queue-index {
  flex: none;
  min-width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--surface);
  border: 1px solid var(--border);
  font-size: 10px;
  line-height: 14px;
  text-align: center;
  color: var(--text-3);
}
.queue-body {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.6;
}
.queue-edit {
  width: 100%;
  min-height: 0;
  padding: 3px 6px;
  font-size: 12px;
  line-height: 1.5;
  resize: none;
}
.queue-ops {
  display: flex;
  flex: none;
  justify-content: flex-end;
  gap: 4px;
}
.q-btn {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-3);
  font: inherit;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.q-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.q-btn--primary {
  border-color: var(--primary);
  color: var(--primary);
}
.q-btn--danger:hover {
  border-color: var(--danger);
  color: var(--danger);
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
