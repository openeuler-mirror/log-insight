<script setup lang="ts">
/* 中·文件阅读器: 多标签页 + 只读查看 + 图片预览
   本机导入的文件默认只读; 点 ✏️ 申请本机写权限后可编辑, 💾 保存直接写回客户端原文件 */
import { computed, ref, watch } from 'vue'
import Icon from '@/components/Icon.vue'
import type { ReaderTab } from '@/components/diagnosis/diagnosisFiles'
import {
  ensureWritable,
  getLocalHandle,
  handleVersion,
  localStamp,
  writeLocalText,
  type LocalStamp,
} from '@/components/diagnosis/localHandles'
import { rawUrl, writeFile as writeFsFile } from '@/api/fs'
import MarkdownView from '@/components/MarkdownView.vue'
import { copyText } from '@/utils/clipboard'

const props = defineProps<{
  tabs: ReaderTab[]
  activeId: string
}>()
const emit = defineEmits<{
  activate: [id: string]
  close: [id: string]
  attach: [tab: ReaderTab]
  saved: [tab: ReaderTab, text: string]
}>()

const active = computed<ReaderTab | null>(() => props.tabs.find((t) => t.id === props.activeId) ?? null)
const lines = computed(() => (active.value ? active.value.content.split('\n') : []))
const lineNo = computed(() => lines.value.map((_, i) => i + 1).join('\n'))

/* 编辑态按标签 id 保存草稿: 取消/关闭标签即丢弃 */
const editing = ref<Record<string, boolean>>({})
const drafts = ref<Record<string, string>>({})
/* 进入编辑时记录的文件指纹: 保存前比对, 防止覆盖其他进程刚写入的内容 */
const stamps = ref<Record<string, LocalStamp | null>>({})
const busy = ref(false)
const msg = ref('')

/* 只有拖入时拿到本机文件句柄的文件才能写回原文件, 其余保持只读
   (读一次 handleVersion 建立响应式依赖: 句柄登记/失效后按钮立即更新) */
const writable = (t: ReaderTab) => {
  void handleVersion.value
  return t.kind === 'text' && !!getLocalHandle(t.path)
}
const isEditing = (t: ReaderTab) => !!editing.value[t.id]

/* 按扩展名分派富文本展示: md 渲染 Markdown, html 走沙箱 iframe 预览, 其余保持纯文本 */
type DocKind = 'markdown' | 'html'
const DOC_EXT: [DocKind, RegExp][] = [
  ['markdown', /\.(md|markdown)$/i],
  ['html', /\.(html?|xhtml)$/i],
]
const docKind = (t: ReaderTab | null): DocKind | null =>
  t && t.kind === 'text' ? DOC_EXT.find(([, re]) => re.test(t.name || t.path))?.[0] ?? null : null
/* 渲染开关按标签记忆: md/html 默认走渲染视图, 可切回源码 */
const rendered = ref<Record<string, boolean>>({})
const isRendered = (t: ReaderTab) => rendered.value[t.id] !== false
const toggleRender = (t: ReaderTab) => {
  rendered.value[t.id] = !isRendered(t)
}

/* 行数跟随当前内容(编辑态显示草稿行数) */
const lineCount = computed(() => {
  const t = active.value
  if (!t) return 0
  const text = editing.value[t.id] ? drafts.value[t.id] ?? '' : t.content
  return text.split('\n').length
})

/* 底栏状态: 富文本视图显示模式名, 源码/编辑视图显示行数 */
const footLabel = computed(() => {
  const t = active.value
  if (!t) return ''
  if (t.kind === 'image') return '图片'
  if (t.kind === 'html') return '报告预览'
  if (t.kind === 'notice') return '文件说明'
  const doc = docKind(t)
  if (doc && isRendered(t) && !isEditing(t)) return doc === 'markdown' ? 'Markdown 预览' : '网页预览'
  return `${lineCount.value} 行`
})

async function startEdit(t: ReaderTab) {
  msg.value = ''
  const handle = getLocalHandle(t.path)
  if (!handle) {
    msg.value = '未找到该文件的本机句柄（兼容导入或句柄已失效），请重新选择或拖入该文件后再尝试是否可以编辑'
    return
  }
  /* 必须在点击的调用栈里申请权限, 浏览器才会弹出授权框 */
  const state = await ensureWritable(handle)
  if (state !== 'granted') {
    msg.value = '未获得本机文件的修改权限'
    return
  }
  drafts.value[t.id] = t.content
  stamps.value[t.id] = await localStamp(t.path)
  editing.value[t.id] = true
}

function cancelEdit(t: ReaderTab) {
  delete drafts.value[t.id]
  delete stamps.value[t.id]
  delete editing.value[t.id]
  msg.value = ''
}

async function saveEdit(t: ReaderTab) {
  const handle = getLocalHandle(t.path)
  const text = drafts.value[t.id] ?? t.content
  if (!handle) {
    msg.value = '未找到该文件的本机句柄（兼容导入或句柄已失效），请重新选择或拖入该文件后再尝试是否可以编辑'
    return
  }
  busy.value = true
  msg.value = ''
  try {
    /* 编辑期间原文件可能被其他进程改过: 指纹不一致就放弃本次保存, 避免覆盖别人的写入 */
    const before = stamps.value[t.id]
    const now = await localStamp(t.path)
    if (before && now && (before.lastModified !== now.lastModified || before.size !== now.size)) {
      msg.value = '本机文件已被其他程序修改，本次未保存。请取消后重新打开文件再编辑'
      busy.value = false
      return
    }
    /* 先写回客户端电脑上的真实文件 */
    await writeLocalText(t.path, handle, text)
  } catch (e) {
    busy.value = false
    msg.value = `写入本机失败：${e instanceof Error ? e.message : String(e)}`
    return
  }
  emit('saved', t, text)
  delete drafts.value[t.id]
  delete stamps.value[t.id]
  delete editing.value[t.id]
  /* 再同步会话副本, 让助手读到修改后的内容(同步失败不影响本机已写入) */
  try {
    await writeFsFile(t.path, text)
    msg.value = '已写回本机文件'
  } catch {
    msg.value = '已写回本机文件；会话副本同步失败，助手可能仍读到旧内容'
  } finally {
    busy.value = false
  }
}

/* 标签关闭后清理未提交的草稿 */
watch(
  () => props.tabs.map((t) => t.id).join('\n'),
  () => {
    const ids = new Set(props.tabs.map((t) => t.id))
    for (const id of Object.keys(editing.value)) {
      if (ids.has(id)) continue
      delete editing.value[id]
      delete drafts.value[id]
      delete stamps.value[id]
    }
  },
)

/* 复制路径: 非 HTTPS 环境由 util 内部降级 execCommand, 不再静默失败 */
const copyPath = async () => {
  if (active.value) await copyText(active.value.path)
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
        <template v-if="active.kind === 'text'">
          <template v-if="isEditing(active)">
            <button
              class="o-btn o-btn--sm o-btn--primary"
              :disabled="busy"
              title="保存并写回本机原文件"
              @click="saveEdit(active)"
            >{{ busy ? '保存中…' : '💾 保存' }}</button>
            <button class="o-btn o-btn--sm" :disabled="busy" @click="cancelEdit(active)">取消</button>
          </template>
          <button
            v-else
            class="o-btn o-btn--sm"
            :title="writable(active) ? '点击编辑并授权后，保存会写回本机原文件' : '未找到本机文件句柄，重新拖入文件后可编辑并写回'"
            @click="startEdit(active)"
          >✏️ 编辑</button>
        </template>
        <button
          v-if="docKind(active) && !isEditing(active)"
          class="o-btn o-btn--sm"
          :class="{ 'o-btn--primary': isRendered(active) }"
          :title="isRendered(active) ? '切换为源码视图' : docKind(active) === 'markdown' ? '渲染 Markdown' : '沙箱预览网页'"
          @click="toggleRender(active)"
        >{{ isRendered(active) ? '源码' : docKind(active) === 'markdown' ? '预览' : '网页预览' }}</button>
        <button class="o-btn o-btn--sm" title="复制文件路径" @click="copyPath">复制路径</button>
        <button
          v-if="!active.id.startsWith('artifact:')"
          class="o-btn o-btn--sm o-btn--primary"
          @click="emit('attach', active)"
        >加入本次提问</button>
      </div>
    </div>

    <div v-if="active && msg" class="rd-msg">{{ msg }}</div>

    <div class="rd-body">
      <div v-if="!active" class="rd-blank">
        <div class="rd-blank-ico"><Icon name="folder" :size="30" /></div>
        <p class="rd-blank-t">从左侧点开一份日志</p>
        <p class="rd-blank-s">这里会显示日志原文，方便和右侧的结论对照</p>
      </div>

      <div v-else-if="active.kind === 'image'" class="rd-image">
        <img :src="active.content" :alt="active.name" />
      </div>

      <iframe
        v-else-if="active.kind === 'html'"
        class="rd-html"
        sandbox=""
        :srcdoc="active.content"
        :title="active.name"
      ></iframe>

      <div v-else-if="active.kind === 'notice'" class="rd-blank">
        <div class="rd-blank-ico"><Icon name="archive" :size="30" /></div>
        <p class="rd-blank-t">{{ active.name }}</p>
        <p class="rd-blank-s">{{ active.content }}</p>
      </div>

      <div v-else-if="isEditing(active)" class="rd-edit">
        <textarea
          v-model="drafts[active.id]"
          class="rd-editor o-mono"
          spellcheck="false"
          :disabled="busy"
        ></textarea>
      </div>

      <div v-else-if="docKind(active) === 'markdown' && isRendered(active)" class="rd-doc">
        <MarkdownView :text="active.content" />
      </div>

      <div v-else-if="docKind(active) === 'html' && isRendered(active)" class="rd-web">
        <iframe
          :src="rawUrl(active.path)"
          sandbox=""
          referrerpolicy="no-referrer"
          :title="active.name"
        ></iframe>
      </div>

      <div v-else class="rd-code">
        <pre class="rd-ln">{{ lineNo }}</pre>
        <pre class="rd-pre"><code>{{ active.content }}</code></pre>
      </div>
    </div>

    <div v-if="active" class="rd-foot o-mono o-text-3">
      <span>{{ footLabel }}</span>
      <span v-if="active.lang">{{ active.lang }}</span>
      <span class="rd-grow"></span>
      <span v-if="isEditing(active)">编辑中 · 保存将写回本机</span>
      <span v-else-if="writable(active)">可编辑 · 保存写回本机原文件</span>
      <span v-else>只读</span>
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
.rd-html {
  display: block;
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}
.rd-msg {
  flex: none;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-3);
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
}
.rd-edit {
  height: 100%;
  display: flex;
}
.rd-editor {
  flex: 1;
  min-height: 100%;
  margin: 0;
  padding: 10px 14px;
  border: none;
  outline: none;
  resize: none;
  background: var(--surface);
  color: var(--text-1);
  font-size: 12px;
  line-height: 1.7;
  tab-size: 2;
}
.rd-doc {
  max-width: 860px;
  margin: 0 auto;
  padding: 20px 24px;
  font-size: 14px;
}
.rd-web {
  height: 100%;
}
.rd-web iframe {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
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
