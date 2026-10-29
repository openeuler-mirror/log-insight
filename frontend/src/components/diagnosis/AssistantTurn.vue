<script setup lang="ts">
/* 一轮助手回复: 正文常显, 思考/工具/计划折叠, 轮末用量行 */
import { computed, ref, watch } from 'vue'
import FoldChevron from './FoldChevron.vue'
import FoldSection from './FoldSection.vue'
import type { ChatMessage, KbBlock, PlanBlock, ToolBlock } from '@/api/diagnosis'
import Icon, { type IconName } from '@/components/Icon.vue'
import MarkdownView from '@/components/MarkdownView.vue'

const STEP_MARK: Record<string, IconName> = { completed: 'check', in_progress: 'activity' }

const props = defineProps<{ message: ChatMessage; running?: boolean }>()

const expanded = ref(false)
const kbOpen = ref(false)
watch(() => props.running, (running) => {
  if (running) expanded.value = true
}, { immediate: true })

const text = computed(() =>
  props.message.blocks
    .filter((b): b is { kind: 'text'; text: string } => b.kind === 'text')
    .map((b) => b.text)
    .join('\n\n'),
)
const thoughts = computed(() =>
  props.message.blocks
    .filter((b): b is { kind: 'thought'; text: string } => b.kind === 'thought')
    .map((b) => b.text),
)
const tools = computed(() =>
  props.message.blocks.filter((b): b is ToolBlock => b.kind === 'tool'),
)
const plans = computed(() =>
  props.message.blocks.filter((b): b is PlanBlock => b.kind === 'plan'),
)
const kbBlock = computed(() =>
  props.message.blocks.find((b): b is KbBlock => b.kind === 'kb'),
)
const processCount = computed(() => thoughts.value.length + tools.value.length + plans.value.length)
const openState = computed(() => expanded.value || !!props.running)
const toolsDone = computed(() => tools.value.filter((t) => t.status === 'done').length)

const usage = computed(() => {
  const u = (props.message.usage || {}) as Record<string, unknown>
  const num = (...keys: string[]) => {
    for (const key of keys) {
      const value = u[key]
      if (value != null) return Number(value) || 0
    }
    return 0
  }
  return {
    input: num('inputTokens', 'input_tokens'),
    output: num('outputTokens', 'output_tokens'),
    cached: num('cachedReadTokens', 'cached_read_tokens'),
    thought: num('thoughtTokens', 'thought_tokens'),
    total: num('totalTokens', 'total_tokens'),
  }
})
const hasUsage = computed(() => usage.value.total > 0 || usage.value.input > 0 || usage.value.output > 0)
const fmt = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="turn">
    <div v-if="kbBlock && (kbBlock.citations?.length || kbBlock.note)" class="kb">
      <button type="button" class="kb-head" @click="kbOpen = !kbOpen">
        <FoldChevron :open="kbOpen" />
        <span>
          知识库引用
          <template v-if="kbBlock.citations?.length"> · {{ kbBlock.citations.length }} 条</template>
        </span>
      </button>
      <div v-if="kbOpen" class="kb-body">
        <p v-if="kbBlock.note" class="kb-note">{{ kbBlock.note }}</p>
        <details v-for="c in kbBlock.citations" :key="c.chunkId || c.index" class="cite">
          <summary>
            <b>[{{ c.index }}]</b>
            {{ c.title }}
            <span class="meta">{{ c.kbName }}</span>
          </summary>
          <MarkdownView class="cite-md" :text="c.content" />
        </details>
      </div>
    </div>

    <div v-if="processCount" class="process">
      <button class="p-head" type="button" @click="expanded = !expanded">
        <FoldChevron :open="openState" />
        <span class="p-title">
          {{ running ? '正在执行' : '过程' }} · {{ processCount }} 项
        </span>
        <span v-if="tools.length" class="p-meta">工具 {{ toolsDone }}/{{ tools.length }}</span>
      </button>

      <div v-if="openState" class="p-body">
        <FoldSection v-for="(t, i) in thoughts" :key="`t${i}`" title="思考" :default-open="!!running">
          <div class="thought-text">{{ t }}</div>
        </FoldSection>

        <FoldSection v-for="plan in plans" :key="`p${plan.entries.length}`" title="诊断步骤" :default-open="true">
          <ol class="steps">
            <li v-for="(entry, i) in plan.entries" :key="i" :class="entry.status">
              <span class="mark"><Icon :name="STEP_MARK[entry.status] ?? 'circle'" :size="12" /></span>
              <span>{{ entry.content }}</span>
            </li>
          </ol>
        </FoldSection>

        <div v-for="tool in tools" :key="tool.id" class="tool" :class="tool.status">
          <div class="tool-head">
            <span class="tool-dot"></span>
            <span class="tool-name">{{ tool.name || tool.title || '工具' }}</span>
            <span class="tool-status">{{ tool.status === 'done' ? '完成' : tool.status === 'failed' ? '失败' : '运行中' }}</span>
          </div>
          <div v-if="tool.input" class="tool-line">{{ tool.input }}</div>
          <FoldSection v-if="tool.output" title="查看输出">
            <pre>{{ tool.output }}</pre>
          </FoldSection>
        </div>
      </div>
    </div>

    <MarkdownView v-if="text" class="text" :text="text" />
    <div v-else-if="running && !processCount" class="typing">正在思考…</div>

    <div v-if="message.error" class="err">{{ message.error }}</div>

    <div v-if="hasUsage" class="usage">
      <span>输入 {{ fmt(usage.input) }}</span>
      <span>输出 {{ fmt(usage.output) }}</span>
      <span v-if="usage.cached">缓存读 {{ fmt(usage.cached) }}</span>
      <span v-if="usage.thought">思考 {{ fmt(usage.thought) }}</span>
      <span class="sum">合计 {{ fmt(usage.total) }} tokens</span>
    </div>
  </div>
</template>

<style scoped>
.turn {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.kb {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--primary-soft);
  overflow: hidden;
}
.kb-head {
  display: flex; align-items: center; gap: 4px; width: 100%;
  padding: 5px 10px 5px 6px; border: none; background: transparent;
  font: inherit; font-size: 12px; color: var(--primary); cursor: pointer; text-align: left;
}
.kb-body { border-top: 1px solid var(--primary-tint); padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 6px; }
.kb-note { margin: 0; font-size: 12px; color: var(--text-2); }
.cite { font-size: 12px; background: var(--surface); border-radius: var(--radius); padding: 6px 8px; }
.cite summary { cursor: pointer; color: var(--text-1); }
.cite .meta { color: var(--text-3); margin-left: 6px; }
.cite .cite-md {
  margin-top: 6px;
  font-size: 12px;
  max-height: 160px;
  overflow: auto;
}
.process {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg);
  overflow: hidden;
}
.p-head {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 5px 10px 5px 6px;
  border: none;
  background: transparent;
  font: inherit;
  font-size: 12px;
  color: var(--text-3);
  cursor: pointer;
  text-align: left;
}
.p-head:hover {
  background: rgba(15, 23, 42, 0.04);
}
.p-title {
  flex: 1;
  min-width: 0;
}
.p-meta {
  flex: none;
  font-size: 10.5px;
  color: var(--text-4);
}
.p-body {
  border-top: 1px solid var(--border);
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.thought-text {
  margin-top: 6px;
  padding: 8px 10px;
  border-left: 2px solid var(--border-strong);
  color: var(--text-3);
  font-size: 12px;
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
}
.steps {
  list-style: none;
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-3);
}
.steps li {
  display: flex;
  gap: 6px;
}
.steps li.in_progress {
  color: var(--text-1);
  font-weight: 500;
}
.steps .mark {
  flex: none;
  width: 12px;
  text-align: center;
  display: inline-flex;
  align-items: center;
}
.tool {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 7px 10px;
  background: var(--surface);
}
.tool-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.tool-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
}
.tool.done .tool-dot {
  background: var(--success);
}
.tool.failed .tool-dot {
  background: var(--danger);
}
.tool-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-status {
  font-size: 10.5px;
  color: var(--text-4);
}
.tool-line {
  margin-top: 4px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool pre {
  margin-top: 6px;
  max-height: 220px;
  overflow: auto;
  font-size: 11px;
  white-space: pre-wrap;
  color: var(--text-3);
}
.text {
  font-size: 13.5px;
  color: var(--text-1);
}
.typing {
  font-size: 12px;
  color: var(--text-4);
}
.err {
  color: var(--danger);
  font-size: 12.5px;
}
.usage {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 10.5px;
  color: var(--text-4);
}
.usage .sum {
  color: var(--text-3);
}
</style>
