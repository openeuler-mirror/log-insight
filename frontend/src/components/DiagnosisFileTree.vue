<script setup lang="ts">
/* 左·目录文件树(数据源: 后端 /api/fs)
   - 默认一次性加载 rootPath 的嵌套树; 目录未带 children 时展开再懒加载(文件过多时后端才标记);
   - 顶部「目录 / 文件」按钮打开服务端路径选择弹窗;
   - 收到 SSE 变更事件时, 只刷新受影响且已加载的目录, 磁盘变化实时可见。
   其余交互不变: 点击文件→主体打开; 勾选→多选(目录整选 / 半选); 拖拽→加入提问上下文。 */
import { computed, ref, watch } from 'vue'
import { fetchFile, fetchList, fetchTree, rawUrl, type FsChange, type FsEntry } from '@/api/fs'
import { apiError } from '@/api/http'
import DiagnosisPathPicker from '@/components/DiagnosisPathPicker.vue'
import { fmtSize, type Attach, type FilePayload } from '@/components/diagnosisFiles'

const props = defineProps<{ rootPath: string; activeId?: string; events: FsChange[] }>()
const emit = defineEmits<{ open: [file: Attach, payload?: FilePayload]; 'pick-root': [path: string] }>()

/* ---------------- 树加载 ---------------- */
const root = ref<FsEntry | null>(null)
const expanded = ref<Record<string, boolean>>({})
const loadingPaths = ref<Record<string, boolean>>({})
const loading = ref(false)
const error = ref('')
const truncated = ref(false)
const pickerOpen = ref(false)
const pickerMode = ref<'dir' | 'file'>('dir')

async function loadRoot(path: string) {
  root.value = null
  expanded.value = {}
  truncated.value = false
  error.value = ''
  if (!path) return
  loading.value = true
  try {
    const r = await fetchTree(path)
    root.value = r.root
    truncated.value = r.truncated
    expanded.value = { [r.root.path]: true }
    /* 根目录本身超出一次性加载阈值时(无 children)补一次懒加载, 否则展开态下会空白 */
    if (!Array.isArray(r.root.children)) await loadChildren(r.root)
  } catch (e) {
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

watch(() => props.rootPath, (p) => void loadRoot(p), { immediate: true })

/* ---------------- 展开 / 懒加载 ---------------- */
interface TreeRow {
  node: FsEntry
  depth: number
}
const treeRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (n: FsEntry, depth: number) => {
    out.push({ node: n, depth })
    if (n.kind === 'dir' && expanded.value[n.path] && n.children) {
      for (const c of n.children) walk(c, depth + 1)
    }
  }
  if (root.value) walk(root.value, 0)
  return out
})

async function loadChildren(n: FsEntry) {
  if (n.kind !== 'dir' || Array.isArray(n.children)) return
  loadingPaths.value[n.path] = true
  try {
    const r = await fetchList(n.path)
    n.children = r.entries
  } catch (e) {
    error.value = apiError(e)
  } finally {
    delete loadingPaths.value[n.path]
  }
}

async function toggleExpand(n: FsEntry) {
  if (n.kind !== 'dir') return
  if (expanded.value[n.path]) {
    expanded.value[n.path] = false
    return
  }
  expanded.value[n.path] = true
  await loadChildren(n)
}

/* ---------------- 打开文件 ---------------- */
const attachOf = (n: FsEntry): Attach => ({ id: n.path, name: n.name, path: n.path, size: fmtSize(n.size) })

async function openEntry(n: FsEntry) {
  try {
    const r = await fetchFile(n.path)
    const content = r.kind === 'image' ? rawUrl(n.path) : (r.content ?? '')
    emit('open', attachOf(n), { kind: r.kind, content, lang: r.lang })
  } catch (e) {
    error.value = apiError(e)
  }
}

async function openByPath(path: string) {
  const name = path.split(/[\\/]/).pop() ?? path
  await openEntry({ name, path, kind: 'file', size: 0, mtime: 0 })
}

async function onRowClick(row: TreeRow) {
  if (row.node.kind === 'dir') await toggleExpand(row.node)
  else await openEntry(row.node)
}

/* ---------------- 多选(已加载范围内): 选中目录等价于选中其下全部文件 ---------------- */
const selectedIds = ref<string[]>([])
const fileIdsOf = (n: FsEntry): string[] =>
  n.kind === 'file' ? [n.path] : (n.children ?? []).flatMap(fileIdsOf)
const isChecked = (n: FsEntry) => {
  const ids = fileIdsOf(n)
  return ids.length > 0 && ids.every((id) => selectedIds.value.includes(id))
}
const isIndeterminate = (n: FsEntry) => {
  const ids = fileIdsOf(n)
  const c = ids.filter((id) => selectedIds.value.includes(id)).length
  return c > 0 && c < ids.length
}
function toggleSelect(n: FsEntry) {
  const ids = fileIdsOf(n)
  if (!ids.length) return
  const all = ids.every((id) => selectedIds.value.includes(id))
  selectedIds.value = all
    ? selectedIds.value.filter((id) => !ids.includes(id))
    : Array.from(new Set([...selectedIds.value, ...ids]))
}

/* 全量文件索引(已加载范围内, 便于目录整选 / 拖拽) */
const allFiles = computed<Record<string, Attach>>(() => {
  const map: Record<string, Attach> = {}
  const walk = (n: FsEntry) => {
    if (n.kind === 'file') map[n.path] = attachOf(n)
    else for (const c of n.children ?? []) walk(c)
  }
  if (root.value) walk(root.value)
  return map
})

/* ---------------- 拖拽: 已多选时拖动选区, 否则拖动该行对应的文件 ---------------- */
function onDragStart(row: TreeRow, e: DragEvent) {
  const ids =
    row.node.kind === 'file' && selectedIds.value.includes(row.node.path) && selectedIds.value.length > 1
      ? selectedIds.value
      : fileIdsOf(row.node)
  const files = ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f)
  if (!files.length) return
  e.dataTransfer?.setData('text/plain', JSON.stringify(files))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}

/* ---------------- 路径选择弹窗 ---------------- */
function pick(mode: 'dir' | 'file') {
  pickerMode.value = mode
  pickerOpen.value = true
}
function onPick(path: string) {
  pickerOpen.value = false
  if (pickerMode.value === 'dir') emit('pick-root', path)
  else void openByPath(path)
}

/* ---------------- 磁盘变更 → 刷新受影响的已加载目录 ---------------- */
/* 节点索引: 按路径定位已加载节点 */
function nodeAt(path: string): FsEntry | null {
  const exact: FsEntry[] = []
  const lower: FsEntry[] = []
  const walk = (n: FsEntry) => {
    if (n.path === path) exact.push(n)
    else if (n.path.toLowerCase() === path.toLowerCase()) lower.push(n)
    for (const c of n.children ?? []) walk(c)
  }
  if (root.value) walk(root.value)
  return exact[0] ?? lower[0] ?? null
}
const isLoadedDir = (path: string) => {
  const n = nodeAt(path)
  return !!n && n.kind === 'dir' && Array.isArray(n.children)
}
/** 向上找到最近的已加载目录(事件所在目录可能尚未加载) */
function nearestLoadedDir(path: string): FsEntry | null {
  let p: string | null = path
  const seen = new Set<string>()
  while (p && !seen.has(p)) {
    seen.add(p)
    const n = nodeAt(p)
    if (n && n.kind === 'dir' && Array.isArray(n.children)) return n
    p = parentOf(p)
  }
  return null
}
function parentOf(p: string): string | null {
  const norm = p.replace(/[\\/]+$/, '')
  const i = Math.max(norm.lastIndexOf('\\'), norm.lastIndexOf('/'))
  if (i < 0) return null
  const parent = norm.slice(0, i)
  if (!parent) return null
  return /^[A-Za-z]:$/.test(parent) ? `${parent}\\` : parent
}

/** 重新拉取某个已加载目录的子项, 并保留其下已展开子目录的既有内容 */
async function refreshDir(node: FsEntry) {
  const snapshot = new Map<string, FsEntry[]>()
  const collect = (n: FsEntry) => {
    if (Array.isArray(n.children)) snapshot.set(n.path, n.children)
    for (const c of n.children ?? []) collect(c)
  }
  collect(node)
  try {
    const r = await fetchList(node.path)
    node.children = r.entries.map((e) => {
      const prev = snapshot.get(e.path)
      if (e.kind === 'dir' && prev) e.children = prev
      return e
    })
  } catch {
    /* 目录可能已被删除 / 暂不可读, 交由其父级刷新移除 */
  }
}

let cursor = 0
const pendingDirs = new Set<string>()
let flushTimer: number | null = null

watch(
  () => props.events.length,
  () => {
    const list = props.events
    for (; cursor < list.length; cursor++) {
      const ev = list[cursor]
      if (ev.op === 'modified') {
        if (isLoadedDir(ev.dir)) pendingDirs.add(ev.dir)
        continue
      }
      const t = nearestLoadedDir(ev.dir)
      if (t) pendingDirs.add(t.path)
      if (ev.srcDir) {
        const t2 = nearestLoadedDir(ev.srcDir)
        if (t2) pendingDirs.add(t2.path)
      }
    }
    if (!pendingDirs.size) return
    if (flushTimer !== null) window.clearTimeout(flushTimer)
    flushTimer = window.setTimeout(() => {
      flushTimer = null
      const nodes = Array.from(pendingDirs).map(nodeAt).filter((n): n is FsEntry => !!n)
      pendingDirs.clear()
      void Promise.all(nodes.map(refreshDir))
    }, 250)
  },
)
</script>

<template>
  <div class="pane-sub-head">
    <span class="pane-title">目录文件</span>
    <span class="sub-right">
      <span v-if="selectedIds.length" class="o-mono o-text-3 sel-count">{{ selectedIds.length }} 已选</span>
      <button class="tree-pick" title="从磁盘选择目录" @click="pick('dir')">目录</button>
      <button class="tree-pick" title="从磁盘选择文件" @click="pick('file')">文件</button>
      <slot name="actions" />
    </span>
  </div>
  <div class="tree">
    <div v-if="loading" class="tree-empty">加载中…</div>
    <div v-else-if="!root" class="tree-empty">未选择目录 · 点右上角「目录」或「文件」</div>
    <template v-else>
      <div
        v-for="r in treeRows"
        :key="r.node.path"
        class="tree-row"
        :class="{
          'is-sel': r.node.kind === 'file' && selectedIds.includes(r.node.path),
          'is-open': r.node.path === activeId,
        }"
        :draggable="fileIdsOf(r.node).length > 0"
        :style="{ paddingLeft: 6 + r.depth * 14 + 'px' }"
        :title="r.node.path"
        @click="onRowClick(r)"
        @dragstart="onDragStart(r, $event)"
      >
        <span
          class="tree-caret"
          :class="{ 'is-open': !!expanded[r.node.path], 'is-leaf': r.node.kind === 'file' }"
          @click.stop="r.node.kind === 'dir' && toggleExpand(r.node)"
          >{{ r.node.kind === 'dir' ? '▸' : '' }}</span
        >
        <input
          class="tree-check"
          type="checkbox"
          :checked="isChecked(r.node)"
          :indeterminate.prop="isIndeterminate(r.node)"
          @click.stop="toggleSelect(r.node)"
        />
        <span class="tree-ico">{{ r.node.kind === 'dir' ? (expanded[r.node.path] ? '📂' : '📁') : '📄' }}</span>
        <span class="tree-name">{{ r.node.name }}</span>
        <span v-if="loadingPaths[r.node.path]" class="o-mono tree-size">…</span>
        <span v-else-if="r.node.kind === 'file'" class="o-mono tree-size">{{ fmtSize(r.node.size) }}</span>
      </div>
    </template>
  </div>
  <div v-if="error" class="tree-hint is-warn">{{ error }}</div>
  <div v-else-if="!root" class="tree-hint">选择目录后可浏览真实磁盘文件, 变动会实时刷新。</div>
  <div v-else-if="truncated" class="tree-hint">目录较大, 已按需展开(展开时自动加载)。</div>
  <div v-else class="tree-hint">点击文件在主体打开 · 勾选 / 拖拽加入提问上下文</div>

  <DiagnosisPathPicker :open="pickerOpen" :mode="pickerMode" @select="onPick" @close="pickerOpen = false" />
</template>

<style scoped>
.pane-sub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px 6px;
  border-top: 1px solid var(--border);
  flex: none;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  white-space: nowrap;
}
.sub-right {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
/* 左栏很窄时优先压缩计数文案, 保证标题与「目录 / 文件」按钮不折行 */
.sel-count {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-pick {
  flex: none;
  padding: 2px 7px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.5;
  cursor: pointer;
}
.tree-pick:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.tree {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 0 6px 6px;
}
.tree-empty {
  padding: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--text-4);
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding-right: 6px;
  border-radius: var(--radius);
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
}
.tree-row:hover {
  background: var(--surface);
}
.tree-row.is-sel {
  background: var(--primary-soft);
}
.tree-row.is-open {
  box-shadow: inset 2px 0 0 var(--primary);
}
.tree-caret {
  flex: none;
  width: 14px;
  text-align: center;
  color: var(--text-3);
  font-size: 10px;
  transition: transform 0.15s;
}
.tree-caret.is-open {
  transform: rotate(90deg);
}
.tree-caret.is-leaf {
  cursor: default;
}
.tree-check {
  flex: none;
  width: 13px;
  height: 13px;
  cursor: pointer;
  accent-color: var(--primary);
}
.tree-ico {
  flex: none;
  font-size: 12px;
}
.tree-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}
.tree-row.is-sel .tree-name,
.tree-row.is-open .tree-name {
  color: var(--primary);
  font-weight: 600;
}
.tree-size {
  flex: none;
  font-size: 10.5px;
  color: var(--text-4);
}
.tree-hint {
  flex: none;
  padding: 6px 14px 8px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-4);
}
.tree-hint.is-warn {
  color: var(--danger-deep);
}
</style>
