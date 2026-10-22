<script setup lang="ts">
/* 中·文件阅读器: 多标签页 + 只读查看 + 图片预览 */
import { computed } from 'vue'
import Icon from '@/components/Icon.vue'
import type { ReaderTab } from '@/components/diagnosisFiles'

const props = defineProps<{
  tabs: ReaderTab[]
  activeId: string
}>()
const emit = defineEmits<{
  activate: [id: string]
  close: [id: string]
  attach: [tab: ReaderTab]
}>()

const active = computed<ReaderTab | null>(() => props.tabs.find((t) => t.id === props.activeId) ?? null)
const lines = computed(() => (active.value ? active.value.content.split('\n') : []))
const lineNo = computed(() => lines.value.map((_, i) => i + 1).join('\n'))

const copyPath = async () => {
  if (!active.value) return
  try {
    await navigator.clipboard.writeText(active.value.path)
  } catch {
    /* 浏览器不允许时忽略 */
  }
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
        <span class="rd-tab-ico">
          <Icon :name="t.kind === 'image' ? 'image' : 'file'" :size="12" />
        </span>
        <span class="rd-tab-name">{{ t.name }}</span>
        <span class="rd-tab-x" title="关闭" @click.stop="emit('close', t.id)">×</span>
      </button>
      <span v-if="!tabs.length" class="rd-tab-none">未打开文件</span>
    </div>

    <div v-if="active" class="rd-bar">
      <span class="o-mono o-text-3 rd-path" :title="active.path">{{ active.path }}</span>
      <span v-if="active.size" class="o-mono o-text-3 rd-size">{{ active.size }}</span>
      <div class="o-row" style="gap: 6px">
        <button class="o-btn o-btn--sm" title="复制文件路径" @click="copyPath">复制路径</button>
        <button class="o-btn o-btn--sm o-btn--primary" @click="emit('attach', active)">加入本次提问</button>
      </div>
    </div>

    <div class="rd-body">
      <div v-if="!active" class="rd-blank">
        <div class="rd-blank-ico"><Icon name="folder" :size="30" /></div>
        <p class="rd-blank-t">从左侧点开一份日志</p>
        <p class="rd-blank-s">这里会显示日志原文，方便和右侧的结论对照</p>
      </div>

      <div v-else-if="active.kind === 'image'" class="rd-image">
        <img :src="active.content" :alt="active.name" />
      </div>

      <div v-else-if="active.kind === 'notice'" class="rd-blank">
        <div class="rd-blank-ico"><Icon name="archive" :size="30" /></div>
        <p class="rd-blank-t">{{ active.name }}</p>
        <p class="rd-blank-s">{{ active.content }}</p>
      </div>

      <div v-else class="rd-code">
        <pre class="rd-ln">{{ lineNo }}</pre>
        <pre class="rd-pre"><code>{{ active.content }}</code></pre>
      </div>
    </div>

    <div v-if="active" class="rd-foot o-mono o-text-3">
      <span>{{ active.kind === 'text' ? lines.length + ' 行' : active.kind === 'image' ? '图片' : '文件说明' }}</span>
      <span v-if="active.lang">{{ active.lang }}</span>
      <span class="rd-grow"></span>
      <span>只读</span>
    </div>
  </div>
</template>

<style scoped>
.rd {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.rd-tabs {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 8px 0;
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  flex: none;
}
.rd-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px 0 10px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: var(--radius) var(--radius) 0 0;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: 12.5px;
  cursor: pointer;
  white-space: nowrap;
}
.rd-tab:hover {
  background: var(--surface-3);
}
.rd-tab.is-active {
  background: var(--surface);
  border-color: var(--border);
  color: var(--text-1);
  font-weight: 600;
}
.rd-tab-ico {
  display: inline-flex;
  align-items: center;
  color: var(--text-4);
}
.rd-tab-x {
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--text-4);
  font-size: 11px;
}
.rd-tab-x:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.rd-tab-none {
  font-size: 12px;
  color: var(--text-4);
  padding: 6px 8px;
}
.rd-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-3);
  flex: none;
}
.rd-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
}
.rd-size {
  flex: none;
  font-size: 11px;
}
.rd-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--surface);
}
.rd-blank {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 24px;
}
.rd-blank-ico {
  font-size: 34px;
  opacity: 0.8;
}
.rd-blank-t {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-2);
}
.rd-blank-s {
  max-width: 380px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--text-3);
  white-space: pre-wrap;
}
.rd-image {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.rd-image img {
  max-width: 100%;
  max-height: 100%;
  border-radius: var(--radius);
  box-shadow: var(--shadow-1);
}
.rd-code {
  display: flex;
  min-height: 100%;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.7;
}
.rd-ln {
  flex: none;
  margin: 0;
  padding: 10px 8px 10px 12px;
  text-align: right;
  color: var(--text-4);
  background: var(--surface-3);
  border-right: 1px solid var(--border);
  user-select: none;
}
.rd-pre {
  margin: 0;
  padding: 10px 14px;
  white-space: pre;
  color: var(--text-1);
}
.rd-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 5px 12px;
  border-top: 1px solid var(--border);
  background: var(--surface-3);
  font-size: 11px;
  flex: none;
}
.rd-grow {
  flex: 1;
}
</style>
