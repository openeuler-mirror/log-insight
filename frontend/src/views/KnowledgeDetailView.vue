<script setup lang="ts">
/* 单个知识库详情: 文档列表 + 正文阅读 + 上传弹窗 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { apiError } from '@/api/http'
import {
  commitTemplateBatch, createFolder, deleteDoc, deleteFolder, getDoc, getKb, listDocs,
  listFolders, renameFolder, uploadFile, uploadTemplateBatch,
  type KbDocument, type KbFolder, type KbIngestBatch, type KnowledgeBase,
} from '@/api/kb'
import Icon from '@/components/Icon.vue'
import ErrorAlert from '@/components/ErrorAlert.vue'
import MarkdownView from '@/components/MarkdownView.vue'

const ACCEPT_SET = new Set(['.txt', '.md', '.pdf', '.docx', '.xlsx', '.xls', '.csv', '.html', '.htm'])
const ACCEPT = [...ACCEPT_SET].join(',')

const route = useRoute()
const rr = useRouter()
const teamsStore = useTeamsStore()
const rbac = useRbacStore()

const teamId = computed(() => teamsStore.activeTeamId)
const kbId = computed(() => Number(route.params.kbId))
const canManage = computed(() =>
  !!teamId.value && (rbac.isPlatformAdmin || teamsStore.can(teamId.value, 'kb:manage')))
const canView = computed(() =>
  !!teamId.value && (rbac.isPlatformAdmin || teamsStore.can(teamId.value, 'kb:view')))

const kb = ref<KnowledgeBase | null>(null)
const docs = ref<KbDocument[]>([])
const loading = ref(false)
const err = ref('')

const activeDocId = ref<number | null>(null)
const docContent = ref('')
const docLoading = ref(false)

/* ---- 上传弹窗 ---- */
interface PendingFile {
  file: File
  /** 原始目录相对路径（拖入目录时保留层级），空=根 */
  relPath: string
}

const showUpload = ref(false)
const uploadMode = ref<'raw' | 'template'>('raw')
const pendingFiles = ref<PendingFile[]>([])
const uploading = ref(false)
const uploadErr = ref('')
const uploadProgress = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)
/** 点击拖拽区时可选择「文件」或「文件夹」 */
const showPickMenu = ref(false)
const dragOver = ref(false)
let dragDepth = 0

const previewBatch = ref<KbIngestBatch | null>(null)
const selectedItemIds = ref<number[]>([])
const committing = ref(false)
const previewErr = ref('')

async function reload() {
  if (!teamId.value || !kbId.value || !canView.value) {
    kb.value = null
    docs.value = []
    folders.value = []
    return
  }
  loading.value = true
  err.value = ''
  try {
    kb.value = await getKb(teamId.value, kbId.value)
    const [docList, folderList] = await Promise.all([
      listDocs(teamId.value, kbId.value),
      listFolders(teamId.value, kbId.value),
    ])
    docs.value = docList
    folders.value = folderList
    if (activeDocId.value && !docs.value.some((d) => d.id === activeDocId.value)) {
      activeDocId.value = null
      docContent.value = ''
    }
    if (!activeDocId.value && docs.value.length) await openDoc(docs.value[0].id)
  } catch (e) {
    err.value = apiError(e)
    kb.value = null
  } finally {
    loading.value = false
  }
}

async function openDoc(id: number) {
  if (!teamId.value || !kbId.value) return
  activeDocId.value = id
  docLoading.value = true
  try {
    const d = await getDoc(teamId.value, kbId.value, id)
    docContent.value = d.content || '（无正文）'
  } catch (e) {
    err.value = apiError(e)
    docContent.value = ''
  } finally {
    docLoading.value = false
  }
}

function openUpload() {
  showUpload.value = true
  uploadMode.value = 'raw'
  pendingFiles.value = []
  uploadErr.value = ''
  uploadProgress.value = ''
  showPickMenu.value = false
}

function closeUpload() {
  if (uploading.value) return
  showUpload.value = false
  showPickMenu.value = false
}

function extOf(name: string) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

const pendingKey = (p: PendingFile) => `${p.relPath || p.file.name}:${p.file.size}:${p.file.lastModified}`

/** 上传时的目标相对路径: 落在当前浏览目录下 */
const uploadRel = (p: PendingFile) => (currentDir.value ? `${currentDir.value}/` : '') + (p.relPath || p.file.name)

function addPending(items: PendingFile[]) {
  if (!items.length) return
  const next = [...pendingFiles.value]
  const seen = new Set(next.map(pendingKey))
  let rejected = 0
  for (const it of items) {
    if (!ACCEPT_SET.has(extOf(it.file.name))) {
      rejected += 1
      continue
    }
    const key = pendingKey(it)
    if (seen.has(key)) continue
    seen.add(key)
    next.push(it)
  }
  pendingFiles.value = next
  uploadErr.value = rejected ? `已忽略 ${rejected} 个不支持格式的文件` : ''
}

/** 待上传文件按相对路径聚合成目录树行: 文件夹在前, 文件在后 */
type PendingRow =
  | { key: string; type: 'dir'; depth: number; name: string; count: number; size: number }
  | { key: string; type: 'file'; depth: number; name: string; size: number; index: number }

type TreeNode = {
  dirs: Map<string, TreeNode>
  files: { name: string; size: number; index: number }[]
}

const treeNode = (): TreeNode => ({ dirs: new Map(), files: [] })

const treeStat = (n: TreeNode) => {
  const subs = [...n.dirs.values()].map(treeStat)
  return {
    count: n.files.length + subs.reduce((s, x) => s + x.count, 0),
    size: n.files.reduce((s, f) => s + f.size, 0) + subs.reduce((s, x) => s + x.size, 0),
  }
}

const pendingRows = computed<PendingRow[]>(() => {
  const root = treeNode()
  pendingFiles.value.forEach((p, index) => {
    const parts = (p.relPath || p.file.name).split('/').filter(Boolean)
    if (!parts.length) return
    let node = root
    for (const seg of parts.slice(0, -1)) {
      let child = node.dirs.get(seg)
      if (!child) {
        child = treeNode()
        node.dirs.set(seg, child)
      }
      node = child
    }
    node.files.push({ name: parts[parts.length - 1], size: p.file.size, index })
  })
  const rows: PendingRow[] = []
  const walk = (node: TreeNode, depth: number, prefix: string) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const child = node.dirs.get(name) as TreeNode
      const path = `${prefix}/${name}`
      rows.push({ key: `d:${path}`, type: 'dir', depth, name, ...treeStat(child) })
      walk(child, depth + 1, path)
    }
    for (const f of [...node.files].sort((a, b) => a.name.localeCompare(b.name))) {
      rows.push({ key: `f:${f.index}`, type: 'file', depth, name: f.name, size: f.size, index: f.index })
    }
  }
  walk(root, 0, '')
  return rows
})

const pendingTotalSize = computed(() => pendingFiles.value.reduce((s, p) => s + p.file.size, 0))

const fmtSize = (n: number) =>
  n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`

/** 点击选择文件（普通文件多选） */
const addFiles = (list: FileList | File[] | null) => list && addPending(Array.from(list).map((file) => ({ file, relPath: '' })))

/** 递归展开拖入目录: 目录下所有文件按相对路径加入, 普通文件只加入自身 */
function readEntry(entry: FileSystemEntry, prefix: string, out: PendingFile[]): Promise<void> {
  if (entry.isFile) {
    return new Promise((resolve) => {
      ;(entry as FileSystemFileEntry).file((file) => {
        out.push({ file, relPath: prefix ? `${prefix}/${file.name}` : file.name })
        resolve()
      }, () => resolve())
    })
  }
  if (!entry.isDirectory) return Promise.resolve()
  const dir = prefix ? `${prefix}/${entry.name}` : entry.name
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  return new Promise((resolve) => {
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve()
          return
        }
        void Promise.all(batch.map((child) => readEntry(child, dir, out))).then(readBatch)
      }, () => resolve())
    }
    readBatch()
  })
}

function removeFile(idx: number) {
  pendingFiles.value = pendingFiles.value.filter((_, i) => i !== idx)
}

function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  addFiles(input.files)
  input.value = ''
}

/** 点击选择文件夹: 浏览器会给每个文件带 webkitRelativePath */
function onPickDir(ev: Event) {
  const input = ev.target as HTMLInputElement
  addPending(Array.from(input.files ?? []).map((file) => ({
    file,
    relPath: (file as File & { webkitRelativePath?: string }).webkitRelativePath || '',
  })))
  input.value = ''
}

const togglePickMenu = () => (showPickMenu.value = !showPickMenu.value)
const pickFiles = () => { showPickMenu.value = false; fileInput.value?.click() }
const pickFolder = () => { showPickMenu.value = false; dirInput.value?.click() }
const closePickMenu = () => (showPickMenu.value = false)

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  dragDepth += 1
  dragOver.value = true
}
function onDragLeave(e: DragEvent) {
  e.preventDefault()
  dragDepth -= 1
  if (dragDepth <= 0) {
    dragDepth = 0
    dragOver.value = false
  }
}
function onDragOver(e: DragEvent) {
  e.preventDefault()
}
async function onDrop(e: DragEvent) {
  e.preventDefault()
  dragDepth = 0
  dragOver.value = false
  const dt = e.dataTransfer
  if (!dt) return
  // 统一走 entries: 拖入目录时递归展开, 拖入文件时只加入文件本身
  const entries = Array.from(dt.items ?? [])
    .map((it) => it.webkitGetAsEntry?.() ?? null)
    .filter((x): x is FileSystemEntry => !!x)
  if (entries.length) {
    const out: PendingFile[] = []
    for (const entry of entries) await readEntry(entry, '', out)
    addPending(out)
    return
  }
  addFiles(dt.files)
}

async function onConfirmUpload() {
  if (!teamId.value || !kbId.value || !pendingFiles.value.length) return
  uploading.value = true
  uploadErr.value = ''
  try {
    if (uploadMode.value === 'template') {
      uploadProgress.value = `模板转换中（${pendingFiles.value.length} 个文件）…`
      const batch = await uploadTemplateBatch(
        teamId.value,
        kbId.value,
        pendingFiles.value.map((p) => p.file),
        pendingFiles.value.map((p) => uploadRel(p)),
      )
      showUpload.value = false
      pendingFiles.value = []
      previewBatch.value = batch
      selectedItemIds.value = batch.items
        .filter((i) => i.convertStatus === 'ok')
        .map((i) => i.id)
      previewErr.value = ''
      return
    }
    let lastId: number | null = null
    const total = pendingFiles.value.length
    for (let i = 0; i < total; i++) {
      const p = pendingFiles.value[i]
      const rel = uploadRel(p)
      uploadProgress.value = `上传中 ${i + 1}/${total}：${rel || p.file.name}`
      const d = await uploadFile(teamId.value, kbId.value, p.file, 'raw', rel)
      lastId = d.id
    }
    showUpload.value = false
    pendingFiles.value = []
    await reload()
    if (lastId) await openDoc(lastId)
  } catch (e) {
    uploadErr.value = apiError(e)
  } finally {
    uploading.value = false
    uploadProgress.value = ''
  }
}

function toggleItem(id: number) {
  const set = new Set(selectedItemIds.value)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  selectedItemIds.value = [...set]
}

function selectAllOk() {
  if (!previewBatch.value) return
  selectedItemIds.value = previewBatch.value.items
    .filter((i) => i.convertStatus === 'ok')
    .map((i) => i.id)
}

async function onCommitPreview() {
  if (!teamId.value || !previewBatch.value || !selectedItemIds.value.length) return
  committing.value = true
  previewErr.value = ''
  try {
    const res = await commitTemplateBatch(teamId.value, previewBatch.value.id, selectedItemIds.value)
    previewBatch.value = null
    await reload()
    const last = res.documents?.[res.documents.length - 1]
    if (last) await openDoc(last.id)
  } catch (e) {
    previewErr.value = apiError(e)
  } finally {
    committing.value = false
  }
}

async function onDeleteDoc(id: number) {
  if (!teamId.value || !kbId.value || !confirm('确认软删除该文档？')) return
  try {
    await deleteDoc(teamId.value, kbId.value, id)
    if (activeDocId.value === id) {
      activeDocId.value = null
      docContent.value = ''
    }
    await reload()
  } catch (e) {
    err.value = apiError(e)
  }
}

/* ---- 目录浏览: 文件管理器风格, 单层展示当前目录 ---- */
const folders = ref<KbFolder[]>([])
const currentDir = ref('')

interface DirFolder {
  path: string
  name: string
  count: number
  updatedAt: string
  virtual: boolean
}

const parentOf = (rel: string) => (rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '')

function fmtDate(s: string): string {
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN', { hour12: false })
}

/** 当前目录下的子目录: 显式目录 + 显式路径派生的父段 + 文档 relPath 派生的虚拟目录合并 */
const dirFolders = computed<DirFolder[]>(() => {
  const prefix = currentDir.value ? `${currentDir.value}/` : ''
  const map = new Map<string, DirFolder>()
  const ensure = (path: string, name: string, virtual: boolean, updatedAt = '') => {
    if (!map.has(path)) map.set(path, { path, name, count: 0, updatedAt, virtual })
  }
  for (const f of folders.value) {
    if (!f.path.startsWith(prefix)) continue
    const rest = f.path.slice(prefix.length)
    if (!rest) continue
    const first = rest.split('/')[0]
    /* 深层显式目录也注册其一级父段, 避免派生文档被删后目录变孤儿不可达 */
    if (rest.includes('/')) ensure(prefix + first, first, true)
    else ensure(f.path, first, false, f.updatedAt || '')
  }
  /* 单趟遍历文档: 派生虚拟目录 + 向全部祖先目录累加计数与最新时间 */
  for (const d of docs.value) {
    const rel = d.relPath || ''
    if (!rel.startsWith(prefix)) continue
    const segs = rel.slice(prefix.length).split('/')
    if (segs.length < 2) continue
    ensure(prefix + segs[0], segs[0], true)
    for (let i = 1; i < segs.length; i++) {
      const fo = map.get(prefix + segs.slice(0, i).join('/'))
      if (!fo) continue
      fo.count += 1
      const u = d.updatedAt || ''
      if (u > fo.updatedAt) fo.updatedAt = u
    }
  }
  return [...map.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh'))
})

/** 当前目录下的文件 */
const dirFiles = computed<KbDocument[]>(() =>
  docs.value
    .filter((d) => parentOf(d.relPath || '') === currentDir.value)
    .sort((a, b) => (a.relPath || a.title).localeCompare(b.relPath || b.title, 'zh')),
)

const crumbs = computed(() => (currentDir.value ? currentDir.value.split('/') : []))

const goDir = (path: string) => { currentDir.value = path; clearSel() }

/* ---- 勾选与批量删除 ---- */
const selected = ref<Set<string>>(new Set())

const isSel = (key: string) => selected.value.has(key)
function toggleSel(key: string) {
  const s = new Set(selected.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  selected.value = s
}
const clearSel = () => { selected.value = new Set() }
const selCount = computed(() => selected.value.size)

/* ---- 目录增删改 ---- */
const folderModal = ref<{
  mode: 'create' | 'rename'
  base: string
  path: string
  name: string
  err: string
} | null>(null)
const deleteModal = ref<{ dirs: DirFolder[]; files: KbDocument[] } | null>(null)
const folderBusy = ref(false)

const openCreateFolder = (base: string) => { folderModal.value = { mode: 'create', base, path: '', name: '', err: '' } }
const openRenameFolder = (f: DirFolder) => {
  folderModal.value = { mode: 'rename', base: parentOf(f.path), path: f.path, name: f.name, err: '' }
}
const openDeleteFolder = (f: DirFolder) => (deleteModal.value = { dirs: [f], files: [] })
const openDeleteFiles = (files: KbDocument[]) => (deleteModal.value = { dirs: [], files })
function openBulkDelete() {
  const dirs = dirFolders.value.filter((f) => selected.value.has(`d:${f.path}`))
  const files = dirFiles.value.filter((d) => selected.value.has(`f:${d.id}`))
  if (!dirs.length && !files.length) return
  deleteModal.value = { dirs, files }
}
/** 删除涉及的文档总数（目录内文档 + 直接选中的文件） */
const deleteDocTotal = computed(() => {
  const m = deleteModal.value
  return m ? m.dirs.reduce((s, f) => s + f.count, 0) + m.files.length : 0
})

async function submitFolderModal() {
  const m = folderModal.value
  if (!m || !teamId.value || !kbId.value || folderBusy.value) return
  const name = m.name.trim()
  if (!name || name.includes('/') || name === '.' || name === '..') {
    m.err = '目录名不能为空且不能包含 /'
    return
  }
  folderBusy.value = true
  try {
    if (m.mode === 'create') {
      await createFolder(teamId.value, kbId.value, m.base ? `${m.base}/${name}` : name)
    } else {
      const newPath = m.base ? `${m.base}/${name}` : name
      await renameFolder(teamId.value, kbId.value, m.path, name)
      if (currentDir.value === m.path) currentDir.value = newPath
      else if (currentDir.value.startsWith(`${m.path}/`)) {
        currentDir.value = newPath + currentDir.value.slice(m.path.length)
      }
    }
    folderModal.value = null
    /* 重命名会改变目录 key, 勾选集合按旧路径残留会导致计数虚高且删除失效 */
    clearSel()
    await reload()
  } catch (e) {
    m.err = apiError(e)
  } finally {
    folderBusy.value = false
  }
}

async function confirmDeleteModal() {
  const m = deleteModal.value
  if (!m || !teamId.value || !kbId.value || folderBusy.value) return
  folderBusy.value = true
  try {
    for (const f of m.dirs) await deleteFolder(teamId.value, kbId.value, f.path)
    for (const d of m.files) await deleteDoc(teamId.value, kbId.value, d.id)
    deleteModal.value = null
    clearSel()
    await reload()
  } catch (e) {
    err.value = apiError(e)
  } finally {
    folderBusy.value = false
  }
}

watch([teamId, kbId], () => {
  activeDocId.value = null
  docContent.value = ''
  currentDir.value = ''
  clearSel()
  reload()
})

onMounted(() => {
  reload()
  document.addEventListener('click', closePickMenu)
})
onUnmounted(() => document.removeEventListener('click', closePickMenu))
</script>

<template>
  <div class="page">
    <div class="top">
      <button class="back" @click="rr.push({ name: 'knowledge' })">
        <Icon name="arrow-left" :size="14" /> 知识库
      </button>
      <div class="top-main" v-if="kb">
        <h1>{{ kb.name }}</h1>
        <p class="sub">
          <span v-if="!kb.vectorEnabled" class="badge">仅关键词</span>
          <span v-else class="badge ok">向量检索</span>
          {{ kb.description || kb.vectorHint || '' }}
        </p>
      </div>
      <div class="actions" v-if="kb && canManage">
        <button class="btn primary" @click="openUpload">
          <Icon name="plus" :size="14" /> 上传
        </button>
      </div>
    </div>

    <ErrorAlert v-if="err" :text="err" style="margin-bottom: 12px" @close="err = ''" />
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="!kb" class="empty">知识库不存在或无权访问</div>

    <div v-else class="layout">
      <aside class="docs">
        <div class="docs-head">
          <span>文档 ({{ docs.length }})</span>
          <span v-if="canManage" class="head-actions">
            <button class="icon-btn" title="新建目录" @click="openCreateFolder(currentDir)">
              <Icon name="plus" :size="14" />
            </button>
            <button class="icon-btn" title="刷新" @click="reload()">
              <Icon name="refresh" :size="14" />
            </button>
          </span>
        </div>
        <div class="crumbs">
          <button class="crumb" :class="{ on: !currentDir }" @click="goDir('')">根目录</button>
          <template v-for="(c, i) in crumbs" :key="`${c}-${i}`">
            <Icon name="chevron-right" :size="12" />
            <button
              class="crumb"
              :class="{ on: i === crumbs.length - 1 }"
              @click="goDir(crumbs.slice(0, i + 1).join('/'))"
            >
              {{ c }}
            </button>
          </template>
        </div>
        <div v-if="selCount" class="sel-bar">
          <span>已选 {{ selCount }} 项</span>
          <button v-if="canManage" class="btn sm danger" @click="openBulkDelete">删除</button>
          <button class="btn sm" @click="clearSel">取消</button>
        </div>
        <div v-if="!dirFolders.length && !dirFiles.length" class="muted pad">
          此目录为空，可上传文件 / 文件夹或新建目录
        </div>
        <div
          v-for="f in dirFolders"
          :key="`d:${f.path}`"
          class="doc-item dir"
          :class="{ checked: isSel(`d:${f.path}`) }"
          :title="f.path"
          @click="goDir(f.path)"
        >
          <input
            type="checkbox"
            class="row-check"
            :checked="isSel(`d:${f.path}`)"
            @click.stop
            @change="toggleSel(`d:${f.path}`)"
          />
          <Icon name="folder" :size="15" />
          <span class="doc-title">{{ f.name }}</span>
          <span class="row-meta">{{ f.count ? `${f.count} 篇` : fmtDate(f.updatedAt) }}</span>
          <span v-if="canManage" class="row-actions">
            <button class="icon-btn" title="新建子目录" @click.stop="openCreateFolder(f.path)">
              <Icon name="plus" :size="12" />
            </button>
            <button class="icon-btn" title="重命名" @click.stop="openRenameFolder(f)">
              <Icon name="pencil" :size="12" />
            </button>
            <button class="icon-btn danger" title="删除目录" @click.stop="openDeleteFolder(f)">
              <Icon name="trash" :size="12" />
            </button>
          </span>
        </div>
        <div
          v-for="d in dirFiles"
          :key="`f:${d.id}`"
          class="doc-item"
          :class="{ active: d.id === activeDocId, checked: isSel(`f:${d.id}`) }"
          :title="d.relPath || d.title"
          @click="openDoc(d.id)"
        >
          <input
            type="checkbox"
            class="row-check"
            :checked="isSel(`f:${d.id}`)"
            @click.stop
            @change="toggleSel(`f:${d.id}`)"
          />
          <Icon name="file" :size="14" />
          <div class="doc-main">
            <div class="doc-title">{{ d.title }}</div>
            <div class="doc-meta">{{ d.sourceFormat }} · {{ d.status }}</div>
          </div>
          <span class="row-meta">{{ fmtDate(d.updatedAt) }}</span>
          <span v-if="canManage" class="row-actions">
            <button class="icon-btn danger" title="删除" @click.stop="openDeleteFiles([d])">
              <Icon name="trash" :size="12" />
            </button>
          </span>
        </div>
      </aside>

      <section class="reader">
        <div v-if="!activeDocId" class="empty muted">选择左侧文档查看正文</div>
        <template v-else>
          <div class="reader-head">
            <h2>{{ docs.find((d) => d.id === activeDocId)?.title }}</h2>
            <button v-if="canManage" class="link danger" @click="onDeleteDoc(activeDocId!)">删除</button>
          </div>
          <pre v-if="docLoading" class="body muted">加载中…</pre>
          <MarkdownView v-else class="body" :text="docContent" />
        </template>
      </section>
    </div>

    <!-- 上传弹窗 -->
    <div v-if="showUpload" class="mask" @click.self="closeUpload">
      <div class="dialog">
        <h3>上传文档</h3>
        <div class="mode">
          <label class="radio">
            <input v-model="uploadMode" type="radio" value="raw" />
            <span><b>原样导入</b> — 解析切块后直接入库，保留原文结构</span>
          </label>
          <label class="radio">
            <input v-model="uploadMode" type="radio" value="template" />
            <span><b>模板化导入</b> — 按案例模板转换后预览勾选入库</span>
          </label>
        </div>
        <div v-if="uploadMode === 'template'" class="hint">
          将使用库内案例模板（空则系统默认）经团队默认 Chat 转换，成功项可预览勾选后入库。
        </div>
        <div v-if="currentDir" class="hint">将导入到当前浏览目录：{{ currentDir }}</div>

        <div
          class="drop"
          :class="{ 'is-drag': dragOver }"
          @click.stop="togglePickMenu"
          @dragenter="onDragEnter"
          @dragleave="onDragLeave"
          @dragover="onDragOver"
          @drop="onDrop"
        >
          <div class="drop-ico"><Icon name="folder" :size="28" /></div>
          <div class="drop-t">点击选择文件 / 文件夹，或直接拖拽到此处</div>
          <div class="drop-s">文件夹会递归加入其中所有支持的文件 · 单文件 ≤ 20MB · 支持 {{ ACCEPT.replace(/,/g, ' / ').replace(/\./g, '') }}</div>
          <div v-if="showPickMenu" class="pick-menu" @click.stop>
            <button type="button" @click="pickFiles">选择文件</button>
            <button type="button" @click="pickFolder">选择文件夹</button>
          </div>
        </div>
        <input
          ref="fileInput"
          type="file"
          hidden
          multiple
          :accept="ACCEPT"
          @change="onPick"
        />
        <input
          ref="dirInput"
          type="file"
          hidden
          multiple
          v-bind="{ webkitdirectory: '', directory: '' }"
          @change="onPickDir"
        />

        <div v-if="pendingRows.length" class="pending-summary">
          已选 {{ pendingFiles.length }} 个文件 · {{ fmtSize(pendingTotalSize) }}
        </div>
        <ul v-if="pendingRows.length" class="file-tree">
          <li
            v-for="row in pendingRows"
            :key="row.key"
            class="tree-row"
            :class="row.type"
            :style="{ paddingLeft: `${10 + row.depth * 16}px` }"
          >
            <span class="fn" :title="row.name">{{ row.name }}</span>
            <span v-if="row.type === 'dir'" class="fs">{{ row.count }} 个文件 · {{ fmtSize(row.size) }}</span>
            <template v-else>
              <span class="fs">{{ fmtSize(row.size) }}</span>
              <button class="link" :disabled="uploading" @click="removeFile(row.index)">移除</button>
            </template>
          </li>
        </ul>

        <ErrorAlert v-if="uploadErr" :text="uploadErr" style="margin-bottom: 12px" @close="uploadErr = ''" />
        <div v-if="uploadProgress" class="muted sm">{{ uploadProgress }}</div>

        <div class="dialog-actions">
          <button class="btn" :disabled="uploading" @click="closeUpload">取消</button>
          <button
            class="btn primary"
            :disabled="uploading || !pendingFiles.length"
            @click="onConfirmUpload"
          >
            {{ uploading
              ? (uploadMode === 'template' ? '转换中…' : '导入中…')
              : (uploadMode === 'template'
                ? `开始转换${pendingFiles.length ? ` (${pendingFiles.length})` : ''}`
                : `开始导入${pendingFiles.length ? ` (${pendingFiles.length})` : ''}`) }}
          </button>
        </div>
      </div>
    </div>

    <!-- 模板预览勾选 -->
    <div v-if="previewBatch" class="mask" @click.self="!committing && (previewBatch = null)">
      <div class="dialog preview">
        <h3>模板转换预览</h3>
        <p class="muted sm">仅转换成功的项可勾选导入；失败项请检查团队默认 Chat 或原文。</p>
        <div class="preview-actions">
          <button class="btn" @click="selectAllOk">全选成功项</button>
          <span class="muted sm">已选 {{ selectedItemIds.length }}</span>
        </div>
        <div class="preview-list">
          <article v-for="it in previewBatch.items" :key="it.id" class="preview-item">
            <label class="row-check">
              <input
                type="checkbox"
                :disabled="it.convertStatus !== 'ok'"
                :checked="selectedItemIds.includes(it.id)"
                @change="toggleItem(it.id)"
              />
              <b>{{ it.relPath || it.filename }}</b>
              <span class="tag" :class="{ ok: it.convertStatus === 'ok' }">
                {{ it.convertStatus === 'ok' ? '可导入' : '失败' }}
              </span>
            </label>
            <p v-if="it.errorMessage" class="err sm">{{ it.errorMessage }}</p>
            <MarkdownView v-if="it.previewMarkdown" class="preview-md" :text="it.previewMarkdown" />
          </article>
        </div>
        <ErrorAlert v-if="previewErr" :text="previewErr" style="margin-bottom: 12px" @close="previewErr = ''" />
        <div class="dialog-actions">
          <button class="btn" :disabled="committing" @click="previewBatch = null">取消</button>
          <button
            class="btn primary"
            :disabled="committing || !selectedItemIds.length"
            @click="onCommitPreview"
          >
            {{ committing ? '导入中…' : `导入所选 (${selectedItemIds.length})` }}
          </button>
        </div>
      </div>
    </div>

    <!-- 新建 / 重命名目录 -->
    <div v-if="folderModal" class="mask" @click.self="folderModal = null">
      <div class="dialog sm">
        <h3>{{ folderModal.mode === 'create' ? '新建目录' : '重命名目录' }}</h3>
        <p class="muted sm">
          {{ folderModal.mode === 'create'
            ? `父目录：${folderModal.base || '根目录'}`
            : `原路径：${folderModal.path}` }}
        </p>
        <input
          v-model="folderModal.name"
          class="input"
          placeholder="目录名称"
          @keyup.enter="submitFolderModal"
        />
        <ErrorAlert v-if="folderModal.err" :text="folderModal.err" style="margin-bottom: 12px" @close="folderModal.err = ''" />
        <div class="dialog-actions">
          <button class="btn" @click="folderModal = null">取消</button>
          <button class="btn primary" :disabled="folderBusy" @click="submitFolderModal">确定</button>
        </div>
      </div>
    </div>

    <!-- 删除确认（目录 / 文件 / 批量） -->
    <div v-if="deleteModal" class="mask" @click.self="deleteModal = null">
      <div class="dialog sm">
        <h3>删除确认</h3>
        <p class="muted sm">
          将删除
          <template v-if="deleteModal.dirs.length">{{ deleteModal.dirs.length }} 个目录</template>
          <template v-if="deleteModal.dirs.length && deleteModal.files.length">、</template>
          <template v-if="deleteModal.files.length">{{ deleteModal.files.length }} 个文档</template>
          ，共涉及 {{ deleteDocTotal }} 篇文档（含目录内文档），操作不可恢复。
        </p>
        <div class="dialog-actions">
          <button class="btn" @click="deleteModal = null">取消</button>
          <button class="btn danger" :disabled="folderBusy" @click="confirmDeleteModal">删除</button>
        </div>
      </div>
    </div>

  </div>
</template>

<style scoped>
.page { width: 100%; }
.top { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
.back {
  border: 0; background: transparent; color: var(--text-2); cursor: pointer;
  display: inline-flex; align-items: center; gap: 4px; padding: 6px 0; font-size: 13px;
}
.back:hover { color: var(--primary); }
.top-main { flex: 1; min-width: 0; }
.top-main h1 { margin: 0 0 4px; font-size: 20px; color: var(--text-1); }
.sub { margin: 0; font-size: 13px; color: var(--text-2); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.actions { flex-shrink: 0; }
.err { color: var(--danger-deep); background: var(--danger-soft); padding: 8px 12px; border-radius: var(--radius); margin-bottom: 12px; }
.err.sm { margin: 0; font-size: 13px; }
.empty { padding: 40px; text-align: center; color: var(--text-2); }
.muted { color: var(--text-2); }
.muted.sm { font-size: 12px; }
.pad { padding: 12px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 99px; background: var(--warning-soft); color: var(--warning-deep); }
.badge.ok { background: var(--success-soft); color: var(--success-deep); }

.layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) 1fr;
  gap: 14px;
  min-height: calc(100vh - 180px);
}
.docs, .reader {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.docs { overflow: auto; max-height: calc(100vh - 180px); }
.docs-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 14px; font-weight: 600; border-bottom: 1px solid var(--border);
  color: var(--text-1); position: sticky; top: 0; background: var(--surface); z-index: 1;
}
.head-actions { display: inline-flex; gap: 2px; }
.icon-btn {
  border: 0; background: transparent; color: var(--text-3); cursor: pointer;
  padding: 4px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center;
}
.icon-btn:hover { background: var(--surface-3); color: var(--text-1); }
.icon-btn.danger:hover { color: var(--danger-deep); }
.crumbs {
  display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--text-4);
}
.crumb {
  border: 0; background: transparent; cursor: pointer; font-size: 12px;
  color: var(--text-2); padding: 2px 6px; border-radius: 6px;
  max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.crumb:hover { background: var(--surface-3); }
.crumb.on { color: var(--primary); font-weight: 600; }
.doc-item {
  display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
  border: 0; border-bottom: 1px solid var(--border);
  background: transparent; padding: 9px 14px; cursor: pointer;
}
.doc-item:hover { background: var(--surface-3); }
.doc-item.active { background: var(--primary-soft); }
.doc-item.checked { background: var(--primary-soft); }
.doc-item.dir { color: var(--text-2); }
/* 文件夹行标题撑满剩余宽度: 截断长名并把右侧按钮栏推到最右 */
.doc-item.dir .doc-title { flex: 1; min-width: 0; }
.row-check { flex-shrink: 0; width: 14px; height: 14px; accent-color: var(--primary); cursor: pointer; }
.sel-bar {
  display: flex; align-items: center; gap: 8px; padding: 8px 14px;
  background: var(--primary-soft); border-bottom: 1px solid var(--border);
  font-size: 12px; color: var(--text-2);
}
.btn.sm { padding: 4px 10px; font-size: 12px; }
.doc-main { flex: 1; min-width: 0; }
.doc-title {
  font-size: 13px; color: var(--text-1); font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.doc-meta { font-size: 12px; color: var(--text-3); margin-top: 2px; }
.row-meta { flex-shrink: 0; font-size: 11px; color: var(--text-4); margin-left: auto; }
.row-actions { display: none; flex-shrink: 0; gap: 2px; align-items: center; margin-left: auto; }
.doc-item:hover .row-actions { display: inline-flex; }
.doc-item:hover .row-meta { display: none; }
.dialog.sm { width: 400px; max-width: calc(100vw - 48px); }
.input {
  width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: var(--radius);
  padding: 8px 10px; font-size: 13px; color: var(--text-1); background: var(--surface);
  outline: none; margin: 8px 0;
}
.input:focus { border-color: var(--primary); }
.btn.danger { background: var(--danger-deep); color: #fff; border-color: var(--danger-deep); }
.reader {
  padding: 14px 16px; display: flex; flex-direction: column;
  min-height: calc(100vh - 180px); max-height: calc(100vh - 180px); overflow: hidden;
}
.reader-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; }
.reader-head h2 { margin: 0; font-size: 16px; color: var(--text-1); }
.body {
  flex: 1; min-height: 0; margin: 0; padding: 12px; background: var(--surface-3); border-radius: var(--radius);
  font-size: 13px; word-break: break-word;
  color: var(--text-1); overflow: auto;
}
.btn { border: 1px solid var(--border); background: var(--surface); border-radius: var(--radius); padding: 6px 12px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; }
.btn.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.link { border: 0; background: none; cursor: pointer; font-size: 13px; color: var(--text-2); }
.link.danger { color: var(--danger-deep); }
.link:disabled { opacity: .5; cursor: not-allowed; }

.mask { position: fixed; inset: 0; background: rgba(15,23,42,.35); display: flex; align-items: center; justify-content: center; z-index: 40; }
.dialog {
  width: min(520px, 94vw); background: var(--surface); border-radius: var(--radius-lg);
  padding: 20px; display: flex; flex-direction: column; gap: 12px; max-height: 90vh; overflow: auto;
}
.dialog h3 { margin: 0; }
.mode { display: flex; flex-direction: column; gap: 8px; }
.radio { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; color: var(--text-2); cursor: pointer; }
.radio b { color: var(--text-1); }
.hint { font-size: 12px; color: var(--text-2); background: var(--surface-3); padding: 8px 10px; border-radius: var(--radius); }
.drop {
  position: relative;
  border: 1.5px dashed var(--border-strong); border-radius: var(--radius-lg);
  padding: 28px 16px; text-align: center; cursor: pointer; transition: all .15s; background: var(--surface);
}
.drop:hover, .drop.is-drag { border-color: var(--primary); background: var(--primary-soft); }
.pick-menu {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  display: flex; flex-direction: column; gap: 6px; padding: 8px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg, 0 8px 24px rgba(0, 0, 0, .14)); z-index: 2;
}
.pick-menu button {
  border: 0; background: transparent; padding: 8px 18px; border-radius: var(--radius);
  font-size: 13px; color: var(--text-1); cursor: pointer; white-space: nowrap;
}
.pick-menu button:hover { background: var(--primary-soft); color: var(--primary); }
.drop-ico { color: var(--text-4); margin-bottom: 8px; display: flex; justify-content: center; }
.drop-t { font-size: 14px; color: var(--text-1); }
.drop-s { font-size: 12px; color: var(--text-3); margin-top: 6px; }
.pending-summary { font-size: 12px; color: var(--text-3); }
.file-tree {
  list-style: none; margin: 0; padding: 4px 0; max-height: 240px; overflow: auto;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-3);
}
.tree-row { display: flex; align-items: center; gap: 10px; padding: 5px 10px; font-size: 13px; color: var(--text-2); }
.tree-row.dir { color: var(--text-1); font-weight: 600; }
.tree-row.file:hover { background: var(--primary-soft); }
.fn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-1); }
.fs { color: var(--text-3); font-size: 12px; flex-shrink: 0; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.dialog.preview { width: min(720px, 96vw); }
.preview-actions { display: flex; align-items: center; gap: 12px; }
.preview-list { display: flex; flex-direction: column; gap: 10px; max-height: 55vh; overflow: auto; }
.preview-item { border: 1px solid var(--border); border-radius: var(--radius); padding: 10px; background: var(--surface-3); }
.row-check { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
.row-check .tag { font-size: 11px; padding: 1px 6px; border-radius: 99px; background: var(--danger-soft); color: var(--danger-deep); }
.row-check .tag.ok { background: var(--success-soft); color: var(--success-deep); }
.preview-md {
  margin: 8px 0 0; padding: 8px; background: var(--surface); border-radius: var(--radius);
  font-size: 12px;
  max-height: 180px; overflow: auto; color: var(--text-1);
}

@media (max-width: 800px) {
  .layout { grid-template-columns: 1fr; min-height: auto; }
  .docs, .reader { max-height: none; min-height: 280px; }
}
</style>
