<script setup lang="ts">
/* 左·底部运行状态: 模型 / 用量 / 诊断进度 / 工具调用 */
import { computed } from 'vue'
import Icon, { type IconName } from '@/components/Icon.vue'

type TodoStatus = 'done' | 'doing' | 'todo'

interface ModelInfo {
  model: string
  permission: string
  skills: number
  input: number
  output: number
  total: number
}

interface Todo {
  text: string
  status: TodoStatus
}

interface ToolCall {
  name: string
  target: string
  status: 'done' | 'running' | 'failed'
}

const props = defineProps<{
  modelInfo: ModelInfo
  todos: Todo[]
  tools: ToolCall[]
}>()

const todoDone = computed(() => props.todos.filter((t) => t.status === 'done').length)
const TODO_MARK: Record<TodoStatus, IconName> = { done: 'check', doing: 'activity', todo: 'circle' }
const fmtNum = (n: number) => (n || 0).toLocaleString('en-US')
</script>

<template>
  <div class="rp-scroll">
    <div class="rp-card">
      <div class="mi-row"><span class="o-text-3">模型</span><b>{{ modelInfo.model || '未配置' }}</b></div>
      <div class="mi-row"><span class="o-text-3">文件权限</span><b>{{ modelInfo.permission }}</b></div>
      <div class="mi-row"><span class="o-text-3">可用技能</span><b>{{ modelInfo.skills }} 项</b></div>

      <div class="mi-usage">
        <div class="mi-usage-head">
          <span class="o-text-3">本次对话 Token</span>
          <b class="o-mono">{{ fmtNum(modelInfo.total) }}</b>
        </div>
        <div class="mi-usage-foot o-mono o-text-3">
          <span>输入 {{ fmtNum(modelInfo.input) }}</span>
          <span>输出 {{ fmtNum(modelInfo.output) }}</span>
        </div>
      </div>
    </div>

    <div class="rp-section">
      <div class="rp-section-head">
        <span class="pane-title">诊断进度</span>
        <span class="o-mono o-text-3">{{ todos.length ? `${todoDone}/${todos.length}` : '' }}</span>
      </div>
      <div v-if="!todos.length" class="rp-empty">助手会在需要时列出诊断步骤。</div>
      <div v-for="(t, i) in todos" :key="i" class="todo" :class="`todo--${t.status}`">
        <span class="todo-mark"><Icon :name="TODO_MARK[t.status]" :size="12" /></span>
        <span class="todo-text">{{ t.text }}</span>
      </div>
    </div>

    <div class="rp-section">
      <div class="rp-section-head">
        <span class="pane-title">工具调用</span>
        <span class="o-mono o-text-3">{{ tools.length }}</span>
      </div>
      <div v-if="!tools.length" class="rp-empty">还没有调用工具。</div>
      <div v-for="(tl, i) in tools" :key="i" class="tool-row">
        <span class="tool-dot" :class="{ 'is-run': tl.status === 'running', 'is-fail': tl.status === 'failed' }"></span>
        <div class="tool-row-main">
          <span class="tool-name">{{ tl.name }}</span>
          <span class="o-mono o-text-3 tool-args">{{ tl.target }}</span>
        </div>
        <span class="o-mono o-text-3">{{ tl.status === 'running' ? '进行中' : tl.status === 'failed' ? '失败' : '完成' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rp-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rp-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.mi-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12.5px;
  padding: 3px 0;
}
.mi-row b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mi-usage {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.mi-usage-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 12.5px;
}
.mi-usage-foot {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 11px;
}
.rp-section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
}
.rp-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.rp-empty {
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-4);
}
.todo {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.7;
  padding: 2px 0;
}
.todo-mark {
  flex: none;
  width: 14px;
  color: var(--text-4);
  display: inline-flex;
  align-items: center;
}
.todo--done .todo-mark {
  color: var(--success);
}
.todo--doing .todo-mark {
  color: var(--warning);
}
.todo--done .todo-text {
  color: var(--text-3);
  text-decoration: line-through;
}
.todo--doing .todo-text {
  color: var(--primary);
  font-weight: 600;
}
.tool-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--success);
}
.tool-dot.is-run {
  background: var(--warning);
  animation: pulse 1s ease-in-out infinite;
}
.tool-dot.is-fail {
  background: var(--danger);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.tool-name {
  font-weight: 600;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-args {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
}
.tool-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px dashed var(--border);
  font-size: 12.5px;
}
.tool-row:first-of-type {
  border-top: 0;
}
.tool-row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
