<script setup lang="ts">
/* 单个知识库详情: 文档列表 + 正文阅读 + 上传弹窗 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { apiError } from '@/api/http'
import {
  commitTemplateBatch, deleteDoc, getDoc, getKb, listDocs, uploadFile, uploadTemplateBatch,
  type KbDocument, type KbIngestBatch, type KnowledgeBase,
} from '@/api/kb'
import Icon from '@/components/Icon.vue'

const ACCEPT = '.txt,.md,.pdf,.docx,.xlsx,.xls,.csv'
const ACCEPT_SET = new Set(['.txt', '.md', '.pdf', '.docx', '.xlsx', '.xls', '.csv'])

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
const showUpload = ref(false)
const uploadMode = ref<'raw' | 'template'>('raw')
const pendingFiles = ref<File[]>([])
const uploading = ref(false)
const uploadErr = ref('')
const uploadProgress = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
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
    return
  }
  loading.value = true
  err.value = ''
  try {
    kb.value = await getKb(teamId.value, kbId.value)
    docs.value = await listDocs(teamId.value, kbId.value)
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
}

function closeUpload() {
  if (uploading.value) return
  showUpload.value = false
}

function extOf(name: string) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function addFiles(list: FileList | File[] | null) {
  if (!list) return
  const next = [...pendingFiles.value]
  const seen = new Set(next.map((f) => `${f.name}:${f.size}:${f.lastModified}`))
  for (const f of Array.from(list)) {
    const ext = extOf(f.name)
    if (!ACCEPT_SET.has(ext)) {
      uploadErr.value = `不支持的格式: ${ext || f.name}`
      continue
    }
    const key = `${f.name}:${f.size}:${f.lastModified}`
    if (seen.has(key)) continue
    seen.add(key)
    next.push(f)
  }
  pendingFiles.value = next
}

function removeFile(idx: number) {
  pendingFiles.value = pendingFiles.value.filter((_, i) => i !== idx)
}

function onPick(ev: Event) {
  const input = ev.target as HTMLInputElement
  addFiles(input.files)
  input.value = ''
}

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
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragDepth = 0
  dragOver.value = false
  addFiles(e.dataTransfer?.files ?? null)
}

async function onConfirmUpload() {
  if (!teamId.value || !kbId.value || !pendingFiles.value.length) return
  uploading.value = true
  uploadErr.value = ''
  try {
    if (uploadMode.value === 'template') {
      uploadProgress.value = `模板转换中（${pendingFiles.value.length} 个文件）…`
      const batch = await uploadTemplateBatch(teamId.value, kbId.value, pendingFiles.value)
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
      const f = pendingFiles.value[i]
      uploadProgress.value = `上传中 ${i + 1}/${total}：${f.name}`
      const d = await uploadFile(teamId.value, kbId.value, f, 'raw')
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

watch([teamId, kbId], () => {
  activeDocId.value = null
  docContent.value = ''
  reload()
})

onMounted(reload)
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

    <div v-if="err" class="err">{{ err }}</div>
    <div v-else-if="loading" class="empty">加载中…</div>
    <div v-else-if="!kb" class="empty">知识库不存在或无权访问</div>

    <div v-else class="layout">
      <aside class="docs">
        <div class="docs-head">文档 ({{ docs.length }})</div>
        <div v-if="!docs.length" class="muted pad">暂无文档，点击右上角「上传」添加</div>
        <button
          v-for="d in docs"
          :key="d.id"
          class="doc-item"
          :class="{ active: d.id === activeDocId }"
          @click="openDoc(d.id)"
        >
          <div class="doc-title">{{ d.title }}</div>
          <div class="doc-meta">{{ d.sourceFormat }} · {{ d.status }}</div>
        </button>
      </aside>

      <section class="reader">
        <div v-if="!activeDocId" class="empty muted">选择左侧文档查看正文</div>
        <template v-else>
          <div class="reader-head">
            <h2>{{ docs.find((d) => d.id === activeDocId)?.title }}</h2>
            <button v-if="canManage" class="link danger" @click="onDeleteDoc(activeDocId!)">删除</button>
          </div>
          <pre v-if="docLoading" class="body muted">加载中…</pre>
          <pre v-else class="body">{{ docContent }}</pre>
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

        <div
          class="drop"
          :class="{ 'is-drag': dragOver }"
          @click="fileInput?.click()"
          @dragenter="onDragEnter"
          @dragleave="onDragLeave"
          @dragover="onDragOver"
          @drop="onDrop"
        >
          <div class="drop-ico"><Icon name="folder" :size="28" /></div>
          <div class="drop-t">点击选择，或拖拽文件到此处</div>
          <div class="drop-s">支持 txt / md / pdf / docx / xlsx / csv · 可多选 · 单文件 ≤ 20MB</div>
        </div>
        <input
          ref="fileInput"
          type="file"
          hidden
          multiple
          :accept="ACCEPT"
          @change="onPick"
        />

        <ul v-if="pendingFiles.length" class="file-list">
          <li v-for="(f, i) in pendingFiles" :key="`${f.name}-${f.size}-${i}`">
            <span class="fn" :title="f.name">{{ f.name }}</span>
            <span class="fs">{{ (f.size / 1024).toFixed(1) }} KB</span>
            <button class="link" :disabled="uploading" @click="removeFile(i)">移除</button>
          </li>
        </ul>

        <div v-if="uploadErr" class="err sm">{{ uploadErr }}</div>
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
              <b>{{ it.filename }}</b>
              <span class="tag" :class="{ ok: it.convertStatus === 'ok' }">
                {{ it.convertStatus === 'ok' ? '可导入' : '失败' }}
              </span>
            </label>
            <p v-if="it.errorMessage" class="err sm">{{ it.errorMessage }}</p>
            <pre v-if="it.previewMarkdown" class="preview-md">{{ it.previewMarkdown }}</pre>
          </article>
        </div>
        <div v-if="previewErr" class="err sm">{{ previewErr }}</div>
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
.docs-head { padding: 12px 14px; font-weight: 600; border-bottom: 1px solid var(--border); color: var(--text-1); position: sticky; top: 0; background: var(--surface); }
.doc-item {
  display: block; width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--border);
  background: transparent; padding: 10px 14px; cursor: pointer;
}
.doc-item:hover { background: var(--surface-3); }
.doc-item.active { background: var(--primary-soft); }
.doc-title { font-size: 13px; color: var(--text-1); font-weight: 500; }
.doc-meta { font-size: 12px; color: var(--text-3); margin-top: 2px; }
.reader { padding: 14px 16px; display: flex; flex-direction: column; min-height: calc(100vh - 180px); }
.reader-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; gap: 8px; }
.reader-head h2 { margin: 0; font-size: 16px; color: var(--text-1); }
.body {
  flex: 1; margin: 0; padding: 12px; background: var(--surface-3); border-radius: var(--radius);
  font-size: 13px; line-height: 1.55; white-space: pre-wrap; word-break: break-word;
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
  border: 1.5px dashed var(--border-strong); border-radius: var(--radius-lg);
  padding: 28px 16px; text-align: center; cursor: pointer; transition: all .15s; background: var(--surface);
}
.drop:hover, .drop.is-drag { border-color: var(--primary); background: var(--primary-soft); }
.drop-ico { color: var(--text-4); margin-bottom: 8px; display: flex; justify-content: center; }
.drop-t { font-size: 14px; color: var(--text-1); }
.drop-s { font-size: 12px; color: var(--text-3); margin-top: 6px; }
.file-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow: auto; }
.file-list li {
  display: flex; align-items: center; gap: 10px; padding: 8px 10px;
  background: var(--primary-soft); border: 1px solid var(--primary-tint); border-radius: var(--radius); font-size: 13px;
}
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
  font-size: 12px; line-height: 1.45; white-space: pre-wrap; word-break: break-word;
  max-height: 180px; overflow: auto; color: var(--text-1);
}

@media (max-width: 800px) {
  .layout { grid-template-columns: 1fr; min-height: auto; }
  .docs, .reader { max-height: none; min-height: 280px; }
}
</style>
