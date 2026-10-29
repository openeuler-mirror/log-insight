<script lang="ts">
/* Markdown 渲染: marked 解析 + DOMPurify 白名单消毒; 外链统一新窗口打开 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

function renderMarkdown(text: string): string {
  if (!text) return ''
  const html = marked.parse(text, { async: false, gfm: true, breaks: true }) as string
  return DOMPurify.sanitize(html)
}
</script>

<script setup lang="ts">
/* 流式文本每来一段即增量重渲染, 用于 Agent 会话 */
import { computed } from 'vue'

const props = defineProps<{ text: string }>()

const html = computed(() => renderMarkdown(props.text))
</script>

<template>
  <div class="md" v-html="html"></div>
</template>

<style scoped>
.md {
  line-height: 1.75;
  color: var(--text-1);
  word-break: break-word;
}
.md :deep(> :first-child) { margin-top: 0; }
.md :deep(> :last-child) { margin-bottom: 0; }
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4),
.md :deep(h5),
.md :deep(h6) {
  margin: 18px 0 8px;
  line-height: 1.35;
  font-weight: 600;
  color: var(--text-1);
}
.md :deep(h1) { font-size: 1.32em; }
.md :deep(h2) { font-size: 1.18em; }
.md :deep(h3) { font-size: 1.06em; }
.md :deep(h4), .md :deep(h5), .md :deep(h6) { font-size: 1em; }
.md :deep(p) { margin: 0 0 8px; }
.md :deep(ul),
.md :deep(ol) { margin: 0 0 8px; padding-left: 20px; }
.md :deep(li) { margin: 2px 0; }
.md :deep(li:has(> input[type='checkbox'])) { list-style: none; }
.md :deep(li > p) { margin: 0; }
.md :deep(li > ul),
.md :deep(li > ol) { margin: 2px 0 0; }
.md :deep(blockquote) {
  margin: 0 0 10px;
  padding: 4px 12px;
  border-left: 3px solid var(--border-strong);
  color: var(--text-2);
  background: var(--surface-3);
  border-radius: 0 var(--radius) var(--radius) 0;
}
.md :deep(code) {
  font-family: var(--mono);
  font-size: 0.92em;
  background: var(--surface-3);
  border-radius: 4px;
  padding: 1px 5px;
}
.md :deep(pre) {
  margin: 0 0 10px;
  padding: 10px 12px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: auto;
}
.md :deep(pre code) {
  padding: 0;
  background: none;
  border-radius: 0;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre;
}
.md :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin: 0 0 10px;
  font-size: 0.94em;
}
.md :deep(th),
.md :deep(td) {
  border: 1px solid var(--border);
  padding: 5px 9px;
  text-align: left;
  vertical-align: top;
}
.md :deep(th) { background: var(--surface-3); font-weight: 600; }
.md :deep(a) { color: var(--primary); text-decoration: none; }
.md :deep(a:hover) { text-decoration: underline; }
.md :deep(hr) { border: 0; border-top: 1px solid var(--border); margin: 14px 0; }
.md :deep(img) { max-width: 100%; }
.md :deep(input[type='checkbox']) { margin-right: 4px; vertical-align: middle; }
.md :deep(del) { color: var(--text-3); }
</style>
