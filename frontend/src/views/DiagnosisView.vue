<script setup lang="ts">
/* 智能诊断: 对话优先
   左: 资料栏(日志树 + 运行状态)   中: 诊断对话   右: 按需展开的日志原文 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import DiagnosisFileTree from '@/components/DiagnosisFileTree.vue'
import DiagnosisFileReader from '@/components/DiagnosisFileReader.vue'
import DiagnosisInspector from '@/components/DiagnosisInspector.vue'
import ComposerModelMenu from '@/components/diagnosis/ComposerModelMenu.vue'
import ComposerTaskDock from '@/components/diagnosis/ComposerTaskDock.vue'
import AssistantTurn from '@/components/diagnosis/AssistantTurn.vue'
import FoldChevron from '@/components/diagnosis/FoldChevron.vue'
import SessionHistoryDialog from '@/components/diagnosis/SessionHistoryDialog.vue'
import Icon from '@/components/Icon.vue'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import type { Attach, ReaderTab } from '@/components/diagnosisFiles'
import { apiError } from '@/api/http'
import {
  agentStatus,
  createSession,
  fetchCatalog,
  listFiles,
  listMessages,
  listSessions,
  readFile as fetchFileContent,
  saveTeamSelection,
  sendMessage,
  type AgentStatus,
  type CatalogTeam,
  type ChatMessage,
  type FileNode,
  type MessageBlock,
  type PlanBlock,
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
const drawerW = ref(560)
const drawerVisible = ref(false)

type DragSide = 'left' | 'drawer'
let dragging: DragSide | null = null
let dragStartX = 0
let dragStartW = 0
let dragged = false

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function onMove(e: MouseEvent) {
  if (!dragging) return
  const dx = e.clientX - dragStartX
  if (!dragged && Math.abs(dx) < 4) return
  dragged = true
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
  dragged = false
  if (side === 'left' && leftCollapsed.value) return
  dragging = side
  dragStartX = e.clientX
  dragStartW = side === 'left' ? leftW.value : drawerW.value
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', stopDrag)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
function toggleLeft() {
  if (dragged) return
  leftCollapsed.value = !leftCollapsed.value
}

/* ---------------- 运行状态 / 团队模型 ---------------- */
const inspectorOpen = ref(false)
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
const treeRef = ref<InstanceType<typeof DiagnosisFileTree> | null>(null)

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

function onTreeSelect(files: Attach[]) {
  attached.value = files
}
function detachFile(id: string) {
  attached.value = attached.value.filter((a) => a.id !== id)
  treeRef.value?.deselect(id)
}
function clearAttached() {
  attached.value = []
  treeRef.value?.clear()
}

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

async function openFile(f: Attach) {
  drawerVisible.value = true
  const cached = fileCache.get(f.id)
  if (cached) {
    if (!tabs.value.some((t) => t.id === f.id)) tabs.value.push(cached)
    activeTabId.value = f.id
    return
  }
  try {
    const payload = await fetchFileContent(f.id)
    const tab: ReaderTab = {
      ...f,
      size: fmtSize(payload.size) || f.size,
      kind: payload.kind,
      content: payload.content,
      original: payload.content,
      lang: payload.lang,
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
const historyOpen = ref(false)

const currentSession = computed(() => sessions.value.find((s) => s.id === activeSessionId.value))
const currentTitle = computed(() => currentSession.value?.title || '新的诊断')
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
}

async function loadMessageList(id: string) {
  loadingSession.value = true
  pickedModel.value = null
  try {
    messages.value = await listMessages(id)
    resetRun()
    await scrollBottom()
  } catch (e) {
    errorText.value = apiError(e)
  } finally {
    loadingSession.value = false
  }
}

async function selectSession(id: string) {
  if (id === activeSessionId.value || running.value) return
  activeSessionId.value = id
  const sess = sessions.value.find((s) => s.id === id)
  if (sess?.teamId) void refreshStatus(sess.teamId)
  await loadMessageList(id)
}

/* 历史会话弹窗: 继续任意可见会话 */
function openHistorySession(s: SessionInfo) {
  if (running.value) return
  historyOpen.value = false
  void selectSession(s.id)
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
  if (running.value) return
  try {
    const s = await createSession('', status.value?.teamId || undefined)
    sessions.value.unshift(s)
    activeSessionId.value = s.id
    messages.value = []
    pickedModel.value = null
    resetRun()
  } catch (e) {
    errorText.value = apiError(e)
  }
}

/* ---------------- 一轮诊断 ---------------- */
const draft = ref('')
const running = ref(false)
const phase = ref('')
const abortCtl = ref<AbortController | null>(null)
const runPlan = ref<PlanBlock['entries']>([])
const runTools = ref<ToolBlock[]>([])
const streamUsage = reactive({ input: 0, output: 0, total: 0 })

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
  return {
    input: input + streamUsage.input,
    output: output + streamUsage.output,
    total: total + streamUsage.total,
  }
})

const modelInfo = computed(() => ({
  model: modelLabel.value,
  permission: permissionLabel.value,
  skills: status.value?.skills.length ?? 0,
  ...usageTotals.value,
}))

const todos = computed(() =>
  runPlan.value.map((e) => ({
    text: e.content,
    status: e.status === 'completed' ? ('done' as const) : e.status === 'in_progress' ? ('doing' as const) : ('todo' as const),
  })),
)

const inspectorTools = computed(() =>
  runTools.value.map((t) => ({
    name: t.title || t.name || '工具',
    target: (t.input || '').replace(/\s+/g, ' ').slice(0, 80),
    status: t.status,
  })),
)

const todoDone = computed(() => todos.value.filter((t) => t.status === 'done').length)

const runPlanBlock = computed<PlanBlock | null>(() =>
  runPlan.value.length ? { kind: 'plan', entries: runPlan.value } : null,
)

function numOf(obj: Record<string, unknown>, keys: string[]): number {
  for (const k of keys) {
    const v = obj[k]
    if (typeof v === 'number') return v
    if (typeof v === 'string' && v && !Number.isNaN(Number(v))) return Number(v)
  }
  return 0
}

function resetRun() {
  runPlan.value = []
  runTools.value = []
  streamUsage.input = 0
  streamUsage.output = 0
  streamUsage.total = 0
  phase.value = ''
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

function upsertTool(msg: ChatMessage, data: RawToolEvent) {
  const statusValue: ToolBlock['status'] =
    data.status === 'completed' || data.status === 'done'
      ? 'done'
      : data.status === 'failed'
        ? 'failed'
        : 'running'
  const block = msg.blocks.find((b): b is ToolBlock => b.kind === 'tool' && b.id === data.id)
  const normalized: ToolBlock = {
    kind: 'tool',
    id: data.id,
    name: data.name || data.title || '工具',
    title: data.title || '',
    toolKind: data.toolKind,
    status: statusValue,
    input: data.input || '',
    output: data.output || '',
  }
  if (block) {
    Object.assign(block, normalized)
  } else {
    msg.blocks.push(normalized)
  }
  const row = runTools.value.find((t) => t.id === data.id)
  if (row) Object.assign(row, normalized)
  else runTools.value.push(normalized)
}

function addUsage(raw: unknown) {
  if (!raw || typeof raw !== 'object') return
  const u = raw as Record<string, unknown>
  streamUsage.input += numOf(u, ['inputTokens', 'input_tokens', 'promptTokens', 'prompt_tokens'])
  streamUsage.output += numOf(u, ['outputTokens', 'output_tokens', 'completionTokens', 'completion_tokens'])
  streamUsage.total += numOf(u, ['totalTokens', 'total_tokens'])
}

function mergeUsage(msg: ChatMessage, raw: unknown) {
  if (!raw || typeof raw !== 'object') return
  const u = raw as Record<string, unknown>
  msg.usage = { ...(msg.usage ?? {}), ...u }
  // 会话级统计已包含 live 用量, 这里清零避免重复
  streamUsage.input = 0
  streamUsage.output = 0
  streamUsage.total = 0
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

async function send() {
  const text = draft.value.trim()
  if (!text || running.value) return
  if (!ready.value) {
    errorText.value = status.value?.reason || '请先在团队「大模型」中启用模型'
    return
  }
  const sid = activeSessionId.value
  if (!sid) return

  const refs = attached.value.map((f) => {
    const [taskId] = f.id.split('|')
    return { taskId, name: f.id.split('|').slice(1).join('|') || f.name }
  })

  messages.value.push({
    id: `u-${Date.now()}`,
    role: 'user',
    blocks: [
      { kind: 'text', text },
      ...attached.value.map((f) => ({ kind: 'file' as const, name: f.name, taskId: f.id.split('|')[0] })),
    ],
  })
  const live: ChatMessage = reactive({ id: 'live', role: 'assistant', blocks: [] })
  messages.value.push(live)

  draft.value = ''
  resetRun()
  errorText.value = ''
  running.value = true
  phase.value = 'preparing'
  await scrollBottom()

  const ctl = new AbortController()
  abortCtl.value = ctl
  let stopped = false

  try {
    await sendMessage(sid, text, refs, {
      onEvent(event, data) {
        if (event === 'phase') {
          phase.value = data.phase ?? ''
        } else if (event === 'text') {
          appendText(live, 'text', data.text ?? '')
        } else if (event === 'thought') {
          appendText(live, 'thought', data.text ?? '')
        } else if (event === 'tool') {
          upsertTool(live, data as RawToolEvent)
        } else if (event === 'plan') {
          runPlan.value = data.entries ?? []
          const block = live.blocks.find((b): b is PlanBlock => b.kind === 'plan')
          if (block) block.entries = runPlan.value
          else live.blocks.push({ kind: 'plan', entries: runPlan.value })
        } else if (event === 'usage') {
          addUsage(data.usage)
        } else if (event === 'error') {
          live.error = data.message ?? '诊断失败'
        } else if (event === 'done') {
          if (data.messageId) live.id = data.messageId
          mergeUsage(live, data.usage)
        }
        void scrollBottom()
      },
    }, ctl.signal, pickedModel.value ?? {})
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      stopped = true
      live.error = '已停止本轮诊断'
    } else {
      const msg = apiError(e)
      if (!live.error) {
        live.error = msg
        errorText.value = msg
      }
    }
  } finally {
    running.value = false
    phase.value = ''
    abortCtl.value = null
    if (!live.blocks.length && !live.error) {
      live.error = stopped ? '已停止本轮诊断' : '智能体没有返回内容'
    }
    // 刷新会话标题与排序
    try {
      sessions.value = await listSessions()
    } catch {
      /* 忽略 */
    }
    await scrollBottom()
  }
}

function stop() {
  abortCtl.value?.abort()
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
    const first = sessions.value.find((s) => s.userId === rbac.currentUserId) ?? sessions.value[0]
    activeSessionId.value = first.id
    if (first.teamId) void refreshStatus(first.teamId)
    await loadMessageList(activeSessionId.value)
  } catch (e) {
    errorText.value = apiError(e)
  }
})

onBeforeUnmount(() => {
  stopDrag()
  abortCtl.value?.abort()
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
      <DiagnosisFileTree
        ref="treeRef"
        :nodes="fileNodes"
        :loading="filesLoading"
        :active-id="activeTabId"
        @open="openFile"
        @select="onTreeSelect"
      >
        <template #actions>
          <button class="icon-btn" title="刷新文件列表" @click="loadFiles">
            <Icon name="refresh" :size="13" />
          </button>
        </template>
        <template #empty>
          <div class="side-empty">
            <p class="side-empty-t">还没有可分析的日志</p>
            <p class="side-empty-s">上传日志后，勾选即可让助手直接读取原文。</p>
            <router-link class="o-btn o-btn--sm" to="/teams">去上传日志</router-link>
          </div>
        </template>
      </DiagnosisFileTree>

      <div class="side-foot">
        <button
          class="side-status"
          :title="ready ? '查看运行状态' : (status?.reason || '模型不可用')"
          @click="ready ? (inspectorOpen = !inspectorOpen) : refreshStatus()"
        >
          <span class="status-dot" :class="running ? 'is-run' : ready ? 'is-ok' : 'is-warn'"></span>
          <span class="side-status-text">{{
            running ? phaseText : ready ? '就绪' : (status?.reason || '模型不可用')
          }}</span>
          <span class="spacer"></span>
          <span v-if="ready" class="o-mono side-tokens">{{ usageTotals.total.toLocaleString('en-US') }} tokens</span>
          <FoldChevron v-if="ready" :open="inspectorOpen" />
        </button>
        <div v-if="inspectorOpen" class="inspector">
          <DiagnosisInspector
            :model-info="modelInfo"
            :todos="todos"
            :tools="inspectorTools"
          />
        </div>
      </div>
    </aside>
    <button
      class="rail rail--left"
      :class="{ 'is-collapsed': leftCollapsed }"
      :title="leftCollapsed ? '展开资料栏' : '收起资料栏 · 沿此栏左右拖动可调整宽度'"
      @mousedown="startDrag('left', $event)"
      @click="toggleLeft"
    >
      <span class="rail-btn">
        <Icon :name="leftCollapsed ? 'chevron-right' : 'chevron-left'" :size="10" />
      </span>
    </button>

    <!-- ============ 中: 诊断对话 ============ -->
    <main class="chat">
      <header class="chat-head">
        <div class="head-left">
          <button class="icon-btn" title="历史会话" @click="historyOpen = true">
            <Icon name="history" :size="14" />
          </button>
          <span class="session-title-text" :title="currentTitle">{{ currentTitle }}</span>
          <span
            v-if="running || ready"
            class="pill"
            :class="running ? 'pill--run' : 'pill--ok'"
          >
            {{ running ? '诊断中' : '就绪' }}
          </span>
        </div>
        <div class="head-actions">
          <button v-if="tabs.length" class="o-btn o-btn--sm log-btn" @click="reopenDrawer">
            <Icon name="file" :size="13" />
            <span class="log-label">日志原文</span>
            <span class="log-count">({{ tabs.length }})</span>
          </button>
          <button class="icon-btn" title="新建会话" @click="addSession">
            <Icon name="plus" :size="13" />
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
      <div ref="bodyRef" class="chat-body">
        <div class="chat-list">
          <div v-if="loadingSession" class="chat-empty"><p>正在加载会话…</p></div>

          <template v-else>
          <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
            <div class="avatar" :class="`avatar--${m.role}`">{{ m.role === 'user' ? '我' : 'AI' }}</div>
            <div class="bubble">
              <div class="msg-head">
                <b>{{ m.role === 'user' ? '我' : '诊断助手' }}</b>
                <span v-if="running && m.role === 'assistant' && m.id === 'live'" class="pill pill--run">生成中</span>
              </div>

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

      <div
        class="composer"
        :class="{ 'is-drop': dropActive }"
        @dragenter.prevent="onDragEnter"
        @dragover.prevent
        @dragleave="onDragLeave"
        @drop.prevent="onDrop"
      >
        <div v-if="dropActive" class="drop-mask">松开鼠标，将文件加入本次提问上下文</div>
        <div v-if="attached.length" class="ctx">
          <div class="ctx-head">
            <span class="ctx-label">上下文 {{ attached.length }}</span>
            <button class="o-btn o-btn--text ctx-clear" @click="attached = []">清空</button>
          </div>
          <div class="ctx-chips">
            <span v-for="f in attached" :key="f.id" class="ctx-chip o-mono" :title="f.path">
              {{ f.name }}
              <button class="ctx-x" title="移除" @click="detachFile(f.id)">×</button>
            </span>
          </div>
        </div>
        <textarea
          v-model="draft"
          class="o-textarea composer-text"
          rows="3"
          placeholder="描述你的诊断问题，或直接粘贴日志片段…（Enter 发送 / Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <div class="composer-bar">
          <div class="o-row" style="gap: 8px">
            <label class="pick">
              <span class="pick-label">模式</span>
              <select v-model="agentMode" class="o-select pick-select">
                <option v-for="a in AGENT_MODES" :key="a.key" :value="a.key">{{ a.label }}</option>
              </select>
            </label>
            <label class="pick">
              <span class="pick-label">模型</span>
              <select v-model="model" class="o-select pick-select">
                <option v-for="mm in MODELS" :key="mm.key" :value="mm.key">{{ mm.label }}</option>
              </select>
            </label>
          </div>
          <button class="o-btn o-btn--primary" :disabled="!draft.trim() || running" @click="send">
            {{ running ? '分析中…' : '发送 ➤' }}
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.diag {
  height: calc(100vh - 142px);
  min-height: 520px;
  display: flex;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
  overflow: hidden;
}

/* ---------- 通用 pane ---------- */
.pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
}
.pane--left,
.pane--right {
  /* 宽度由内联样式给出(已按容器动态收敛); 三栏同属一个 flex 容器,
     左右栏变化时由主体栏吸收, 故正常情况下无需压缩 */
  flex: 0 1 auto;
  min-width: 140px;
  background: var(--surface-3);
}
.pane--center {
  flex: 1 1 0;         /* 占满左右栏之外的剩余宽度: 左右栏变宽则主体栏变窄 */
  min-width: 240px;    /* 与 CENTER_MIN 一致: 主体栏保底宽度 */
  background: var(--surface);
}
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  flex: none;
}

/* ---------- 左·底端运行状态停靠面板 ---------- */
.pane-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.insp-dock {
  flex: 0 0 46%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-top: 1px solid var(--border);
  background: var(--surface-3);
}
.insp-dock.is-collapsed {
  flex: 0 0 auto;
}
.insp-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--border);
}
.insp-dock.is-collapsed .insp-head {
  border-bottom: none;
}
.insp-head .pane-title {
  flex: none;
}
.insp-mini {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  text-align: right;
}
.insp-head .insp-toggle {
  margin-left: auto;
}

/* ---------- 右上角图标按钮 ---------- */
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
/* ---------- 侧栏竖栏: 单击收起/展开, 沿栏左右拖动调宽 ---------- */
.rail {
  flex: none;
  width: 26px;
  padding: 0;
  border: none;
  cursor: col-resize;
  background: var(--surface-3);
  color: var(--text-3);
  font-size: 14px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.rail.is-collapsed {
  cursor: pointer;
}
.rail--left {
  border-right: 1px solid var(--border);
}
.rail--right {
  border-left: 1px solid var(--border);
}
.rail:hover {
  background: var(--primary-soft);
  color: var(--primary);
}

/* ---------- 右·会话页签 ---------- */
.chat-tabs {
  gap: 10px;
}
.tab-strip {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
}
.chat-tab {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  min-width: 30px;
  padding: 0 6px 0 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
  font: inherit;
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
.chat-tab-no {
  font-size: 12.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
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
  pointer-events: none;
  transition: all 0.15s;
}
.chat-tab:hover .chat-tab-x,
.chat-tab.is-active .chat-tab-x {
  opacity: 1;
  pointer-events: auto;
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
  transition: all 0.15s;
}
.chat-tab-add:hover {
  border-color: var(--primary);
  color: var(--primary);
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
  padding: 24px 20px;
}
.chat-empty-ico {
  font-size: 30px;
}
.chat-empty p {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-2);
}
.chat-empty span {
  font-size: 12px;
  line-height: 1.7;
  max-width: 260px;
}

/* ---------- 右·消息 ---------- */
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
  max-width: 84%;
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
.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.msg-head b {
  font-size: 12px;
}
.msg-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.msg-file {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2px 8px;
  font-size: 11px;
  color: var(--text-2);
}
.blk-text {
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.blk-text + .blk-text {
  margin-top: 8px;
}
.blk-tool {
  position: relative;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
  padding: 7px 9px 7px 11px;
  background: var(--surface-3);
  border: 1px solid var(--border);
  border-left: 2px solid var(--primary);
  border-radius: var(--radius);
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
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.tool-name {
  font-weight: 600;
  color: var(--primary);
  font-size: 12px;
}
.tool-args {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
}
.tool-ms {
  flex: none;
  font-size: 11.5px;
}
.tool-result {
  flex-basis: 100%;
  font-size: 11.5px;
  color: var(--text-2);
  padding-left: 13px;
}
.blk-list {
  margin: 6px 0 0;
  padding-left: 16px;
  font-size: 12.5px;
  line-height: 1.75;
}
.blk-list-title {
  list-style: none;
  margin-left: -16px;
  font-weight: 600;
  font-size: 12px;
  margin-bottom: 2px;
}
.blk-code {
  margin: 8px 0;
  padding: 10px 12px;
  background: #1f2a3a;
  color: #d7e3f4;
  border-radius: var(--radius);
  font-family: var(--mono);
  font-size: 11.5px;
  line-height: 1.7;
  overflow: auto;
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

/* ---------- 右·输入区 ---------- */
.composer {
  flex: none;
  position: relative;
  border-top: 1px solid var(--border);
  padding: 10px 14px 12px;
  background: var(--surface);
}
.composer.is-drop {
  background: var(--primary-soft);
  box-shadow: inset 0 0 0 2px var(--primary);
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
.ctx {
  margin-bottom: 8px;
}
/* 上下文较多时: 标题与「清空」固定在头部, 标签区限高可滚动, 避免按钮被挤出可视区 */
.ctx-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.ctx-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  max-height: 84px;
  overflow-y: auto;
  padding-right: 2px;
}
.ctx-label {
  font-size: 12px;
  color: var(--text-3);
}
.ctx-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--primary-soft);
  color: var(--primary);
  border-radius: var(--radius);
  padding: 2px 6px 2px 8px;
  font-size: 11.5px;
}
.ctx-x {
  border: none;
  background: transparent;
  color: var(--primary);
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 0;
  opacity: 0.6;
}
.ctx-x:hover {
  opacity: 1;
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
  flex-wrap: wrap;
}
.pick {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pick-label {
  font-size: 12px;
  color: var(--text-3);
}
.pick-select {
  width: auto;
  height: 30px;
  font-size: 12.5px;
  padding: 0 8px;
}
</style>
