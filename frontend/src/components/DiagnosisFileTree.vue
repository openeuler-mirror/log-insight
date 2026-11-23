<script setup lang="ts">
/* 左·文件树: 两种数据源
   - 团队日志: 渲染传入的 nodes, 点击文件由视图读取 DB 内文件;
   - 本地目录(本机导入): 把客户端电脑上的文件/文件夹直接拖入列表, 上传到会话工作区后按 /api/fs 浏览;
     拖拽按内容自动分辨目录/文件并保留层级;
     磁盘变更(SSE)只定点刷新受影响的已加载目录, 保留展开态。
   通用交互: 点击打开 / 勾选多选(目录整选) / 拖拽加入提问上下文。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FoldChevron from '@/components/diagnosis/FoldChevron.vue'
import Icon from '@/components/Icon.vue'
import { deleteEntry, fetchFile, fetchList, fetchTree, rawUrl, type FsChange, type FsEntry } from '@/api/fs'
import { apiError } from '@/api/http'
import { uploadClientDir, type FileNode } from '@/api/diagnosis'
import type { Attach, FilePayload } from '@/components/diagnosis/diagnosisFiles'
import {
  joinWorkspace,
  rememberLocalHandle,
  forgetLocalHandles,
  type LocalDirHandle,
  type LocalFileHandle,
} from '@/components/diagnosis/localHandles'

const props = withDefaults(
  defineProps<{
    source?: 'log' | 'local'
    nodes?: FileNode[]
    loading?: boolean
    rootPath?: string
    rootSeq?: number
    events?: FsChange[]
    activeId?: string
    /* 当前会话 id: 本机导入需要写入会话工作区 */
    sessionId?: string
    /* 外部(会话记录)恢复的勾选, 变化时同步到树 */
    selectedIds?: string[]
  }>(),
  { nodes: () => [], loading: false, rootPath: '', rootSeq: 0, events: () => [],
    source: 'log', sessionId: '', selectedIds: () => [] },
)
const emit = defineEmits<{
  open: [file: Attach, payload?: FilePayload]
  select: [files: Attach[], ids: string[]]
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
  /** 团队日志: 节点 id; 本地目录: 绝对路径 */
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
/* 空目录(子树内没有任何文件)的勾选标记: 无附件可加入上下文, 仅作结构标记 */
const emptyDirTicked = ref<Record<string, boolean>>({})

/* ---------------- 团队日志: 展开态按节点 id ---------------- */
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
/* 本机导入: 文件/文件夹直接拖入树区域, 自动分辨目录与文件 */
const dropActive = ref(false)
const dragging = ref(false)
let dropHintTimer: number | null = null
const importing = ref(false)
const importText = ref('')

onBeforeUnmount(() => {
  if (dropHintTimer !== null) window.clearTimeout(dropHintTimer)
  if (noticeTimer !== null) window.clearTimeout(noticeTimer)
})

let cursor = 0
/* 首次导入后需要自动展开的导入目录(待根目录加载完成后消费) */
let pendingExpand = ''
const pendingDirs = new Set<string>()
let flushTimer: number | null = null

async function loadRoot(path: string) {
  root.value = null
  expanded.value = {}
  emptyDirTicked.value = {}
  loadingPaths.value = {}
  truncated.value = false
  localError.value = ''
  /* 换目录(含重选同一目录): 勾选以外部为准(会话记录), 并清掉待刷新队列 */
  selectedIds.value = [...(props.selectedIds ?? [])]
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
    /* 刚导入的目录直接展开并加载子项, 让用户马上看到上传了哪些文件 */
    if (pendingExpand) {
      const p = pendingExpand
      pendingExpand = ''
      const n = nodeAt(p)
      if (n) {
        expanded.value[n.path] = true
        await loadChildren(n)
      }
    }
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
  const r = root.value
  if (!r) return out
  /* 工作区根只是磁盘上的实现细节, 不展示: 各次导入的目录/文件直接平级铺开, 且都可删除 */
  if (r.kind === 'dir') {
    for (const c of r.children ?? []) walk(c, 0)
  } else {
    walk(r, 0)
  }
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

/* 追加导入后刷新根的子项: 新导入目录出现在顶层, 保留已展开子目录的既有内容 */
async function refreshRootChildren() {
  const r = root.value
  if (!r) return
  if (!Array.isArray(r.children)) {
    await loadChildren(r)
    return
  }
  try {
    r.children = (await fetchList(r.path)).entries.map((e) => {
      const prev = r.children?.find((c) => c.path === e.path)
      if (e.kind === 'dir' && prev?.children) e.children = prev.children
      return e
    })
  } catch {
    /* 根目录暂不可读, 忽略 */
  }
}

/* 删除工作区里的导入项(不允许删根); 删掉后同步取消其下勾选 */
async function removeEntry(r: TreeRow) {
  if (props.source !== 'local' || (root.value && r.path === root.value.path)) return
  try {
    await deleteEntry(r.path)
  } catch (e) {
    localError.value = apiError(e)
    return
  }
  const removed = new Set(r.fileIds)
  forgetLocalHandles(r.path)
  if (selectedIds.value.some((id) => removed.has(id))) {
    selectedIds.value = selectedIds.value.filter((id) => !removed.has(id))
  }
  /* 清掉被删目录及其子孙的空目录勾选标记, 避免同路径复活时残留勾选态 */
  const dead = (k: string) => k === r.path || k.startsWith(r.path + '\\') || k.startsWith(r.path + '/')
  const tickKeys = Object.keys(emptyDirTicked.value)
  if (tickKeys.some(dead)) {
    emptyDirTicked.value = Object.fromEntries(tickKeys.filter((k) => !dead(k)).map((k) => [k, emptyDirTicked.value[k]]))
  }
  const parent = nodeAt(parentOf(r.path) ?? '')
  if (parent) await refreshDir(parent)
  else await refreshRootChildren()
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

/* 挂载时已有目录则直接加载 (折叠只隐藏不卸载, 正常仅在首次挂载时命中) */
onMounted(() => {
  if (props.source === 'local' && props.rootPath) {
    void loadRoot(props.rootPath)
  }
})

/* 外部勾选(会话记录恢复)变化时同步到树, 与当前一致则跳过以免回写抖动 */
watch(
  () => props.selectedIds,
  (ids) => {
    const next = ids ?? []
    if (next.length === selectedIds.value.length
      && next.every((id) => selectedIds.value.includes(id))) return
    selectedIds.value = [...next]
  },
)

/* 切换数据源时释放本侧瞬时状态, 避免两边的展开 / 勾选 / 待刷新残留 */
watch(
  () => props.source,
  () => {
    selectedIds.value = [...(props.selectedIds ?? [])]
    root.value = null
    expanded.value = {}
    emptyDirTicked.value = {}
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

/* ---------------- 多选(选中目录等价于选中其下全部文件, 含子目录) ---------------- */
const isEmptyDir = (row: TreeRow) => row.kind === 'dir' && row.fileIds.length === 0
const isChecked = (row: TreeRow) =>
  isEmptyDir(row)
    ? !!emptyDirTicked.value[row.id]
    : row.fileIds.length > 0 && row.fileIds.every((id) => selectedIds.value.includes(id))
const isIndeterminate = (row: TreeRow) => {
  const c = row.fileIds.filter((id) => selectedIds.value.includes(id)).length
  return c > 0 && c < row.fileIds.length
}
/* 目录因超出加载阈值/深度而未带 children 时, 先递归拉取整棵子树 */
async function loadSubtree(n: FsEntry) {
  if (n.kind !== 'dir') return
  if (!Array.isArray(n.children)) await loadChildren(n)
  for (const c of n.children ?? []) {
    if (c.kind === 'dir' && !Array.isArray(c.children)) await loadSubtree(c)
  }
}
/* 父目录勾选/取消时, 其下空目录的标记同步跟随 */
function tickDescendantDirs(path: string, on: boolean) {
  const n = nodeAt(path)
  if (!n) return
  const walk = (d: FsEntry) => {
    for (const c of d.children ?? []) {
      if (c.kind !== 'dir') continue
      emptyDirTicked.value[c.path] = on
      walk(c)
    }
  }
  walk(n)
}
async function toggleSelect(row: TreeRow) {
  let ids = row.fileIds
  if (row.kind === 'dir') {
    const n = nodeAt(row.path)
    if (n) {
      if (props.source === 'local') await loadSubtree(n)
      ids = fsIdsOf(n)
    }
  }
  if (!ids.length) {
    /* 空目录: 没有文件可加入上下文, 勾选框只切换结构标记(含其下空目录) */
    if (row.kind !== 'dir') return
    const on = !emptyDirTicked.value[row.id]
    emptyDirTicked.value = { ...emptyDirTicked.value, [row.id]: on }
    tickDescendantDirs(row.path, on)
    return
  }
  const all = ids.every((id) => selectedIds.value.includes(id))
  selectedIds.value = all
    ? selectedIds.value.filter((id) => !ids.includes(id))
    : Array.from(new Set([...selectedIds.value, ...ids]))
  /* 非空目录: 其下空目录的勾选态跟随目录本身 */
  if (row.kind === 'dir') tickDescendantDirs(row.path, !all)
}

/* 同时给出 id 列表: 已加载节点外的勾选(深层未展开)由视图侧用会话记录补全 */
watch(
  selectedIds,
  (ids) => emit(
    'select',
    ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f),
    [...ids],
  ),
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

/* ---------------- 本机文件导入 ---------------- */
const CLIENT_DIR_MAX_FILES = 2000
const CLIENT_DIR_MAX_BYTES = 2 * 1024 * 1024 * 1024
const UPLOAD_BATCH_FILES = 50
const UPLOAD_BATCH_BYTES = 64 * 1024 * 1024

/* 导入结果提示(追加数量 / 去重跳过), 数秒后自动消失 */
const notice = ref('')
let noticeTimer: number | null = null
function showNotice(text: string) {
  notice.value = text
  if (noticeTimer !== null) window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => {
    noticeTimer = null
    notice.value = ''
  }, 6000)
}

/* 本机导入: 把文件/文件夹直接拖入列表, 由拖拽自动分辨目录与文件并保留层级 */
function clearDropHint() {
  if (dropHintTimer !== null) {
    window.clearTimeout(dropHintTimer)
    dropHintTimer = null
  }
  dropActive.value = false
  dragging.value = false
}

/* 本机文件/目录导入: 分批上传到会话工作区; rootName 为空表示文件直接落在工作区根上;
   handles 为拖拽时拿到的本机文件句柄, 上传成功后按工作区绝对路径登记, 供编辑保存写回原文件 */
async function uploadLocal(
  items: Array<{ file: File; path: string }>,
  rootName: string,
  handles: Array<[string, LocalFileHandle]> = [],
) {
  if (!items.length) {
    showNotice('目录为空，没有可导入的文件')
    return
  }
  if (!props.sessionId) {
    localError.value = '请先新建或选择会话，再导入本机目录或文件'
    return
  }
  if (items.length > CLIENT_DIR_MAX_FILES) {
    localError.value = `文件过多（${items.length} 个），最多导入 ${CLIENT_DIR_MAX_FILES} 个`
    return
  }
  const totalBytes = items.reduce((n, it) => n + it.file.size, 0)
  if (totalBytes > CLIENT_DIR_MAX_BYTES) {
    localError.value = `体积过大（${fmtSize(totalBytes)}），最多 ${fmtSize(CLIENT_DIR_MAX_BYTES)}`
    return
  }

  importing.value = true
  localError.value = ''
  let done = 0
  let skipped = 0
  let workspace = ''
  let importedRoot = ''
  try {
    let batch: Array<{ file: File; path: string }> = []
    let batchBytes = 0
    const flush = async () => {
      if (!batch.length) return
      importText.value = `正在导入 ${done}/${items.length}`
      const res = await uploadClientDir(props.sessionId, batch, { rootName })
      workspace = res.workspace || res.root
      importedRoot = res.root
      skipped += res.skipped ?? 0
      done += batch.length
      batch = []
      batchBytes = 0
      importText.value = `正在导入 ${done}/${items.length}`
    }
    for (const it of items) {
      if (batch.length >= UPLOAD_BATCH_FILES || batchBytes + it.file.size > UPLOAD_BATCH_BYTES) {
        await flush()
      }
      batch.push(it)
      batchBytes += it.file.size
    }
    await flush()
    if (!workspace) return
    /* 上传成功后才登记句柄: 路径与树一致, 编辑保存时可直接写回本机原文件;
       同时记下导入时的文件指纹, 之后轮询发现其他进程改动即可增量同步 */
    const meta = new Map(items.map((it) => [it.path, it.file]))
    for (const [rel, handle] of handles) {
      const file = meta.get(rel)
      rememberLocalHandle(
        joinWorkspace(workspace, rel),
        handle,
        file ? { lastModified: file.lastModified, size: file.size } : undefined,
      )
    }
    /* 追加语义: 树根已是同一工作区时只刷新列表, 否则切到工作区根 */
    if (props.source === 'local' && props.rootPath === workspace) {
      await refreshRootChildren()
      /* 新导入目录默认展开并加载子项, 让用户直接看到刚追加的文件 */
      if (importedRoot && importedRoot !== workspace) {
        expanded.value[importedRoot] = true
        const node = nodeAt(importedRoot)
        if (node) await loadChildren(node)
      }
    } else {
      pendingExpand = importedRoot === workspace ? '' : importedRoot
      emit('pick-root', workspace)
    }
    const added = done - skipped
    showNotice(skipped > 0
      ? `已追加 ${added} 个文件，${skipped} 个重复已跳过`
      : `已追加 ${added} 个文件`)
  } catch (err) {
    localError.value = err instanceof Error ? err.message : apiError(err)
  } finally {
    importing.value = false
    importText.value = ''
  }
}

/* ---------------- 拖拽导入: 按拖入内容自动分辨目录 / 文件 ---------------- */

const readFileEntry = (entry: FileSystemFileEntry) =>
  new Promise<File>((resolve, reject) => entry.file(resolve, reject))

/* readEntries 单次最多返回 100 条, 需循环读到空数组 */
const readDirEntries = (dir: FileSystemDirectoryEntry) =>
  new Promise<FileSystemEntry[]>((resolve, reject) => {
    const reader = dir.createReader()
    const all: FileSystemEntry[] = []
    const next = () =>
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(all)
          return
        }
        all.push(...batch)
        next()
      }, reject)
    next()
  })

async function collectEntry(
  entry: FileSystemEntry,
  rel: string,
  out: Array<{ file: File; path: string }>,
) {
  if (entry.isFile) {
    out.push({ file: await readFileEntry(entry as FileSystemFileEntry), path: rel })
    return
  }
  if (!entry.isDirectory) return
  for (const child of await readDirEntries(entry as FileSystemDirectoryEntry)) {
    await collectEntry(child, `${rel}/${child.name}`, out)
  }
}

const hasDroppedFiles = (e: DragEvent) =>
  Array.from(e.dataTransfer?.types ?? []).includes('Files')

/* File System Access API: 拖入时同时申请可写句柄(Chromium 支持), 用于把编辑内容写回本机原文件 */
type HandleCapableItem = DataTransferItem & {
  getAsFileSystemHandle?: () => Promise<LocalFileHandle | LocalDirHandle | null>
}

async function collectHandleDir(
  dir: LocalDirHandle,
  prefix: string,
  out: Array<[string, LocalFileHandle]>,
) {
  for await (const child of dir.values()) {
    const rel = `${prefix}/${child.name}`
    if (child.kind === 'file') out.push([rel, child])
    else await collectHandleDir(child, rel, out)
  }
}

/* 句柄的相对路径与 collectEntry 保持一致(单个目录以目录名作根);
   浏览器不支持或任一拖入项拿不到句柄时返回空, 此时文件只能只读浏览 */
async function collectHandles(items: DataTransferItem[], singleDir: boolean) {
  const out: Array<[string, LocalFileHandle]> = []
  const pending = items.map((it) => (it as HandleCapableItem).getAsFileSystemHandle?.call(it))
  if (!items.length || pending.some((p) => !p)) return out
  try {
    const resolved = await Promise.all(pending as Array<Promise<LocalFileHandle | LocalDirHandle | null>>)
    if (resolved.some((h) => !h)) return out
    for (const h of resolved as Array<LocalFileHandle | LocalDirHandle>) {
      if (h.kind === 'file') {
        if (!singleDir) out.push([h.name, h])
      } else {
        await collectHandleDir(h, h.name, out)
      }
    }
  } catch {
    return []
  }
  return out
}

/* 资源管理器选择: 优先走 File System Access API, 选中的文件同样登记句柄(导入后仍可编辑写回) */
type PickerWindow = Window & {
  showOpenFilePicker?: (opts?: { multiple?: boolean }) => Promise<LocalFileHandle[]>
  showDirectoryPicker?: () => Promise<LocalDirHandle>
}

async function collectPickedDir(
  dir: LocalDirHandle,
  prefix: string,
  items: Array<{ file: File; path: string }>,
  handles: Array<[string, LocalFileHandle]>,
) {
  for await (const child of dir.values()) {
    const rel = `${prefix}/${child.name}`
    if (child.kind === 'file') {
      items.push({ file: await child.getFile(), path: rel })
      handles.push([rel, child])
    } else {
      await collectPickedDir(child, rel, items, handles)
    }
  }
}

/* 同一个文档同一时刻只允许一个系统选择器: 连点菜单项或导入中再次触发,
   浏览器会以 InvalidStateError("File picker already active") 拒绝, 这里先拦截;
   用时间窗而非布尔锁, 即使浏览器把上一次选择器卡住, 几秒后也能自动恢复 */
let pickBusyUntil = 0

/* 兼容回退: 非安全上下文 / 不支持 File System Access API 的浏览器 / 浏览器判定
   「已有选择器打开」时, 改用经典 input[type=file] 导入(拿不到句柄, 本次不可写回本机文件) */
const fallbackInput = ref<HTMLInputElement | null>(null)
let fallbackKind: 'file' | 'directory' = 'file'

function openFallback(kind: 'file' | 'directory') {
  const el = fallbackInput.value
  if (!el) {
    localError.value = '当前浏览器不支持资源管理器选择，请直接把文件 / 文件夹拖进来'
    return
  }
  fallbackKind = kind
  el.value = ''
  if (kind === 'directory') el.setAttribute('webkitdirectory', '')
  else el.removeAttribute('webkitdirectory')
  el.click()
  showNotice('已使用兼容方式导入：本次无法写回本机文件')
}

async function onFallbackPicked() {
  const files = Array.from(fallbackInput.value?.files ?? [])
  if (!files.length) return
  if (fallbackKind === 'directory') {
    const first = files[0].webkitRelativePath || ''
    const rootName = first.split('/')[0] || ''
    const items = files.map((f) => ({
      file: f,
      path: (f.webkitRelativePath || `${rootName}/${f.name}`).replace(/^\/+/, ''),
    }))
    await uploadLocal(items, rootName)
  } else {
    await uploadLocal(files.map((f) => ({ file: f, path: f.name })), '')
  }
}

async function pickLocal(kind: 'file' | 'directory') {
  const w = window as PickerWindow
  const picker = kind === 'file' ? w.showOpenFilePicker : w.showDirectoryPicker
  if (!picker) {
    openFallback(kind)
    return
  }
  if (Date.now() < pickBusyUntil) return
  if (importing.value) {
    showNotice('正在导入，请稍候')
    return
  }
  pickBusyUntil = Date.now() + 5000
  try {
    if (kind === 'file') {
      const picked = await w.showOpenFilePicker!({ multiple: true })
      const items: Array<{ file: File; path: string }> = []
      const handles: Array<[string, LocalFileHandle]> = []
      for (const h of picked) {
        items.push({ file: await h.getFile(), path: h.name })
        handles.push([h.name, h])
      }
      await uploadLocal(items, '', handles)
    } else {
      const dir = await w.showDirectoryPicker!()
      const items: Array<{ file: File; path: string }> = []
      const handles: Array<[string, LocalFileHandle]> = []
      await collectPickedDir(dir, dir.name, items, handles)
      await uploadLocal(items, dir.name, handles)
    }
  } catch (err) {
    const name = (err as DOMException)?.name
    /* 用户取消: 静默; 浏览器拒绝(已有选择器/非安全上下文): 退回兼容导入, 不表现为「按钮没反应」 */
    if (name === 'AbortError') return
    if (name === 'InvalidStateError' || name === 'SecurityError' || name === 'NotAllowedError') {
      openFallback(kind)
      return
    }
    localError.value = err instanceof Error ? err.message : String(err)
  } finally {
    pickBusyUntil = 0
  }
}

function onTreeDragOver(e: DragEvent) {
  if (!hasDroppedFiles(e)) return
  e.preventDefault()
  if (props.source !== 'local' || importing.value) return
  /* 真实拖拽期间保持提示常亮, 不再走计时自动隐藏 */
  if (dropHintTimer !== null) {
    window.clearTimeout(dropHintTimer)
    dropHintTimer = null
  }
  dragging.value = true
  dropActive.value = true
}

function onTreeDragLeave(e: DragEvent) {
  if (e.currentTarget !== e.target) return
  dropActive.value = false
  dragging.value = false
}

async function onTreeDrop(e: DragEvent) {
  /* dragover 已 preventDefault, drop 必须同样阻止, 否则浏览器会直接打开/导航到拖入的文件 */
  if (!hasDroppedFiles(e)) return
  e.preventDefault()
  clearDropHint()
  if (props.source !== 'local' || importing.value) return
  /* 拖放的来源窗口(资源管理器)由系统管理, 网页无权关闭; 只把浏览器窗口拉回前台 */
  window.focus()
  const dt = e.dataTransfer
  if (!dt) return

  const entries = Array.from(dt.items ?? [])
    .map((it) => (typeof it.webkitGetAsEntry === 'function' ? it.webkitGetAsEntry() : null))
    .filter((en): en is FileSystemEntry => !!en)
  /* 句柄必须在 drop 的同步调用栈里发起, 不能等遍历完成后再取 */
  const singleDir = entries.length === 1 && entries[0].isDirectory
  const handleTask = collectHandles(Array.from(dt.items ?? []), singleDir)

  try {
    /* 单个目录: 用目录名作根, 保留原有层级 */
    if (singleDir) {
      const rootName = entries[0].name
      const items: Array<{ file: File; path: string }> = []
      await collectEntry(entries[0], rootName, items)
      await uploadLocal(items, rootName, await handleTask)
      return
    }
    /* 多文件 / 多目录: 直接落在工作区根上, 不新建批次目录 */
    const items: Array<{ file: File; path: string }> = []
    if (entries.length) {
      for (const en of entries) {
        if (en.isFile) {
          const file = await readFileEntry(en as FileSystemFileEntry)
          items.push({ file, path: file.name })
        } else {
          await collectEntry(en, en.name, items)
        }
      }
    } else {
      for (const file of Array.from(dt.files ?? [])) {
        items.push({ file, path: file.name })
      }
    }
    if (items.length) await uploadLocal(items, '', await handleTask)
  } catch (err) {
    localError.value = err instanceof Error ? err.message : String(err)
  }
}

defineExpose({
  pickLocal,
  clear: () => {
    selectedIds.value = []
    emptyDirTicked.value = {}
  },
  select: (id: string) => {
    if (!selectedIds.value.includes(id)) selectedIds.value = [...selectedIds.value, id]
  },
  deselect: (id: string) => {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  },
})
</script>

<template>
  <div
    class="tree"
    :class="{ 'is-drop': dropActive }"
    @dragover="onTreeDragOver"
    @dragleave="onTreeDragLeave"
    @drop="onTreeDrop"
  >
    <input
      ref="fallbackInput"
      class="tree-file-input"
      type="file"
      multiple
      @change="onFallbackPicked"
    />
    <div v-if="importing" class="tree-drop">{{ importText || '正在导入…' }}</div>
    <div v-else-if="dropActive" class="tree-drop">
      {{ dragging ? '松开导入本机文件 / 目录' : '也可以把文件 / 文件夹直接拖到这里' }}
    </div>
    <div v-if="source === 'log' && loading" class="tree-empty">正在加载…</div>
    <div v-else-if="source === 'local' && localLoading && !root" class="tree-empty">正在加载…</div>
    <div v-else-if="source === 'local' && (!root || !rows.length)" class="tree-drag-hint">
      <p class="tree-drag-hint-t">还没有导入文件</p>
      <p class="tree-drag-hint-s">把本机日志文件 / 文件夹拖到这里，或点上方「打开资源管理器」选择，导入本次诊断的工作区。</p>
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
        <button
          v-if="source === 'local'"
          class="tree-del"
          title="从会话工作区删除（不影响客户端原文件）"
          @click.stop="removeEntry(r)"
        >×</button>
      </div>
    </template>
  </div>

  <div v-if="source === 'local' && notice" class="tree-hint is-ok">{{ notice }}</div>
  <div v-if="source === 'local' && localError" class="tree-hint is-warn">{{ localError }}</div>
  <div v-else-if="source === 'local' && truncated" class="tree-hint">
    目录较大，已按需展开(展开时自动加载)。
  </div>
  <div v-else-if="source === 'local' && root" class="tree-hint">
    本机导入 · 可继续拖入追加（自动去重），悬停行可删除 · 点击预览 / 勾选加入上下文
  </div>
</template>

<style scoped>
.tree {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 6px;
}
/* 拖入文件/目录时的落点提示(仅本地目录模式) */
.tree.is-drop {
  background: var(--surface-3);
}
/* 兼容导入用的隐藏文件输入 */
.tree-file-input {
  display: none;
}
.tree-drop {
  position: absolute;
  inset: 6px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border: 1px dashed var(--primary);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--primary);
  font-size: 12px;
  text-align: center;
  pointer-events: none;
}
.tree-empty {
  padding: 18px 12px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--text-3);
  text-align: center;
}
/* 本机导入的空态: 与团队日志一致的提示框, 同时也是拖放区 */
.tree-drag-hint {
  margin: 8px 4px;
  padding: 16px 12px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  font-size: 12.5px;
  line-height: 1.8;
  text-align: center;
  color: var(--text-3);
}
.tree-drag-hint-t {
  margin: 0 0 2px;
  color: var(--text-2);
  font-weight: 600;
}
.tree-drag-hint-s {
  margin: 0;
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
.tree-hint.is-ok {
  color: var(--success, var(--text-2));
}
/* 删除: 悬停行时出现, 不挤压文件名 */
.tree-row .tree-del {
  flex: none;
  width: 18px;
  height: 18px;
  margin-left: 2px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-4);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s;
}
.tree-row:hover .tree-del {
  opacity: 1;
}
.tree-row .tree-del:hover {
  background: var(--danger-bg, rgba(200, 40, 40, 0.12));
  color: var(--danger-deep);
}
</style>
