<script setup lang="ts">
/* 左·文件树: 两种数据源
   - 任务日志: 渲染传入的 nodes, 点击文件由视图读取 DB 内文件;
   - 本地目录(后端 /api/fs): 顶部「选择目录 / 选择文件」打开服务端路径弹窗, 逐层懒加载;
     磁盘变更(SSE)只定点刷新受影响的已加载目录, 保留展开态。
   通用交互: 点击打开 / 勾选多选(目录整选) / 拖拽加入提问上下文。 */
import { computed, ref, watch } from 'vue'
import FoldChevron from '@/components/diagnosis/FoldChevron.vue'
import Icon from '@/components/Icon.vue'
import DiagnosisPathPicker from '@/components/DiagnosisPathPicker.vue'
import { fetchFile, fetchList, fetchTree, rawUrl, type FsChange, type FsEntry } from '@/api/fs'
import { apiError } from '@/api/http'
import type { Attach, FilePayload } from '@/components/diagnosisFiles'
import type { FileNode } from '@/api/diagnosis'

const props = withDefaults(
  defineProps<{
    source?: 'log' | 'local'
    nodes?: FileNode[]
    loading?: boolean
    rootPath?: string
    rootSeq?: number
    events?: FsChange[]
    activeId?: string
  }>(),
  { nodes: () => [], loading: false, rootPath: '', rootSeq: 0, events: () => [], source: 'log' },
)
const emit = defineEmits<{
  open: [file: Attach, payload?: FilePayload]
  select: [files: Attach[]]
  'pick-root': [path: string]
}>()

const fmtSize = (bytes?: number) => {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

interface TreeRow {
  /** 任务日志: 节点 id; 本地目录: 绝对路径 */
  id: string
  name: string
  kind: 'dir' | 'file'
  size?: number
  path: string
  depth: number
  /** 该行(含已加载子级)下全部文件标识 */
  fileIds: string[]
}

const expanded = ref<Record<string, boolean>>({})
const selectedIds = ref<string[]>([])

/* ---------------- 任务日志: 展开态按节点 id ---------------- */
const logIdsOf = (n: FileNode): string[] =>
  n.kind === 'file' ? [n.id] : (n.children ?? []).flatMap(logIdsOf)

const logRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (nodes: FileNode[], depth: number, prefix: string) => {
    for (const n of nodes) {
      const path = prefix ? `${prefix}/${n.name}` : n.name
      out.push({ id: n.id, name: n.name, kind: n.kind, size: n.size, path, depth, fileIds: logIdsOf(n) })
      if (n.kind === 'dir' && expanded.value[n.id]) walk(n.children ?? [], depth + 1, path)
    }
  }
  walk(props.nodes, 0, '')
  return out
})

watch(
  () => props.nodes,
  (nodes) => {
    for (const n of nodes) {
      if (expanded.value[n.id] === undefined) expanded.value[n.id] = true
    }
  },
  { immediate: true },
)

/* ---------------- 本地目录: 加载 / 懒展开 / 变更定点刷新 ---------------- */
const root = ref<FsEntry | null>(null)
const localLoading = ref(false)
const loadingPaths = ref<Record<string, boolean>>({})
const localError = ref('')
const truncated = ref(false)
const pickerOpen = ref(false)
const pickerMode = ref<'dir' | 'file'>('dir')

let cursor = 0
const pendingDirs = new Set<string>()
let flushTimer: number | null = null

async function loadRoot(path: string) {
  root.value = null
  expanded.value = {}
  loadingPaths.value = {}
  truncated.value = false
  localError.value = ''
  /* 换目录(含重选同一目录)时释放上一次的勾选与待刷新队列, 避免残留选中态 */
  selectedIds.value = []
  pendingDirs.clear()
  cursor = 0
  if (flushTimer !== null) {
    window.clearTimeout(flushTimer)
    flushTimer = null
  }
  if (!path) return
  localLoading.value = true
  try {
    const r = await fetchTree(path)
    root.value = r.root
    truncated.value = r.truncated
    expanded.value = { [r.root.path]: true }
    /* 根目录本身超出一次性加载阈值时(无 children)补一次懒加载, 否则展开态下会空白 */
    if (!Array.isArray(r.root.children)) await loadChildren(r.root)
  } catch (e) {
    localError.value = apiError(e)
  } finally {
    localLoading.value = false
  }
}

async function loadChildren(n: FsEntry) {
  if (n.kind !== 'dir' || Array.isArray(n.children)) return
  loadingPaths.value[n.path] = true
  try {
    n.children = (await fetchList(n.path)).entries
  } catch (e) {
    localError.value = apiError(e)
  } finally {
    delete loadingPaths.value[n.path]
  }
}

const fsIdsOf = (n: FsEntry): string[] =>
  n.kind === 'file' ? [n.path] : (n.children ?? []).flatMap(fsIdsOf)

const localRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (n: FsEntry, depth: number) => {
    out.push({ id: n.path, name: n.name, kind: n.kind, size: n.size, path: n.path, depth, fileIds: fsIdsOf(n) })
    if (n.kind === 'dir' && expanded.value[n.path] && n.children) {
      for (const c of n.children) walk(c, depth + 1)
    }
  }
  if (root.value) walk(root.value, 0)
  return out
})

const rows = computed<TreeRow[]>(() => (props.source === 'local' ? localRows.value : logRows.value))

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
function parentOf(p: string): string | null {
  const norm = p.replace(/[\\/]+$/, '')
  const i = Math.max(norm.lastIndexOf('\\'), norm.lastIndexOf('/'))
  if (i < 0) return null
  const parent = norm.slice(0, i)
  if (!parent) return null
  return /^[A-Za-z]:$/.test(parent) ? `${parent}\\` : parent
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
/** 重新拉取某个已加载目录的子项, 并保留其下已展开子目录的既有内容 */
async function refreshDir(node: FsEntry) {
  const snapshot = new Map<string, FsEntry[]>()
  const collect = (n: FsEntry) => {
    if (Array.isArray(n.children)) snapshot.set(n.path, n.children)
    for (const c of n.children ?? []) collect(c)
  }
  collect(node)
  try {
    node.children = (await fetchList(node.path)).entries.map((e) => {
      const prev = snapshot.get(e.path)
      if (e.kind === 'dir' && prev) e.children = prev
      return e
    })
  } catch {
    /* 目录可能已被删除 / 暂不可读, 交由其父级刷新移除 */
  }
}

watch(
  () => props.events.length,
  () => {
    if (props.source !== 'local') return
    const list = props.events
    /* 事件数组已被截断(超出上限)时长游标会越界, 重置以重新对齐 */
    if (cursor > list.length) cursor = 0
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

/* rootPath 变化 或 视图侧重新选择目录(rootSeq 自增) 时重载 */
watch(
  [() => props.rootPath, () => props.rootSeq],
  ([p]) => {
    if (props.source === 'local') void loadRoot(p)
  },
)

/* 切换数据源时释放本侧瞬时状态, 避免两边的展开 / 勾选 / 待刷新残留 */
watch(
  () => props.source,
  () => {
    selectedIds.value = []
    root.value = null
    expanded.value = {}
    loadingPaths.value = {}
    localError.value = ''
    truncated.value = false
    pendingDirs.clear()
    cursor = 0
    if (flushTimer !== null) {
      window.clearTimeout(flushTimer)
      flushTimer = null
    }
  },
)

/* ---------------- 打开 ---------------- */
const allFiles = computed<Record<string, Attach>>(() => {
  const map: Record<string, Attach> = {}
  if (props.source === 'local') {
    const walk = (n: FsEntry) => {
      if (n.kind === 'file') map[n.path] = { id: n.path, name: n.name, path: n.path, size: fmtSize(n.size) }
      else for (const c of n.children ?? []) walk(c)
    }
    if (root.value) walk(root.value)
  } else {
    const walk = (nodes: FileNode[], prefix: string) => {
      for (const n of nodes) {
        const path = prefix ? `${prefix}/${n.name}` : n.name
        if (n.kind === 'file') map[n.id] = { id: n.id, name: n.name, path, size: fmtSize(n.size) }
        else walk(n.children ?? [], path)
      }
    }
    walk(props.nodes, '')
  }
  return map
})

async function openLocal(f: Attach) {
  try {
    const r = await fetchFile(f.path)
    const content = r.kind === 'image' ? rawUrl(f.path) : (r.content ?? '')
    emit('open', { ...f, size: fmtSize(r.size) || f.size }, { kind: r.kind, content, lang: r.lang })
  } catch (e) {
    localError.value = apiError(e)
  }
}

async function openByPath(path: string) {
  const name = path.split(/[\\/]/).pop() ?? path
  await openLocal({ id: path, name, path })
}

async function toggleExpand(row: TreeRow) {
  if (row.kind !== 'dir') return
  if (expanded.value[row.id]) {
    expanded.value[row.id] = false
    return
  }
  expanded.value[row.id] = true
  if (props.source === 'local') {
    const n = nodeAt(row.path)
    if (n) await loadChildren(n)
  }
}

async function onRowClick(row: TreeRow) {
  if (row.kind === 'dir') {
    await toggleExpand(row)
    return
  }
  const f = allFiles.value[row.id]
  if (!f) return
  if (props.source === 'local') await openLocal(f)
  else emit('open', f)
}

/* ---------------- 多选(已加载范围内): 选中目录等价于选中其下全部文件 ---------------- */
const isChecked = (row: TreeRow) =>
  row.fileIds.length > 0 && row.fileIds.every((id) => selectedIds.value.includes(id))
const isIndeterminate = (row: TreeRow) => {
  const c = row.fileIds.filter((id) => selectedIds.value.includes(id)).length
  return c > 0 && c < row.fileIds.length
}
const toggleSelect = (row: TreeRow) => {
  const ids = row.fileIds
  if (!ids.length) return
  const all = ids.every((id) => selectedIds.value.includes(id))
  selectedIds.value = all
    ? selectedIds.value.filter((id) => !ids.includes(id))
    : Array.from(new Set([...selectedIds.value, ...ids]))
}

watch(
  selectedIds,
  (ids) => emit('select', ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f)),
  { deep: true },
)

const onDragStart = (row: TreeRow, e: DragEvent) => {
  const ids =
    row.kind === 'file' && selectedIds.value.includes(row.id) && selectedIds.value.length > 1
      ? selectedIds.value
      : row.fileIds
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

defineExpose({
  clear: () => (selectedIds.value = []),
  select: (id: string) => {
    if (!selectedIds.value.includes(id)) selectedIds.value = [...selectedIds.value, id]
  },
  deselect: (id: string) => {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  },
})
</script>

<template>
  <div v-if="source === 'local'" class="tree-sub">
    <div class="tree-sub-row">
      <button class="tree-pick" title="从磁盘选择目录" @click="pick('dir')">选择目录</button>
      <button class="tree-pick" title="从磁盘选择文件" @click="pick('file')">选择文件</button>
    </div>
    <div class="tree-sub-path o-mono" :class="{ 'o-text-3': !rootPath }" :title="rootPath">
      {{ rootPath || '未选择目录' }}
    </div>
  </div>

  <div class="tree">
    <div v-if="source === 'log' && loading" class="tree-empty">正在加载…</div>
    <div v-else-if="source === 'local' && localLoading && !root" class="tree-empty">正在加载…</div>
    <div v-else-if="source === 'local' && !root" class="tree-empty">
      未选择目录 · 点上方「选择目录」浏览磁盘文件，磁盘变动会实时刷新。
    </div>
    <slot v-else-if="!rows.length" name="empty">
      <div class="tree-empty">
        还没有可分析的日志。<br />先去「创建解析任务」上传日志，或直接把日志粘贴到右侧提问。
      </div>
    </slot>
    <template v-else>
      <div
        v-for="r in rows"
        :key="r.id"
        class="tree-row"
        :class="{
          'is-sel': r.kind === 'file' && selectedIds.includes(r.id),
          'is-open': r.id === activeId,
        }"
        :draggable="r.fileIds.length > 0"
        :style="{ paddingLeft: 6 + r.depth * 14 + 'px' }"
        :title="r.path"
        @click="onRowClick(r)"
        @dragstart="onDragStart(r, $event)"
      >
        <span
          class="tree-caret"
          :class="{ 'is-leaf': r.kind === 'file' }"
          @click.stop="r.kind === 'dir' && toggleExpand(r)"
        >
          <FoldChevron v-if="r.kind === 'dir'" :open="!!expanded[r.id]" />
        </span>
        <input
          class="tree-check"
          type="checkbox"
          :aria-label="'选择 ' + r.name"
          :checked="isChecked(r)"
          :indeterminate.prop="isIndeterminate(r)"
          @click.stop="toggleSelect(r)"
        />
        <span class="tree-ico">
          <Icon
            :name="r.kind === 'dir' ? (expanded[r.id] ? 'folder-open' : 'folder') : 'file'"
            :size="14"
          />
        </span>
        <span class="tree-name">{{ r.name }}</span>
        <span v-if="loadingPaths[r.path]" class="o-mono tree-size">…</span>
        <span v-else-if="r.size" class="o-mono tree-size">{{ fmtSize(r.size) }}</span>
      </div>
    </template>
  </div>

  <div v-if="source === 'local' && localError" class="tree-hint is-warn">{{ localError }}</div>
  <div v-else-if="source === 'local' && truncated" class="tree-hint">
    目录较大，已按需展开(展开时自动加载)。
  </div>
  <div v-else-if="source === 'local' && root" class="tree-hint">
    点击文件预览 · 勾选 / 拖拽加入提问上下文
  </div>

  <DiagnosisPathPicker :open="pickerOpen" :mode="pickerMode" @select="onPick" @close="pickerOpen = false" />
</template>

<style scoped>
.tree-sub {
  flex: none;
  padding: 8px 8px 6px;
  border-top: 1px solid var(--border);
}
.tree-sub-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tree-pick {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  font: inherit;
  font-size: 11.5px;
  line-height: 1.6;
  cursor: pointer;
}
.tree-pick:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.tree-sub-path {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 6px;
}
.tree-empty {
  padding: 18px 12px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--text-3);
  text-align: center;
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding-right: 6px;
  border-radius: var(--radius);
  font-size: 13px;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
}
.tree-caret.is-leaf {
  cursor: default;
}
.tree-check {
  flex: none;
  width: 14px;
  height: 14px;
  cursor: pointer;
  accent-color: var(--primary);
}
.tree-ico {
  flex: none;
  display: inline-flex;
  align-items: center;
  color: var(--text-4);
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
  font-size: 11px;
  color: var(--text-4);
}
.tree-hint {
  flex: none;
  padding: 6px 12px 8px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-4);
}
.tree-hint.is-warn {
  color: var(--danger-deep);
}
</style>
