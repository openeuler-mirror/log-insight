<script setup lang="ts">
/* 中·文件阅读器: 多标签页 + 文本查看/选中/编辑 + 图片预览
   标签页与内容由父级持有, 本组件只负责渲染与交互, 通过事件回写。 */
import { computed, ref, watch } from 'vue'
import type { ReaderTab } from '@/components/diagnosisFiles'

const props = defineProps<{
  tabs: ReaderTab[]
  activeId: string
}>()

const emit = defineEmits<{
  activate: [id: string]
  close: [id: string]
  edit: [payload: { id: string; content: string }]
  save: [id: string]
  revert: [id: string]
}>()

const mode = ref<'view' | 'edit'>('view')
const draft = ref('')

const active = computed<ReaderTab | null>(() => props.tabs.find((t) => t.id === props.activeId) ?? null)
const dirty = computed(() => !!active.value && active.value.content !== active.value.original)
const lines = computed(() => (active.value ? active.value.content.split('\n') : []))
/* 行号栏宽度: 按最大行号位数预留, 保证各行正文左边界对齐 */
const gutterW = computed(() => `${Math.max(2, String(lines.value.length).length)}ch`)

/* 切换标签页时回到预览态并重置编辑缓冲 */
watch(
  () => active.value?.id,
  () => {
    mode.value = 'view'
    draft.value = active.value?.content ?? ''
  },
  { immediate: true },
)

function toggleMode() {
  if (mode.value === 'view') {
    draft.value = active.value?.content ?? ''
    mode.value = 'edit'
  } else {
    mode.value = 'view'
  }
}
function onInput() {
  const t = active.value
  if (t) emit('edit', { id: t.id, content: draft.value })
}
function onRevert() {
  const t = active.value
  if (!t) return
  draft.value = t.original
  emit('revert', t.id)
}
</script>

<template>
  <div class="rd">
    <div class="rd-tabs">
      <button
        v-for="t in tabs"
        :key="t.id"
        class="rd-tab"
        :class="{ 'is-active': t.id === activeId }"
        :title="t.path"
        @click="emit('activate', t.id)"
      >
        <span class="rd-tab-ico">{{ t.kind === 'image' ? '🖼' : '📄' }}</span>
        <span class="rd-tab-name">{{ t.name }}</span>
        <span v-if="t.content !== t.original" class="rd-tab-dot" title="未保存"></span>
        <span class="rd-tab-x" title="关闭" @click.stop="emit('close', t.id)">×</span>
      </button>
      <span v-if="!tabs.length" class="rd-tab-none">未打开文件</span>
    </div>

    <div v-if="active" class="rd-bar">
      <span class="o-mono o-text-3 rd-path" :title="active.path">{{ active.path }}</span>
      <span v-if="active.size" class="o-mono o-text-3 rd-size">{{ active.size }}</span>
      <div class="o-row" style="gap: 6px">
        <template v-if="active.kind === 'text'">
          <button class="o-btn o-btn--sm" @click="toggleMode">{{ mode === 'edit' ? '预览' : '编辑' }}</button>
          <button class="o-btn o-btn--sm" :disabled="!dirty" @click="emit('save', active.id)">保存</button>
          <button class="o-btn o-btn--sm" :disabled="!dirty" @click="onRevert">撤销</button>
        </template>
        <span v-else class="o-badge o-badge--outline">图片预览</span>
      </div>
    </div>

    <div class="rd-body">
      <div v-if="!active" class="rd-blank">
        <div class="rd-blank-ico">🗂</div>
        <p class="rd-blank-t">从左侧目录点击文件，即可在此打开</p>
        <p class="rd-blank-s">支持文本查看 / 选中 / 编辑，图片直接预览</p>
      </div>

      <div v-else-if="active.kind === 'image'" class="rd-image">
        <img :src="active.content" :alt="active.name" />
      </div>

      <div v-else-if="mode === 'view'" class="rd-code" :style="{ '--ln-w': gutterW }">
        <div v-for="(ln, i) in lines" :key="i" class="rd-line">
          <span class="rd-ln">{{ i + 1 }}</span>
          <span class="rd-txt">{{ ln }}</span>
        </div>
      </div>

      <textarea v-else v-model="draft" class="rd-edit" spellcheck="false" @input="onInput"></textarea>
    </div>

    <div v-if="active" class="rd-foot o-mono o-text-3">
      <span>{{ active.kind === 'text' ? lines.length + ' 行' : '图片' }}</span>
      <span v-if="active.kind === 'text'">{{ active.lang }}</span>
      <span class="rd-grow"></span>
      <span v-if="dirty" class="rd-dirty">● 未保存</span>
      <span v-else>已保存</span>
    </div>
  </div>
</template>

<style scoped>
.rd {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface);
}

/* ---------- 标签页 ---------- */
.rd-tabs {
  flex: none;
  display: flex;
  align-items: stretch;
  gap: 2px;
  padding: 6px 8px 0;
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  background: var(--surface-3);
}
.rd-tab {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 220px;
  padding: 7px 8px 7px 10px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: var(--radius) var(--radius) 0 0;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
}
.rd-tab:hover {
  background: var(--surface);
}
.rd-tab.is-active {
  background: var(--surface);
  border-color: var(--border);
  color: var(--text-1);
  font-weight: 600;
}
.rd-tab-ico {
  flex: none;
  font-size: 11px;
}
.rd-tab-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rd-tab-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warning);
}
.rd-tab-x {
  flex: none;
  width: 15px;
  height: 15px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  color: var(--text-4);
  font-size: 11px;
}
.rd-tab-x:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.rd-tab-none {
  padding: 7px 4px;
  font-size: 12.5px;
  color: var(--text-4);
}

/* ---------- 工具条 ---------- */
.rd-bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
}
.rd-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rd-size {
  flex: none;
}

/* ---------- 内容区 ---------- */
.rd-body {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.rd-code {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 14px 0;
  background: var(--surface);
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.7;
}
.rd-line {
  display: flex;
  align-items: flex-start;
}
.rd-ln {
  flex: none;
  width: calc(var(--ln-w, 2ch) + 24px);
  box-sizing: border-box;
  padding: 0 10px;
  text-align: right;
  color: var(--text-4);
  background: var(--surface-3);
  border-right: 1px solid var(--border);
  user-select: none;
  font-variant-numeric: tabular-nums;
}
.rd-txt {
  flex: 1;
  min-width: 0;
  padding: 0 14px;
  color: var(--text-1);
  /* 主体栏变窄时自动换行而不是横向滚动; 超长单行/长 token 强制断行 */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.rd-edit {
  flex: 1;
  min-width: 0;
  padding: 14px;
  border: none;
  outline: none;
  resize: none;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
  background: var(--surface);
  box-shadow: inset 0 0 0 2px var(--primary-soft);
}
.rd-image {
  flex: 1;
  min-width: 0;
  overflow: auto;
  padding: 18px;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  background: var(--surface-3);
}
.rd-image img {
  max-width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  background: var(--surface);
}
.rd-blank {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-3);
}
.rd-blank-ico {
  font-size: 34px;
  opacity: 0.5;
}
.rd-blank-t {
  font-size: 13.5px;
  color: var(--text-2);
}
.rd-blank-s {
  font-size: 12px;
  color: var(--text-4);
}

/* ---------- 状态条 ---------- */
.rd-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 14px;
  border-top: 1px solid var(--border);
  background: var(--surface-3);
}
.rd-grow {
  flex: 1;
}
.rd-dirty {
  color: var(--warning);
}
</style>
