<script setup lang="ts">
/* 右·信息面板: 模型信息 / Token 用量 / Todo List / 工具调用
   数据由父级持有, 随流式事件实时刷新, 本组件只负责渲染。 */
import { computed } from 'vue'

type TodoStatus = 'done' | 'doing' | 'todo'

interface ModelInfo {
  name: string
  provider: string
  window: string
  usedPct: number
  input: number
  output: number
  total: number
  cost: string
  latency: string
}

interface Todo {
  text: string
  status: TodoStatus
}

interface ToolCall {
  name: string
  target: string
  status: 'done' | 'running'
  ms: number | null
}

const props = defineProps<{
  modelInfo: ModelInfo
  todos: Todo[]
  tools: ToolCall[]
}>()

const todoDone = computed(() => props.todos.filter((t) => t.status === 'done').length)
const TODO_MARK: Record<TodoStatus, string> = { done: '✓', doing: '◐', todo: '○' }
const fmtNum = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="rp-scroll">
    <div class="rp-card">
      <div class="mi-row"><span class="o-text-3">服务商</span><b>{{ modelInfo.provider }}</b></div>
      <div class="mi-row"><span class="o-text-3">上下文窗口</span><b>{{ modelInfo.window }}</b></div>
      <div class="mi-row"><span class="o-text-3">平均延迟</span><b>{{ modelInfo.latency }}</b></div>
      <div class="mi-row"><span class="o-text-3">累计费用</span><b>{{ modelInfo.cost }}</b></div>

      <div class="mi-usage">
        <div class="mi-usage-head">
          <span class="o-text-3">Token 用量</span>
          <b class="o-mono">{{ fmtNum(modelInfo.total) }}</b>
        </div>
        <div class="bar"><span :style="{ width: modelInfo.usedPct + '%' }"></span></div>
        <div class="mi-usage-foot o-mono o-text-3">
          <span>↑ 输入 {{ fmtNum(modelInfo.input) }}</span>
          <span>↓ 输出 {{ fmtNum(modelInfo.output) }}</span>
        </div>
      </div>
    </div>

    <div class="rp-section">
      <div class="rp-section-head">
        <span class="pane-title">Todo List</span>
        <span class="o-mono o-text-3">{{ todoDone }}/{{ todos.length }}</span>
      </div>
      <div v-for="(t, i) in todos" :key="i" class="todo" :class="`todo--${t.status}`">
        <span class="todo-mark">{{ TODO_MARK[t.status] }}</span>
        <span class="todo-text">{{ t.text }}</span>
      </div>
    </div>

    <div class="rp-section">
      <div class="rp-section-head">
        <span class="pane-title">工具调用</span>
        <span class="o-mono o-text-3">{{ tools.length }}</span>
      </div>
      <div v-for="(tl, i) in tools" :key="i" class="tool-row">
        <span class="tool-dot" :class="{ 'is-run': tl.status === 'running' }"></span>
        <div class="tool-row-main">
          <span class="o-mono tool-name">{{ tl.name }}</span>
          <span class="o-mono o-text-3 tool-args">{{ tl.target }}</span>
        </div>
        <span class="o-mono o-text-3">{{ tl.ms ? tl.ms + 'ms' : '执行中' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rp-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
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
  font-size: 12.5px;
  padding: 3px 0;
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
  margin-bottom: 6px;
}
.bar {
  height: 6px;
  border-radius: 3px;
  background: var(--primary-soft);
  overflow: hidden;
}
.bar span {
  display: block;
  height: 100%;
  background: var(--primary);
  border-radius: 3px;
}
.mi-usage-foot {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
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
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.tool-name {
  font-weight: 600;
  color: var(--primary);
}
.tool-args {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.tool-row-main .tool-args {
  font-size: 11px;
}
</style>
