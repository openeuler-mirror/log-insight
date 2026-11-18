<script setup lang="ts">
/* 智能诊断: 对话优先
   左: 资料栏(日志树 + 运行状态)   中: 诊断对话   右: 按需展开的日志原文 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import DiagnosisFileTree from '@/components/DiagnosisFileTree.vue'
import DiagnosisFileReader from '@/components/DiagnosisFileReader.vue'
import DiagnosisInspector from '@/components/DiagnosisInspector.vue'
import ComposerModelMenu from '@/components/diagnosis/ComposerModelMenu.vue'
import ComposerKbMenu from '@/components/diagnosis/ComposerKbMenu.vue'
import ComposerTaskDock from '@/components/diagnosis/ComposerTaskDock.vue'
import AssistantTurn from '@/components/diagnosis/AssistantTurn.vue'
import FoldChevron from '@/components/diagnosis/FoldChevron.vue'
import SessionHistoryList from '@/components/diagnosis/SessionHistoryList.vue'
import ErrorAlert from '@/components/ErrorAlert.vue'
import Icon from '@/components/Icon.vue'
import { copyText } from '@/utils/clipboard'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import type { Attach, FilePayload, ReaderTab } from '@/components/diagnosis/diagnosisFiles'
import { apiError } from '@/api/http'
import { fetchFile as fetchFsFile, rawUrl, watchUrl, type FsChange } from '@/api/fs'
import {
  agentStatus,
  createSession,
  deleteMessage,
  fetchCatalog,
  listFiles,
  listMessages,
  listSessions,
  patchSession,
  readFile as fetchFileContent,
  saveTeamSelection,
  sendMessage,
  type AgentStatus,
  type CatalogTeam,
  type ChatMessage,
  type FileNode,
  type FileRef,
  type KbBlock,
  type MessageBlock,
  type SessionInfo,
  type ToolBlock,
} from '@/api/diagnosis'

/* ---------------- 侧栏 / 日志面板宽度 ---------------- */
const LEFT_MIN = 240
const LEFT_MAX = 400
const DRAWER_MIN = 360
const DRAWER_MAX = 760

const leftW = ref(276)
const leftCollapsed = ref(false)
const historyOpen = ref(true)
const filesOpen = ref(true)
const drawerW = ref(560)
const drawerVisible = ref(false)

type DragSide = 'left' | 'drawer'
let dragging: DragSide | null = null
let dragStartX = 0
let dragStartW = 0

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function onMove(e: MouseEvent) {
  if (!dragging) return
  const dx = e.clientX - dragStartX
  if (dragging === 'left') leftW.value = clamp(dragStartW + dx, LEFT_MIN, LEFT_MAX)
  else drawerW.value = clamp(dragStartW - dx, DRAWER_MIN, DRAWER_MAX)
}
function stopDrag() {
  dragging = null
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', stopDrag)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}
function startDrag(side: DragSide, e: MouseEvent) {
  dragging = side
  dragStartX = e.clientX
  dragStartW = side === 'left' ? leftW.value : drawerW.value
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', stopDrag)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
function toggleLeft() {
  leftCollapsed.value = !leftCollapsed.value
}

/* ---------------- 运行状态 / 团队模型 ---------------- */
const statusOpen = ref(false)
const status = ref<AgentStatus | null>(null)
const catalog = ref<CatalogTeam[]>([])
const errorText = ref('')

const teamsStore = useTeamsStore()
const rbac = useRbacStore()
const ready = computed(() => !!status.value?.ready)
const modelLabel = computed(() => status.value?.modelName || status.value?.model || '—')
const modelEmpty = computed(() => !catalog.value.some((t) => t.models.length))
/* 大模型页签仅管理者可见：无管理权限的成员不引导去「团队配置」 */
const canConfigureModel = computed(() => {
  const tid = status.value?.teamId
  return !tid || teamsStore.can(tid, 'llm:manage')
})
const permissionLabel = computed(() => {
  const mode = status.value?.permission ?? ''
  if (mode === 'workspace-write') return '只能写本次诊断目录'
  if (mode === 'danger-full-access') return '不受限（仅调试用）'
  return '只读'
})

async function loadCatalog() {
  try {
    catalog.value = (await fetchCatalog()).teams
  } catch {
    catalog.value = []
  }
}

async function refreshStatus(teamId?: number | null) {
  try {
    status.value = await agentStatus(teamId ?? undefined)
  } catch (e) {
    errorText.value = apiError(e)
  }
}

/* 本次会话显式选择的模型 (未选择时沿用历史会话自己的模型) */
const pickedModel = ref<{ modelRowId: number; reasoning: string } | null>(null)

/* 输入框内切换模型/推理强度: 保存到当前团队的选择 */
async function onModelChange(payload: { teamId: number; modelRowId: number; reasoning: string }) {
  pickedModel.value = { modelRowId: payload.modelRowId, reasoning: payload.reasoning }
  try {
    status.value = await saveTeamSelection({
      team_id: payload.teamId,
      model_row_id: payload.modelRowId,
      reasoning: payload.reasoning,
    })
    await loadCatalog()
  } catch (e) {
    errorText.value = apiError(e)
  }
}

/* ---------------- 日志文件 ---------------- */
const fileNodes = ref<FileNode[]>([])
const filesLoading = ref(false)
const attached = ref<Attach[]>([])
/* 文件树的勾选由 attached 驱动, 会话切换时用会话记录还原 */
const attachedIds = computed(() => attached.value.map((a) => a.id))
const treeRef = ref<InstanceType<typeof DiagnosisFileTree> | null>(null)

/* ---------------- 排队发送 ---------------- */
/* 处理中新消息进入排队, 当前轮结束或被中止后依次自动发送 */
let queuedSeq = 0

async function loadFiles() {
  filesLoading.value = true
  try {
    fileNodes.value = await listFiles()
  } catch (e) {
    errorText.value = apiError(e)
  } finally {
    filesLoading.value = false
  }
}

/* 刷新文件列表: 任务日志重拉接口, 本地目录通过 rootSeq 自增触发整树重载 */
function refreshFiles() {
  if (sourceMode.value === 'log') void loadFiles()
  else if (rootPath.value) rootSeq.value++
}

/* 勾选变化: 树上已加载的节点直接给 Attach, 未加载(深层/会话恢复)的沿用旧记录 */
function onTreeSelect(files: Attach[], ids: string[]) {
  const prev = new Map(attached.value.map((a) => [a.id, a]))
  const known = new Map(files.map((f) => [f.id, f]))
  attached.value = ids.map((id) => known.get(id) ?? prev.get(id) ?? {
    id,
    name: id.split(/[\\/]/).pop() || id,
    path: id,
  })
  scheduleSaveFiles()
}
function detachFile(id: string) {
  attached.value = attached.value.filter((a) => a.id !== id)
  treeRef.value?.deselect(id)
  scheduleSaveFiles()
}
function clearAttached() {
  attached.value = []
  treeRef.value?.clear()
  /* 模板「清空」按钮直接绑定本函数, 清空也要持久化, 否则刷新/切回勾选复原 */
  scheduleSaveFiles()
}

/* 勾选持久化到会话: 连续勾选做 200ms 去抖, 避免频繁 PATCH */
let saveFilesTimer: number | null = null
function scheduleSaveFiles() {
  if (saveFilesTimer !== null) window.clearTimeout(saveFilesTimer)
  saveFilesTimer = window.setTimeout(() => {
    saveFilesTimer = null
    void saveSessionFiles()
  }, 200)
}

/* 会话局部更新: PATCH 后把返回值合并回 sessions 列表 */
async function patchAndMergeSession(sid: string, payload: Parameters<typeof patchSession>[1]) {
  try {
    const updated = await patchSession(sid, payload)
    const idx = sessions.value.findIndex((s) => s.id === sid)
    if (idx >= 0) sessions.value[idx] = { ...sessions.value[idx], ...updated }
    return updated
  } catch (e) {
    errorText.value = apiError(e)
    return undefined
  }
}

/* 上次持久化的勾选 id 序列: 会话恢复触发树同步 emit 时内容未变, 跳过冗余 PATCH */
let lastSavedFileKey = ''
function filesKey() {
  return attached.value.map((a) => a.id).join('\n')
}

async function saveSessionFiles() {
  const sid = activeSessionId.value
  if (!sid) return
  const key = filesKey()
  if (key === lastSavedFileKey) return
  const files = attached.value.map((a) => ({ id: a.id, name: a.name, path: a.path, size: a.size ?? '' }))
  const updated = await patchAndMergeSession(sid, { selectedFiles: files })
  if (updated) lastSavedFileKey = key
}

/* ---------------- 文件树数据源: 任务日志 / 本地目录 ----------------
   本地目录由后端 /api/fs 提供, watchdog 递归监听所选目录并经 SSE 推送变更;
   前端据此刷新受影响的已加载目录, 并对已打开且无未保存编辑的文件自动重载最新内容。 */
const sourceMode = ref<'log' | 'local'>('log')
const rootPath = ref('')
const rootSeq = ref(0)
const fsEvents = ref<FsChange[]>([])
let fsSource: EventSource | null = null
let fsCursor = 0

/* 事件缓存上限: 超出后丢弃最旧的一段, 防止长时间运行无界增长 */
const FS_EVENTS_MAX = 2000

function startWatch(path: string) {
  fsSource?.close()
  fsSource = null
  if (!path) return
  const es = new EventSource(watchUrl(path))
  es.addEventListener('change', (e) => {
    try {
      fsEvents.value.push(JSON.parse((e as MessageEvent).data) as FsChange)
      if (fsEvents.value.length > FS_EVENTS_MAX) fsEvents.value.splice(0, fsEvents.value.length - FS_EVENTS_MAX)
    } catch {
      /* 忽略异常事件包 */
    }
  })
  fsSource = es
}

/* 重新选择目录: 释放上一次目录的事件缓存与待重载队列后重启监听(rootSeq 自增以便重选同一目录也生效),
   目录绑定与勾选清空合并为一次 PATCH 持久化到当前会话 */
function pickRoot(path: string) {
  rootPath.value = path
  rootSeq.value++
  fsEvents.value = []
  fsCursor = 0
  dirtyPaths.clear()
  if (reloadTimer !== null) {
    window.clearTimeout(reloadTimer)
    reloadTimer = null
  }
  startWatch(path)
  /* 目录变了, 旧的勾选路径已失效 */
  clearAttached()
  void saveSessionRoot(path)
}

/* 目录绑定到会话: root 与清空后的勾选一次提交, 切回该会话时自动恢复目录 */
async function saveSessionRoot(path: string) {
  const sid = activeSessionId.value
  if (!sid) return
  /* clearAttached 的去抖保存与本次合并提交内容一致, 先对齐基准避免重复 PATCH */
  lastSavedFileKey = filesKey()
  await patchAndMergeSession(sid, { localRoot: path, selectedFiles: [] })
}

/* 会话切换: 恢复该会话绑定的本地目录(存在则自动切到本地目录模式), 无绑定则清空 */
function applySessionRoot(sess?: SessionInfo) {
  const path = sess?.localRoot || ''
  /* 勾选随会话持久化: 切换会话还原该会话勾选的文件 */
  attached.value = (sess?.selectedFiles ?? []) as Attach[]
  /* 恢复即已持久化的内容, 树同步 emit 回环时跳过冗余 PATCH */
  lastSavedFileKey = filesKey()
  fsSource?.close()
  fsSource = null
  fsEvents.value = []
  fsCursor = 0
  dirtyPaths.clear()
  rootPath.value = path
  rootSeq.value++
  if (path) {
    sourceMode.value = 'local'
    startWatch(path)
  }
}

/* 释放本地目录数据源的全部瞬时状态并断开监听 */
function resetLocal() {
  fsSource?.close()
  fsSource = null
  fsEvents.value = []
  fsCursor = 0
  dirtyPaths.clear()
  if (reloadTimer !== null) {
    window.clearTimeout(reloadTimer)
    reloadTimer = null
  }
  rootPath.value = ''
  rootSeq.value++
}

/* 切换数据源: 保留已选本地目录与勾选, 切回时重载树并恢复监听;
   目录与勾选都属于会话, 只有换目录/切会话/新建会话时才变化 */
function setSource(mode: 'log' | 'local') {
  if (mode === sourceMode.value) return
  sourceMode.value = mode
  if (mode === 'local') {
    if (rootPath.value) {
      fsEvents.value = []
      fsCursor = 0
      rootSeq.value++
      startWatch(rootPath.value)
    }
  } else {
    fsSource?.close()
    fsSource = null
  }
}

/* 已打开文件随磁盘变化自动重载(有未保存编辑时不覆盖); 同名批量事件做去抖合并 */
const dirtyPaths = new Set<string>()
let reloadTimer: number | null = null

async function reloadTab(t: ReaderTab, path: string) {
  try {
    const r = await fetchFsFile(path)
    const content = r.kind === 'image' ? rawUrl(path) : (r.content ?? '')
    t.kind = r.kind
    t.content = content
    t.original = content
    t.lang = r.lang
    t.size = fmtSize(r.size)
  } catch {
    /* 文件可能正被写入 / 已删除, 忽略 */
  }
}

function scheduleReload(path: string) {
  dirtyPaths.add(path)
  if (reloadTimer !== null) window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(() => {
    reloadTimer = null
    const paths = Array.from(dirtyPaths)
    dirtyPaths.clear()
    for (const p of paths) {
      const t = tabs.value.find((x) => x.id === p)
      if (t && t.content === t.original) void reloadTab(t, p)
    }
  }, 300)
}

watch(
  () => fsEvents.value.length,
  () => {
    const list = fsEvents.value
    /* 事件数组被截断(超出上限)后游标越界, 重置重新对齐 */
    if (fsCursor > list.length) fsCursor = 0
    for (; fsCursor < list.length; fsCursor++) {
      const ev = list[fsCursor]
      if (ev.kind !== 'file') continue
      const target = ev.src ?? ev.path
      const t = tabs.value.find((x) => x.id === target)
      if (!t) continue
      if (ev.op === 'deleted') {
        if (t.content === t.original) closeTab(t.id)
        continue
      }
      if (ev.op === 'moved') {
        const oldId = t.id
        const name = ev.path.split(/[\\/]/).pop() ?? t.name
        if (activeTabId.value === oldId) activeTabId.value = ev.path
        t.id = ev.path
        t.name = name
        t.path = ev.path
        fileCache.delete(oldId)
        fileCache.set(ev.path, t)
      }
      scheduleReload(t.id)
    }
  },
)

/* ---------------- 中间阅读器 ---------------- */
const tabs = ref<ReaderTab[]>([])
const activeTabId = ref('')
const fileCache = new Map<string, ReaderTab>()

function fmtSize(bytes?: number) {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

async function openFile(f: Attach, payload?: FilePayload) {
  drawerVisible.value = true
  const cached = fileCache.get(f.id)
  if (cached) {
    if (!tabs.value.some((t) => t.id === f.id)) tabs.value.push(cached)
    activeTabId.value = f.id
    return
  }
  try {
    let tab: ReaderTab
    if (payload) {
      /* 本地磁盘文件: 内容由文件树读取后传入 */
      tab = {
        ...f,
        kind: payload.kind,
        content: payload.content,
        original: payload.content,
        lang: payload.lang,
      }
    } else {
      const p = await fetchFileContent(f.id)
      tab = {
        ...f,
        size: fmtSize(p.size) || f.size,
        kind: p.kind,
        content: p.content,
        original: p.content,
        lang: p.lang,
      }
    }
    fileCache.set(f.id, tab)
    tabs.value.push(tab)
    activeTabId.value = tab.id
  } catch (e) {
    errorText.value = apiError(e)
  }
}
function closeTab(id: string) {
  const i = tabs.value.findIndex((t) => t.id === id)
  if (i < 0) return
  tabs.value.splice(i, 1)
  if (activeTabId.value === id) activeTabId.value = tabs.value[Math.max(0, i - 1)]?.id ?? ''
  if (!tabs.value.length) drawerVisible.value = false
}
function closeDrawer() {
  drawerVisible.value = false
}
function reopenDrawer() {
  if (tabs.value.length) drawerVisible.value = true
}
function attachFromReader(tab: ReaderTab) {
  if (treeRef.value) {
    treeRef.value.select(tab.id)
    return
  }
  if (!attached.value.some((a) => a.id === tab.id)) {
    attached.value.push({ id: tab.id, name: tab.name, path: tab.path, size: tab.size })
  }
}

/* ---------------- 会话 ---------------- */
const sessions = ref<SessionInfo[]>([])
const activeSessionId = ref('')
const messages = ref<ChatMessage[]>([])
const loadingSession = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

const currentSession = computed(() => sessions.value.find((s) => s.id === activeSessionId.value))
const currentTitle = computed(() => currentSession.value?.title || '新的诊断')
const sessionTeamId = computed(() => currentSession.value?.teamId ?? status.value?.teamId ?? null)
const canKbSearch = computed(() => {
  const tid = sessionTeamId.value
  return !!tid && (rbac.isPlatformAdmin || teamsStore.can(tid, 'kb:search'))
})
const sessionKbIds = computed(() => currentSession.value?.selectedKbIds ?? [])
const sessionKbTopK = computed(() => currentSession.value?.kbTopK ?? 10)

/* ---------------- 会话级瞬时状态 ----------------
   输入草稿 / 排队消息 / 运行态都按会话隔离: 一个会话在后台继续诊断时切走,
   状态不会串到另一个会话, 切回来还能接着看进度。 */
type QueuedItem = {
  id: number
  text: string
  refs: FileRef[]
  files: { kind: 'file'; name: string; taskId?: string }[]
  /* 入队时刻的模型选择, 后台续发时不跟随当前输入框的选择漂移 */
  model?: { modelRowId: number; reasoning: string }
}

type RunState = {
  draft: string
  queue: QueuedItem[]
  running: boolean
  phase: string
  tools: ToolBlock[]
  usage: { input: number; output: number; total: number }
  /* 在飞轮次的流式消息 (未落库), 切回该会话时重新挂到列表末尾 */
  live: ChatMessage | null
  ctl: AbortController | null
}

function emptyRun(): RunState {
  return {
    draft: '',
    queue: [],
    running: false,
    phase: '',
    tools: [],
    usage: { input: 0, output: 0, total: 0 },
    live: null,
    ctl: null,
  }
}

/* 只读占位: 未创建过状态的会话不会把数据写进共享对象 */
const EMPTY_RUN = emptyRun()
const runs = reactive<Record<string, RunState>>({})

function runOf(sid: string): RunState {
  /* 统一返回 reactive 代理: 直接返回原始对象会导致后续改动不触发视图更新 */
  if (!runs[sid]) runs[sid] = emptyRun()
  return runs[sid]
}

const activeRun = computed<RunState>(() => runs[activeSessionId.value] ?? EMPTY_RUN)

/* 输入框内容跟随会话: 切换会话显示各自的草稿 */
const draft = computed({
  get: () => activeRun.value.draft,
  set: (v: string) => {
    if (activeSessionId.value) runOf(activeSessionId.value).draft = v
  },
})
const running = computed(() => activeRun.value.running)
const phase = computed(() => activeRun.value.phase)
const runTools = computed(() => activeRun.value.tools)
const queue = computed(() => activeRun.value.queue)
const streamUsage = computed(() => activeRun.value.usage)

async function onKbChange(payload: { selectedIds: number[]; topK: number }) {
  const sid = activeSessionId.value
  if (!sid) return
  await patchAndMergeSession(sid, {
    selectedKbIds: payload.selectedIds,
    kbTopK: payload.topK,
  })
}

/* 输入框展示当前会话实际使用的模型 (未显式切换时取向会话快照) */
const composerStatus = computed<AgentStatus | null>(() => {
  if (!status.value) return null
  const sess = currentSession.value
  if (pickedModel.value || !sess?.model) return status.value
  return {
    ...status.value,
    teamId: sess.teamId ?? status.value.teamId,
    provider: sess.provider || status.value.provider,
    model: sess.model,
    modelName: sess.modelName || sess.model,
    reasoning: sess.reasoning || '',
  }
})

async function scrollBottom() {
  await nextTick()
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
  onBodyScroll()
}

/* 回到底部: 离开底部超过阈值时浮出按钮 (以最近一条消息为参考) */
const atBottom = ref(true)

function onBodyScroll() {
  const el = bodyRef.value
  if (el) atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

async function jumpBottom() {
  await nextTick()
  const el = bodyRef.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  /* 平滑滚动目标在开始时固定, 期间悬停操作栏展开会改变内容高度, 结束后再补对齐 */
  window.setTimeout(() => {
    const cur = bodyRef.value
    if (cur) cur.scrollTop = cur.scrollHeight
  }, 360)
}

async function loadMessageList(id: string) {
  loadingSession.value = true
  pickedModel.value = null
  try {
    const rows = await listMessages(id)
    /* 快速连续切换会话时, 丢弃已不是当前会话的过期响应 */
    if (activeSessionId.value !== id) return
    const run = runs[id]
    /* 该会话正在后台跑: live 消息尚未落库, 重新挂回列表末尾继续接收事件 */
    if (run?.running && run.live) rows.push(run.live)
    messages.value = rows
    applySessionRoot(sessions.value.find((s) => s.id === id))
    if (!run?.running) resetRun(id)
  } catch (e) {
    errorText.value = apiError(e)
  } finally {
    loadingSession.value = false
    /* 等 loading 占位退出、消息真正渲染后再定位到最新消息 */
    if (activeSessionId.value === id) await scrollBottom()
  }
}

async function selectSession(id: string) {
  if (id === activeSessionId.value) return
  /* 允许切走正在诊断的会话: 该轮次在后台继续, 切回来重新挂上进度 */
  activeSessionId.value = id
  const sess = sessions.value.find((s) => s.id === id)
  if (sess?.teamId) void refreshStatus(sess.teamId)
  await loadMessageList(id)
}

async function refreshSessions() {
  try {
    sessions.value = await listSessions()
    if (!sessions.value.some((s) => s.id === activeSessionId.value)) {
      const next = sessions.value[0]
      if (next) {
        activeSessionId.value = next.id
        if (next.teamId) void refreshStatus(next.teamId)
        await loadMessageList(next.id)
      } else {
        await addSession()
      }
    }
  } catch {
    /* 忽略 */
  }
}

async function addSession() {
  try {
    const s = await createSession('', status.value?.teamId || undefined)
    sessions.value.unshift(s)
    activeSessionId.value = s.id
    messages.value = []
    pickedModel.value = null
    /* 新建会话: 释放上一会话的本地目录选择与监听, 勾选也从空开始 */
    resetLocal()
    attached.value = []
    lastSavedFileKey = ''
    resetRun(s.id)
  } catch (e) {
    errorText.value = apiError(e)
  }
}

/* ---------------- 一轮诊断 ---------------- */
/* draft / running / phase / tools / usage 都是当前会话运行态的只读视图 */

const usageTotals = computed(() => {
  let input = 0
  let output = 0
  let total = 0
  for (const m of messages.value) {
    if (m.role !== 'assistant' || !m.usage) continue
    const u = m.usage as Record<string, unknown>
    input += numOf(u, ['inputTokens', 'input_tokens', 'promptTokens', 'prompt_tokens'])
    output += numOf(u, ['outputTokens', 'output_tokens', 'completionTokens', 'completion_tokens'])
    total += numOf(u, ['totalTokens', 'total_tokens'])
  }
  if (!total) total = input + output
  const live = streamUsage.value
  return {
    input: input + live.input,
    output: output + live.output,
    total: total + live.total,
  }
})

const modelInfo = computed(() => ({
  model: modelLabel.value,
  permission: permissionLabel.value,
  skills: status.value?.skills.length ?? 0,
  ...usageTotals.value,
}))

const inspectorTools = computed(() =>
  runTools.value.map((t) => ({
    name: t.title || t.name || '工具',
    target: (t.input || '').replace(/\s+/g, ' ').slice(0, 80),
    status: t.status,
  })),
)

function numOf(obj: Record<string, unknown>, keys: string[]): number {
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'number') return v
    if (typeof v === 'string' && v && !Number.isNaN(Number(v))) return Number(v)
  }
  return 0
}

function resetRun(sid: string) {
  const run = runOf(sid)
  run.tools = []
  run.usage.input = 0
  run.usage.output = 0
  run.usage.total = 0
  run.phase = ''
}

function normalizeKbBlock(data: Record<string, unknown>): KbBlock {
  const raw = (data.citations as Record<string, unknown>[] | undefined) || []
  return {
    kind: 'kb',
    citations: raw.map((c) => ({
      index: Number(c.index ?? 0),
      chunkId: Number(c.chunkId ?? c.chunk_id ?? 0),
      documentId: Number(c.documentId ?? c.document_id ?? 0),
      kbId: Number(c.kbId ?? c.kb_id ?? 0),
      kbName: String(c.kbName ?? c.kb_name ?? ''),
      title: String(c.title ?? ''),
      content: String(c.content ?? ''),
      score: Number(c.score ?? 0),
    })),
    queries: (data.queries as string[] | undefined) || [],
    note: String(data.note ?? ''),
  }
}

function appendText(msg: ChatMessage, kind: 'text' | 'thought', text: string) {
  const last = msg.blocks[msg.blocks.length - 1]
  if (last && last.kind === kind) {
    last.text += text
  } else {
    msg.blocks.push({ kind, text })
  }
}

type RawToolEvent = {
  id: string
  name?: string
  title?: string
  toolKind?: string
  status?: string
  input?: string
  output?: string
}

function upsertTool(msg: ChatMessage, run: RunState, data: RawToolEvent) {
  const statusValue: ToolBlock['status'] =
    data.status === 'completed' || data.status === 'done'
      ? 'done'
      : data.status === 'failed'
        ? 'failed'
        : 'running'
  let block = msg.blocks.find((b): b is ToolBlock => b.kind === 'tool' && b.id === data.id)
  if (!block) {
    block = {
      kind: 'tool',
      id: data.id,
      name: data.name || data.title || '工具',
      title: data.title || '',
      toolKind: data.toolKind,
      status: statusValue,
      input: data.input || '',
      output: data.output || '',
    }
    msg.blocks.push(block)
  } else {
    /* 增量 update 往往只带 status/output, 不能把已有标题清空 */
    if (data.name) block.name = data.name
    if (data.title) block.title = data.title
    if (data.toolKind) block.toolKind = data.toolKind
    if (data.input) block.input = data.input
    if (data.output) block.output = data.output
    block.status = statusValue
  }
  const row = run.tools.find((t) => t.id === data.id)
  if (row) Object.assign(row, block)
  else run.tools.push({ ...block } as ToolBlock)
}

function addUsage(run: RunState, raw: unknown) {
  if (!raw || typeof raw !== 'object') return
  const u = raw as Record<string, unknown>
  run.usage.input += numOf(u, ['inputTokens', 'input_tokens', 'promptTokens', 'prompt_tokens'])
  run.usage.output += numOf(u, ['outputTokens', 'output_tokens', 'completionTokens', 'completion_tokens'])
  run.usage.total += numOf(u, ['totalTokens', 'total_tokens'])
}

function mergeUsage(msg: ChatMessage, run: RunState, raw: unknown) {
  if (!raw || typeof raw !== 'object') return
  const u = raw as Record<string, unknown>
  msg.usage = { ...(msg.usage ?? {}), ...u }
  // 会话级统计已包含 live 用量, 这里清零避免重复
  run.usage.input = 0
  run.usage.output = 0
  run.usage.total = 0
}

function fileBlocks(m: ChatMessage) {
  return m.blocks.filter(
    (b): b is Extract<MessageBlock, { kind: 'file' }> => b.kind === 'file',
  )
}

function textBlocks(m: ChatMessage) {
  return m.blocks.filter(
    (b): b is Extract<MessageBlock, { kind: 'text' }> => b.kind === 'text',
  )
}

/* 消息复制: 悬停浮现操作栏, 只取正文 text 块 (思考/工具不算) */
const copiedId = ref<string | number>('')
let copyTimer: number | undefined

async function copyMessage(m: ChatMessage) {
  const content = textBlocks(m).map((b) => b.text).join('\n\n')
  if (!content || !(await copyText(content))) return
  copiedId.value = m.id
  window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => { copiedId.value = '' }, 1600)
}

onBeforeUnmount(() => window.clearTimeout(copyTimer))

/* 消息删除: 生成中的消息等价于停止本轮并从列表移除, 已落库的走接口删除 */
const removingId = ref<string | number>('')

async function removeMessage(m: ChatMessage) {
  const sid = activeSessionId.value
  if (!sid || removingId.value) return
  /* 生成中的消息: 变相触发停止, 再把该条移出列表 */
  if (m.id === 'live') {
    runs[sid]?.ctl?.abort()
    messages.value = messages.value.filter((x) => x.id !== 'live')
    return
  }
  /* 尚未拿到后端 id 的本地消息: 只从当前列表移除 */
  if (typeof m.id !== 'number') {
    messages.value = messages.value.filter((x) => x.id !== m.id)
    return
  }
  removingId.value = m.id
  try {
    await deleteMessage(sid, m.id)
    messages.value = messages.value.filter((x) => x.id !== m.id)
  } catch (e) {
    errorText.value = apiError(e)
  } finally {
    removingId.value = ''
  }
}

/* ---------------- 排队发送 ---------------- */
/* queue / QueuedItem 声明在顶部状态区; 这里是入队快照与编辑操作 */

/* 快照当前勾选的日志文件 (入队时刻的上下文) */
function snapshotContext() {
  const refs: FileRef[] = attached.value.map((f) => {
    // 日志文件 id 形如 "{taskId}|{rel}"; 本地文件 id 即绝对路径
    const cut = f.id.indexOf('|')
    if (cut > 0) return { taskId: f.id.slice(0, cut), name: f.id.slice(cut + 1) || f.name }
    return { path: f.path || f.id, name: f.name }
  })
  const files = attached.value.map((f) => ({
    kind: 'file' as const,
    name: f.name,
    taskId: f.id.split('|')[0],
  }))
  return { refs, files }
}

function removeQueued(id: number) {
  const sid = activeSessionId.value
  if (!sid) return
  const list = runOf(sid).queue
  const i = list.findIndex((q) => q.id === id)
  if (i >= 0) list.splice(i, 1)
}

function updateQueued(id: number, text: string) {
  const sid = activeSessionId.value
  if (!sid) return
  const item = runOf(sid).queue.find((q) => q.id === id)
  if (item) item.text = text
}

function send() {
  const text = draft.value.trim()
  if (!text) return
  if (!ready.value) {
    errorText.value = status.value?.reason || '请先在团队「大模型」中启用模型'
    return
  }
  const sid = activeSessionId.value
  if (!sid) return
  const { refs, files } = snapshotContext()
  const run = runOf(sid)
  const model = pickedModel.value ? { ...pickedModel.value } : undefined
  if (run.running) {
    // 正在处理: 进入排队, 当前轮结束或被停止后依次自动发送
    run.queue.push({ id: ++queuedSeq, text, refs, files, model })
    run.draft = ''
    void scrollBottom()
    return
  }
  void runTurn(sid, text, refs, files, model)
}

/* SSE 流事件分发: 按 event 类型更新 live 消息块与该会话的运行态 */
function applyStreamEvent(live: ChatMessage, run: RunState, event: string, data: any) {
  if (event === 'phase') {
    run.phase = data.phase ?? ''
  } else if (event === 'kb') {
    const block = normalizeKbBlock(data)
    const existing = live.blocks.find((b): b is KbBlock => b.kind === 'kb')
    if (existing) Object.assign(existing, block)
    else live.blocks.unshift(block)
  } else if (event === 'text') {
    appendText(live, 'text', data.text ?? '')
  } else if (event === 'thought') {
    appendText(live, 'thought', data.text ?? '')
  } else if (event === 'tool') {
    upsertTool(live, run, data as RawToolEvent)
  } else if (event === 'usage') {
    addUsage(run, data.usage)
  } else if (event === 'error') {
    live.error = data.message ?? '诊断失败'
  } else if (event === 'done') {
    if (data.messageId) live.id = data.messageId
    mergeUsage(live, run, data.usage)
  }
}

async function runTurn(
  sid: string,
  text: string,
  refs: FileRef[],
  files: { kind: 'file'; name: string; taskId?: string }[],
  model?: { modelRowId: number; reasoning: string },
) {
  const run = runOf(sid)
  const visible = () => activeSessionId.value === sid
  const live: ChatMessage = reactive({ id: 'live', role: 'assistant', blocks: [] })
  run.live = live
  /* 只有会话可见时才插到当前列表; 后台会话的消息等切回来再从后端 + live 恢复 */
  let userMsg: ChatMessage | null = null
  if (visible()) {
    userMsg = reactive<ChatMessage>({
      id: `u-${Date.now()}`,
      role: 'user',
      blocks: [{ kind: 'text', text }, ...files],
    })
    messages.value.push(userMsg)
    messages.value.push(live)
  }

  run.draft = ''
  resetRun(sid)
  errorText.value = ''
  run.running = true
  run.phase = 'preparing'
  await scrollBottom()

  const ctl = new AbortController()
  run.ctl = ctl
  let stopped = false

  try {
    await sendMessage(sid, text, refs, {
      onEvent(event, data) {
        /* 后端在开流时回传用户消息 id, 用于把本地占位换成可删除的真实 id */
        if (userMsg && typeof userMsg.id !== 'number' && data?.userMessageId) {
          userMsg.id = data.userMessageId
        }
        applyStreamEvent(live, run, event, data)
        /* 会话已切走, 或用户上翻查看历史时不跟随; 流式内容照常累积 */
        if (visible() && atBottom.value) void scrollBottom()
      },
    }, ctl.signal, model ?? {})
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      stopped = true
      live.error = '已停止本轮诊断'
    } else {
      const msg = apiError(e)
      if (!live.error) {
        live.error = msg
        if (visible()) errorText.value = msg
      }
    }
  } finally {
    run.running = false
    run.phase = ''
    run.ctl = null
    run.live = null
    if (!live.blocks.length && !live.error) {
      live.error = stopped ? '已停止本轮诊断' : '智能体没有返回内容'
    }
    // 刷新会话标题与排序
    try {
      sessions.value = await listSessions()
    } catch {
      /* 忽略 */
    }
    if (visible() && atBottom.value) await scrollBottom()
    // 当前轮结束或被中止后, 继续发送该会话排队中的下一条
    const next = run.queue.shift()
    if (next) void runTurn(sid, next.text, next.refs, next.files, next.model)
  }
}

function stop() {
  const sid = activeSessionId.value
  if (sid) runs[sid]?.ctl?.abort()
}

/* 删除会话时终止其运行中的轮次: 复用停止链路 (连接断开 -> 后端关流 -> DSH 子进程退出),
   并清空排队消息, 避免对已删会话继续发起注定 404 的请求 */
function onSessionsRemoved(ids: string[]) {
  for (const id of ids) {
    const run = runs[id]
    if (!run) continue
    run.queue.length = 0
    run.ctl?.abort()
  }
}

const examples = [
  {
    title: 'CPU 突然飙高',
    desc: '定位是哪个进程、为什么打满',
    prompt: '10:05 起 node-01 的 CPU 一直跑到 95%，帮我定位原因',
  },
  {
    title: '服务反复重启',
    desc: '找出退出原因和重启链路',
    prompt: '服务从昨晚开始反复重启，日志里能看到什么？',
  },
  {
    title: '磁盘 I/O 报错',
    desc: '判断是硬件劣化还是文件系统',
    prompt: '磁盘出现 I/O error，帮我判断是不是硬件故障',
  },
]
function useExample(text: string) {
  draft.value = text
}

/* ---------------- 拖拽加入上下文 ---------------- */
const dropActive = ref(false)
let dragDepth = 0
function onDragEnter() {
  dragDepth += 1
  dropActive.value = true
}
function onDragLeave() {
  dragDepth -= 1
  if (dragDepth <= 0) {
    dragDepth = 0
    dropActive.value = false
  }
}
function onDrop(e: DragEvent) {
  dragDepth = 0
  dropActive.value = false
  const raw = e.dataTransfer?.getData('text/plain') ?? ''
  try {
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      for (const f of parsed as Attach[]) {
        if (f?.id && !attached.value.some((a) => a.id === f.id)) attached.value.push(f)
      }
    }
  } catch {
    /* 不是文件拖拽 */
  }
}

/* ---------------- 启动 ---------------- */
onMounted(async () => {
  await refreshStatus()
  void loadCatalog()
  void loadFiles()
  try {
    sessions.value = await listSessions()
    if (!sessions.value.length) {
      const s = await createSession('', status.value?.teamId || undefined)
      sessions.value = [s]
    }
    const first = sessions.value[0]
    activeSessionId.value = first.id
    if (first.teamId) void refreshStatus(first.teamId)
    await loadMessageList(activeSessionId.value)
  } catch (e) {
    errorText.value = apiError(e)
  }
})

onBeforeUnmount(() => {
  stopDrag()
  /* 离开页面时中止所有在飞轮次 (含后台会话) */
  for (const run of Object.values(runs)) run.ctl?.abort()
  fsSource?.close()
  fsSource = null
  if (reloadTimer !== null) window.clearTimeout(reloadTimer)
})

const phaseText = computed(() => {
  if (!running.value) return ''
  if (phase.value === 'connecting') return '正在启动诊断助手…'
  if (phase.value === 'thinking') return '正在分析…'
  return '正在准备…'
})
</script>

<template>
  <div class="diag">
    <!-- ============ 左: 资料栏 ============ -->
    <aside v-if="!leftCollapsed" class="sidebar" :style="{ width: leftW + 'px' }">
      <!-- 历史会话 -->
      <section class="side-sec side-sec--history" :class="{ 'is-collapsed': !historyOpen }">
        <header class="side-sec-head" @click="historyOpen = !historyOpen">
          <FoldChevron :open="historyOpen" />
          <span class="side-sec-title">历史会话</span>
          <span v-if="sessions.length" class="side-count">{{ sessions.length }}</span>
          <span class="spacer"></span>
          <button class="icon-btn" title="新建会话" @click.stop="addSession">
            <Icon name="plus" :size="15" />
          </button>
        </header>
        <SessionHistoryList
          v-if="historyOpen"
          :sessions="sessions"
          :active-id="activeSessionId"
          @select="selectSession($event.id)"
          @changed="refreshSessions"
          @removed="onSessionsRemoved"
        />
      </section>

      <!-- 日志文件 / 本地目录 -->
      <section class="side-sec side-sec--files" :class="{ 'is-collapsed': !filesOpen }">
        <header class="side-sec-head" @click="filesOpen = !filesOpen">
          <FoldChevron :open="filesOpen" />
          <span class="side-sec-title">文件</span>
          <span v-if="attached.length" class="side-count">已选 {{ attached.length }}</span>
          <span class="spacer"></span>
          <div class="src-switch" @click.stop>
            <button
              class="src-btn"
              :class="{ 'is-on': sourceMode === 'log' }"
              @click="setSource('log')"
            >
              任务日志
            </button>
            <button
              class="src-btn"
              :class="{ 'is-on': sourceMode === 'local' }"
              @click="setSource('local')"
            >
              本地目录
            </button>
          </div>
          <button
            class="icon-btn"
            title="刷新文件列表"
            @click.stop="refreshFiles"
          >
            <Icon name="refresh" :size="15" />
          </button>
        </header>
        <div v-show="filesOpen" class="tree-wrap">
        <DiagnosisFileTree
          ref="treeRef"
          :source="sourceMode"
          :nodes="fileNodes"
          :loading="filesLoading"
          :root-path="rootPath"
          :root-seq="rootSeq"
          :events="fsEvents"
          :active-id="activeTabId"
          :selected-ids="attachedIds"
          @open="openFile"
          @select="onTreeSelect"
          @pick-root="pickRoot"
        >
          <template #empty>
            <div class="side-empty">
              <p class="side-empty-t">还没有可分析的日志</p>
              <p class="side-empty-s">上传日志后，勾选即可让助手直接读取原文。</p>
              <router-link class="o-btn o-btn--sm" to="/teams">去上传日志</router-link>
            </div>
          </template>
        </DiagnosisFileTree>
        </div>
      </section>

      <!-- 会话统计 / 状态 -->
      <section class="side-sec" :class="{ 'is-collapsed': !statusOpen }">
        <header class="side-sec-head" @click="statusOpen = !statusOpen">
          <FoldChevron :open="statusOpen" />
          <span class="side-sec-title">会话统计</span>
          <span class="status-dot" :class="running ? 'is-run' : ready ? 'is-ok' : 'is-warn'"></span>
          <span class="side-state">{{
            running ? phaseText : ready ? '就绪' : (status?.reason || '模型不可用')
          }}</span>
          <span class="spacer"></span>
          <span class="o-mono side-tokens">{{ usageTotals.total.toLocaleString('en-US') }}</span>
        </header>
        <DiagnosisInspector
          v-if="statusOpen"
          :model-info="modelInfo"
          :tools="inspectorTools"
        />
      </section>
    </aside>
    <div
      v-if="!leftCollapsed"
      class="resizer"
      title="拖动调整侧栏宽度"
      @mousedown="startDrag('left', $event)"
    ></div>

    <!-- ============ 中: 诊断对话 ============ -->
    <main class="chat">
      <header class="chat-head">
        <div class="head-left">
          <button
            class="icon-btn"
            :class="{ 'is-on': !leftCollapsed }"
            :title="leftCollapsed ? '显示侧栏' : '隐藏侧栏'"
            @click="toggleLeft"
          >
            <Icon name="sidebar" :size="15" />
          </button>
          <span class="session-title-text" :title="currentTitle">{{ currentTitle }}</span>
          <span v-if="running" class="pill pill--run">诊断中</span>
        </div>
        <div class="head-actions">
          <button v-if="tabs.length" class="o-btn o-btn--sm log-btn" @click="reopenDrawer">
            <Icon name="file" :size="13" />
            <span class="log-label">日志原文</span>
            <span class="log-count">({{ tabs.length }})</span>
          </button>
        </div>
      </header>

      <div v-if="!ready && status && messages.length" class="notice">
        <span class="notice-text">{{ status.reason }}</span>
        <router-link
          v-if="canConfigureModel"
          class="o-btn o-btn--sm"
          :to="status.teamId ? `/teams/${status.teamId}?tab=llm` : '/teams'"
        >
          去团队配置
        </router-link>
        <span v-else class="o-text-3" style="font-size: 12.5px">请联系团队管理员配置大模型</span>
      </div>
      <div class="chat-scroll">
        <div ref="bodyRef" class="chat-body" @scroll.passive="onBodyScroll">
        <div class="chat-list">
          <div v-if="loadingSession" class="chat-empty"><p>正在加载会话…</p></div>

          <template v-else>
          <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
            <div class="avatar" :class="`avatar--${m.role}`">{{ m.role === 'user' ? '我' : 'AI' }}</div>
            <div class="msg-main">
              <div class="bubble">
                <span
                  v-if="running && m.role === 'assistant' && m.id === 'live'"
                  class="pill pill--run msg-live"
                  >生成中</span>

                <div v-if="fileBlocks(m).length" class="msg-files">
                  <span v-for="(b, i) in fileBlocks(m)" :key="i" class="msg-file o-mono">
                    <Icon name="file" :size="12" />
                    {{ b.name }}
                  </span>
                </div>

                <AssistantTurn
                  v-if="m.role === 'assistant'"
                  :message="m"
                  :running="running && m.id === 'live'"
                />
                <template v-else>
                  <p v-for="(b, i) in textBlocks(m)" :key="i" class="blk-text">{{ b.text }}</p>
                </template>
              </div>

              <div class="msg-actions">
                <button
                  v-if="textBlocks(m).length"
                  type="button"
                  class="copy-btn"
                  :class="{ 'is-ok': copiedId === m.id }"
                  :data-tip="copiedId === m.id ? '已复制' : '复制'"
                  aria-label="复制"
                  @click="copyMessage(m)"
                >
                  <Icon :name="copiedId === m.id ? 'check' : 'clipboard'" :size="13" />
                </button>
                <button
                  type="button"
                  class="copy-btn copy-btn--del"
                  :data-tip="m.id === 'live' ? '停止并删除' : '删除'"
                  :aria-label="m.id === 'live' ? '停止并删除' : '删除'"
                  :disabled="removingId === m.id"
                  @click="removeMessage(m)"
                >
                  <Icon name="trash" :size="13" />
                </button>
              </div>
            </div>
          </div>

          <div v-if="!messages.length && !running" class="welcome">
            <div class="welcome-ico"><Icon name="activity" :size="34" /></div>
            <h2>{{ ready || !modelEmpty ? '从一个故障现象开始' : '先由团队管理员配置大模型' }}</h2>
            <p v-if="ready">说清时间、主机和现象；勾选左侧日志，助手会先取证再给结论。</p>
            <p v-else-if="modelEmpty">
              团队还没有启用模型。模型由团队统一配置，成员在输入框里直接选择即可。
            </p>
            <p v-else>{{ status?.reason || '当前模型尚不可用，请在输入框切换模型或联系团队管理员。' }}</p>
            <div v-if="!ready && modelEmpty && canConfigureModel" class="welcome-cta">
              <router-link
                class="o-btn o-btn--primary"
                :to="status?.teamId ? `/teams/${status.teamId}?tab=llm` : '/teams'"
              >
                {{ status?.teamId ? '去团队配置大模型' : '去团队页' }}
              </router-link>
            </div>
            <div class="example-grid">
              <button
                v-for="e in examples"
                :key="e.prompt"
                class="example-card"
                @click="useExample(e.prompt)"
              >
                <b>{{ e.title }}</b>
                <span>{{ e.desc }}</span>
              </button>
            </div>
            <p v-if="!fileNodes.length" class="welcome-note">
              还没有日志？<router-link to="/teams">去创建解析任务</router-link>
            </p>
          </div>
        </template>
        </div>
      </div>
      <!-- 离开底部时浮出的快速回底按钮 -->
      <button v-if="!atBottom" type="button" class="jump-bottom" title="回到底部" @click="jumpBottom">
        <Icon name="arrow-down" :size="16" />
      </button>
      </div>

      <div
        class="composer"
        :class="{ 'is-drop': dropActive }"
        @dragenter.prevent="onDragEnter"
        @dragover.prevent
        @dragleave="onDragLeave"
        @drop.prevent="onDrop"
      >
        <div class="composer-inner">
          <div v-if="dropActive" class="drop-mask">松开鼠标，把文件加入本次提问</div>
          <ComposerTaskDock
            :tools="runTools"
            :running="running"
            :queue="queue"
            @remove-queued="removeQueued"
            @update-queued="updateQueued"
          />
          <div v-if="attached.length" class="ctx-chips">
            <span class="ctx-label">已选 {{ attached.length }} 份日志</span>
            <span v-for="f in attached" :key="f.id" class="ctx-chip" :title="f.path">
              <span class="ctx-name">{{ f.name }}</span>
              <button class="ctx-x" title="取消勾选" @click="detachFile(f.id)">×</button>
            </span>
            <button class="o-btn o-btn--text ctx-clear" @click="clearAttached">清空</button>
          </div>
          <textarea
            v-model="draft"
            class="o-textarea composer-text"
            rows="3"
            :placeholder="
              attached.length
                ? '描述故障现象，助手会直接读取已选日志…'
                : '描述故障现象，或直接粘贴日志片段…（Enter 发送 / Shift+Enter 换行）'
            "
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <div class="composer-bar">
            <div class="composer-left">
              <ComposerModelMenu
                :catalog="catalog"
                :status="composerStatus"
                :disabled="running"
                @change="onModelChange"
              />
              <ComposerKbMenu
                :team-id="sessionTeamId"
                :selected-ids="sessionKbIds"
                :top-k="sessionKbTopK"
                :can-search="canKbSearch"
                :disabled="running"
                @change="onKbChange"
              />
              <span class="composer-hint o-text-3">
                {{
                  !ready
                    ? (status?.reason || '模型不可用')
                    : attached.length
                      ? `将结合 ${attached.length} 份日志分析`
                      : '勾选左侧日志可让助手直接读取原文'
                }}
              </span>
            </div>
            <div class="o-row" style="gap: 8px">
              <button v-if="running" class="o-btn" @click="stop">停止</button>
              <button
                v-if="running"
                class="o-btn o-btn--primary"
                :disabled="!draft.trim() || !activeSessionId || !ready"
                title="加入排队，当前轮结束后自动发送"
                @click="send"
              >
                排队发送
              </button>
              <button
                v-else
                class="o-btn o-btn--primary"
                :disabled="!draft.trim() || !activeSessionId || !ready"
                @click="send"
              >
                开始诊断
              </button>
            </div>
          </div>
          <ErrorAlert v-if="errorText" :text="errorText" style="margin-top: 8px" @close="errorText = ''" />
        </div>
      </div>
    </main>

    <!-- ============ 右: 按需展开的日志原文 ============ -->
    <div
      v-if="drawerVisible"
      class="resizer resizer--drawer"
      title="拖动调整日志面板宽度"
      @mousedown="startDrag('drawer', $event)"
    ></div>
    <aside v-if="drawerVisible" class="drawer" :style="{ width: drawerW + 'px' }">
      <div class="drawer-head">
        <b>日志原文</b>
        <span class="o-text-3">只读</span>
        <span class="spacer"></span>
        <button class="icon-btn" title="关闭日志原文" @click="closeDrawer"><Icon name="close" :size="13" /></button>
      </div>
      <DiagnosisFileReader
        :tabs="tabs"
        :active-id="activeTabId"
        @activate="activeTabId = $event"
        @close="closeTab"
        @attach="attachFromReader"
      />
    </aside>
  </div>
</template>

<style scoped>
.diag {
  height: calc(100vh - var(--topbar-h));
  min-height: 380px;
  display: flex;
  background: var(--surface);
  overflow: hidden;
}

.icon-btn {
  flex: none;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.icon-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.icon-btn.is-on {
  border-color: transparent;
  background: var(--primary-soft);
  color: var(--primary);
}

/* ---------- 侧栏拖拽分隔线 ---------- */
.resizer {
  flex: none;
  width: 5px;
  position: relative;
  cursor: col-resize;
  background: var(--surface-3);
}
.resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  width: 1px;
  background: var(--border);
  transition: background 0.12s;
}
.resizer:hover::after {
  background: var(--primary);
}

/* ---------- 侧栏分区 ---------- */
.sidebar {
  flex: 0 0 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--surface-3);
  overflow: hidden;
}
.side-sec {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--border);
  overflow: hidden;
}
.side-sec.is-collapsed {
  flex: 0 0 auto;
}
/* 文件树容器: 折叠时仅隐藏不卸载, 保留目录 / 展开态 / 勾选 */
.tree-wrap {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.side-sec--history {
  flex: 0 1 auto;
  max-height: 38%;
}
.side-sec-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 7px;
  height: 38px;
  padding: 0 8px 0 10px;
  cursor: pointer;
  user-select: none;
}
.side-sec-head:hover {
  background: var(--surface);
}
.side-sec-head :deep(.o-fold) {
  width: 20px;
  height: 20px;
}
.side-sec-head :deep(.o-fold svg) {
  width: 11px;
  height: 11px;
}
.side-sec-title {
  flex: none;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
}
.side-count {
  flex: none;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--surface);
  color: var(--text-3);
  font-size: 11px;
  line-height: 17px;
}
.side-state {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
  color: var(--text-4);
}
.side-tokens {
  flex: none;
  font-size: 11px;
  color: var(--text-4);
}
.side-sec-head .icon-btn {
  width: 26px;
  height: 26px;
  border: none;
  background: transparent;
  opacity: 0.75;
}
.side-sec-head .icon-btn:hover {
  background: var(--surface-3);
  opacity: 1;
}

.chat-tabs-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.tab-strip {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
}
.chat-tab {
  flex: none;
  max-width: 170px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 6px 0 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  transition: all 0.15s;
}
.chat-tab:hover {
  border-color: var(--primary-tint);
  color: var(--primary);
}
.chat-tab.is-active {
  background: var(--primary-soft);
  border-color: var(--primary);
  color: var(--primary);
}
.chat-tab-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-tab-x {
  width: 14px;
  height: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 9px;
  color: var(--text-4);
  opacity: 0;
  transition: all 0.15s;
}
.chat-tab:hover .chat-tab-x,
.chat-tab.is-active .chat-tab-x {
  opacity: 1;
}
.chat-tab-x:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.chat-tab-add {
  flex: none;
  width: 26px;
  height: 26px;
  padding: 0;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
}
.chat-tab-add:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.chat-fold {
  margin-left: 0;
}
.chat-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  min-width: 0;
}
.chat-spacer {
  flex: 1;
}
.chat-model {
  font-size: 11px;
}
.chat-session {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
}
.pill {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}
.pill--ok {
  background: var(--success-soft);
  color: var(--success);
}
.pill--run {
  background: var(--warning-soft);
  color: var(--warning);
}
.pill--warn {
  background: var(--danger-soft);
  color: var(--danger);
}
.notice-bar {
  flex: none;
  padding: 7px 12px;
  background: var(--warning-soft);
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
  border-bottom: 1px solid var(--border);
}

/* ---------- 消息 ---------- */
.chat-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  background: var(--surface);
}
.msg {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}
.msg--user {
  flex-direction: row-reverse;
}
.msg-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: 86%;
}
.msg--assistant .msg-main {
  max-width: 94%;
}
.msg--user .msg-main {
  max-width: 78%;
}
.avatar {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
}
.avatar--assistant {
  background: linear-gradient(135deg, #1e6fff, #6f4dff);
}
.avatar--user {
  background: hsl(212, 60%, 48%);
}
.bubble {
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 10px 12px;
  min-width: 0;
}
.msg--user .bubble {
  background: var(--primary-soft);
  border-color: var(--primary-tint);
}
.msg-live {
  margin-bottom: 6px;
}
.msg-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.msg-file {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2px 8px;
  font-size: 11px;
  color: var(--text-2);
}
/* 悬停消息时在气泡下方浮出的操作栏 */
.msg-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  max-height: 0;
  opacity: 0;
  overflow: hidden;
  transition: max-height 0.16s ease, margin-top 0.16s ease, opacity 0.16s ease;
}
.msg:hover .msg-actions {
  max-height: 30px;
  margin-top: 4px;
  opacity: 1;
  /* 悬停时放出图标的文字提示, 避免被 max-height 裁切 */
  overflow: visible;
}
.copy-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-4);
  cursor: pointer;
  transition: all 0.15s;
}
.copy-btn::after {
  content: attr(data-tip);
  position: absolute;
  top: calc(100% + 5px);
  left: 50%;
  padding: 2px 6px;
  border-radius: var(--radius);
  background: var(--text-1);
  color: #fff;
  font-size: 11px;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transform: translateX(-50%) translateY(-2px);
  transition: opacity 0.12s, transform 0.12s;
  z-index: 20;
}
.copy-btn:hover::after {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
.copy-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.copy-btn--del:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}
.copy-btn--del:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.copy-btn.is-ok {
  border-color: var(--success);
  color: var(--success);
}
.blk-text {
  font-size: 13.5px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
.blk-text + .blk-text {
  margin-top: 8px;
}
.blk-thought {
  margin: 6px 0;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: 6px 9px;
  background: var(--surface-3);
  font-size: 12px;
  color: var(--text-3);
}
.blk-thought summary {
  cursor: pointer;
  color: var(--text-3);
}
.blk-thought p {
  margin-top: 6px;
  line-height: 1.7;
  white-space: pre-wrap;
}
.blk-tool {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
  padding: 7px 9px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  border-left: 2px solid var(--primary);
  border-radius: var(--radius);
  font-size: 12px;
}
.tool-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--success);
}
.tool-dot.is-run {
  background: var(--warning);
  animation: pulse 1s ease-in-out infinite;
}
.tool-dot.is-fail {
  background: var(--danger);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.tool-name {
  font-weight: 600;
  color: var(--text-1);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.tool-args {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
}
.tool-state {
  flex: none;
  font-size: 11px;
}
.tool-detail {
  flex-basis: 100%;
  font-size: 11.5px;
  color: var(--text-2);
}
.tool-detail summary {
  cursor: pointer;
  color: var(--text-3);
}
.tool-detail pre {
  margin: 6px 0 0;
  padding: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.blk-error {
  margin-top: 6px;
  padding: 6px 8px;
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--radius);
  font-size: 12px;
  line-height: 1.6;
}
.typing {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-2);
}
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  animation: bounce 1.2s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: 0.15s; }
.typing span:nth-child(3) { animation-delay: 0.3s; }
@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
  30% { transform: translateY(-4px); opacity: 1; }
}
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
  color: var(--text-3);
  padding: 24px 16px;
}
.chat-empty-ico {
  font-size: 30px;
}
.chat-empty p {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-2);
}
.chat-empty span {
  font-size: 12px;
  line-height: 1.7;
  max-width: 300px;
}
.examples {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
  width: 100%;
}
.example {
  border: 1px solid var(--border);
  background: var(--surface-3);
  color: var(--text-2);
  border-radius: var(--radius);
  padding: 7px 10px;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  line-height: 1.5;
}
.example:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-soft);
}

/* ---------- 输入区 ---------- */
.composer {
  flex: none;
  position: relative;
  border-top: 1px solid var(--border);
  padding: 10px 12px 12px;
  background: var(--surface);
}
.composer.is-drop {
  background: var(--primary-soft);
}
.drop-mask {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 13px;
  font-weight: 600;
  pointer-events: none;
}
.ctx-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.ctx-label {
  font-size: 12px;
  color: var(--text-3);
}
.ctx-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 180px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
  padding: 2px 4px 2px 8px;
  font-size: 11.5px;
  white-space: nowrap;
}
.ctx-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 跟着文件名尾巴: 极简方块, 悬停才显出底色 */
.ctx-x {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: var(--primary);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  opacity: 0.6;
  transition: opacity 0.15s, background 0.15s;
}
.ctx-x:hover {
  opacity: 1;
  background: rgba(15, 23, 42, 0.07);
}
.ctx-clear {
  margin-left: auto;
}
.composer-text {
  min-height: 62px;
  font-family: var(--font);
  font-size: 13px;
}
.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 8px;
}
.composer-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.composer-hint {
  font-size: 11.5px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

<style scoped>
/* ---------- 对话优先布局 ---------- */
.status-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-4);
}
.status-dot.is-ok {
  background: var(--success);
}
.status-dot.is-warn {
  background: var(--warning);
}
.status-dot.is-run {
  background: var(--warning);
  animation: pulse 1s ease-in-out infinite;
}

/* 文件树数据源切换 */
.src-switch {
  flex: none;
  display: inline-flex;
  padding: 1px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-3);
}
.src-btn {
  border: none;
  background: transparent;
  color: var(--text-3);
  font: inherit;
  font-size: 11px;
  line-height: 1.7;
  padding: 1px 6px;
  border-radius: calc(var(--radius) - 1px);
  cursor: pointer;
  white-space: nowrap;
}
.src-btn:hover {
  color: var(--primary);
}
.src-btn.is-on {
  background: var(--surface);
  color: var(--primary);
  font-weight: 600;
}
.spacer {
  flex: 1;
  min-width: 0;
}
.side-empty {
  padding: 22px 16px;
  text-align: center;
}
.side-empty-t {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 6px;
}
.side-empty-s {
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-3);
  margin-bottom: 14px;
}

.chat {
  flex: 1;
  min-width: 420px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
  container-type: inline-size;
}
.chat-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: relative;
  z-index: 20;
}
.head-left,
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.head-left {
  flex: 1;
  overflow: hidden;
}
.head-actions {
  flex: none;
}
.session-title-text {
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
.model-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  color: var(--text-2);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.model-chip:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.model-chip.is-warn {
  border-color: var(--warning);
  background: var(--warning-soft);
  color: var(--warning-deep);
}
.model-chip.is-warn:hover {
  border-color: var(--warning-deep);
  color: var(--warning-deep);
}
.model-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
}
.model-dot.is-warn {
  background: var(--warning);
}
.model-gear {
  color: var(--text-4);
  font-size: 11px;
}
.log-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.log-ico {
  font-size: 11px;
}
.log-count {
  color: var(--text-3);
}
@container (max-width: 660px) {
  .chat-head .pill,
  .model-name:not(.is-cta),
  .log-label {
    display: none;
  }
  .chat-head {
    padding-left: 12px;
    padding-right: 12px;
  }
  .session-title-text {
    max-width: 220px;
  }
}
.notice {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 18px;
  background: var(--warning-soft);
  border-bottom: 1px solid var(--border);
  font-size: 12.5px;
  color: var(--text-2);
}
.notice-text {
  flex: 1;
  min-width: 0;
}

/* ---------- 对话主体 ---------- */
.chat-scroll {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}
.chat-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0;
  display: block;
}
.jump-bottom {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: var(--surface);
  color: var(--text-2);
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.16);
  cursor: pointer;
  z-index: 4;
  transition: color 0.15s, border-color 0.15s;
}
.jump-bottom:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.chat-list {
  width: 100%;
  /* 底部留白: 容纳悬浮的回底按钮, 避免压住最后一条消息 */
  padding: 14px 18px 56px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}
.welcome {
  margin: auto;
  width: 100%;
  max-width: 760px;
  padding: 10px;
  text-align: center;
}
.welcome-ico {
  font-size: 38px;
}
.welcome h2 {
  margin-top: 8px;
  font-size: 20px;
  color: var(--text-1);
}
.welcome > p {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--text-3);
}
.welcome-cta {
  margin-top: 14px;
}
.example-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 22px;
}
.example-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface-3);
  color: var(--text-2);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.example-card:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
}
.example-card b {
  font-size: 13px;
  color: var(--text-1);
}
.example-card span {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-3);
}
.welcome-note {
  margin-top: 16px;
  font-size: 12px;
  color: var(--text-4);
}
.welcome-note a {
  color: var(--primary);
  cursor: pointer;
}

/* ---------- 输入区 ---------- */
.composer {
  padding: 0;
}
.composer-inner {
  position: relative;
  width: 100%;
  padding: 10px 18px 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.composer-text {
  min-height: 76px;
}

/* ---------- 日志原文抽屉 ---------- */
.resizer--drawer {
  background: var(--surface);
}
.resizer--drawer::after {
  left: auto;
  right: 0;
}
.drawer {
  flex: 0 1 auto;
  min-width: 300px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface);
}
.drawer-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-3);
}
.drawer-head b {
  font-size: 13px;
}
.drawer-head .icon-btn {
  margin-left: auto;
}
.drawer :deep(.rd) {
  flex: 1;
  min-height: 0;
  height: auto;
}

@media (max-width: 1100px) {
  .example-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 1280px) {
  .diag {
    position: relative;
  }
  .drawer {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 60;
    max-width: 80%;
    border-radius: 0;
    box-shadow: -14px 0 32px rgba(15, 23, 42, 0.16);
  }
  .resizer--drawer {
    display: none;
  }
}
</style>
