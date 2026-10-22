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
import SessionHistoryList from '@/components/diagnosis/SessionHistoryList.vue'
import Icon from '@/components/Icon.vue'
import { useTeamsStore } from '@/stores/teams'
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
        />
      </section>

      <!-- 日志文件 -->
      <section class="side-sec side-sec--files" :class="{ 'is-collapsed': !filesOpen }">
        <header class="side-sec-head" @click="filesOpen = !filesOpen">
          <FoldChevron :open="filesOpen" />
          <span class="side-sec-title">日志文件</span>
          <span v-if="attached.length" class="side-count">已选 {{ attached.length }}</span>
          <span class="spacer"></span>
          <button class="icon-btn" title="刷新文件列表" @click.stop="loadFiles">
            <Icon name="refresh" :size="15" />
          </button>
        </header>
        <DiagnosisFileTree
          v-if="filesOpen"
          ref="treeRef"
          :nodes="fileNodes"
          :loading="filesLoading"
          :active-id="activeTabId"
          @open="openFile"
          @select="onTreeSelect"
        >
          <template #empty>
            <div class="side-empty">
              <p class="side-empty-t">还没有可分析的日志</p>
              <p class="side-empty-s">上传日志后，勾选即可让助手直接读取原文。</p>
              <router-link class="o-btn o-btn--sm" to="/teams">去上传日志</router-link>
            </div>
          </template>
        </DiagnosisFileTree>
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
          :todos="todos"
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
      <div ref="bodyRef" class="chat-body">
        <div class="chat-list">
          <div v-if="loadingSession" class="chat-empty"><p>正在加载会话…</p></div>

          <template v-else>
          <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
            <div class="avatar" :class="`avatar--${m.role}`">{{ m.role === 'user' ? '我' : 'AI' }}</div>
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
        <div class="composer-inner">
          <div v-if="dropActive" class="drop-mask">松开鼠标，把文件加入本次提问</div>
          <ComposerTaskDock :plan="runPlanBlock" :tools="runTools" :running="running" />
          <div v-if="attached.length" class="ctx-chips">
            <span class="ctx-label">已选 {{ attached.length }} 份日志</span>
            <span v-for="f in attached" :key="f.id" class="ctx-chip" :title="f.path">
              {{ f.name }}
              <button class="ctx-x" title="移除" @click="detachFile(f.id)">×</button>
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
                v-else
                class="o-btn o-btn--primary"
                :disabled="!draft.trim() || !activeSessionId || !ready"
                @click="send"
              >
                开始诊断
              </button>
            </div>
          </div>
          <p v-if="errorText" class="composer-error">{{ errorText }}</p>
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
  max-width: 86%;
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
.blk-plan {
  margin: 6px 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface-3);
}
.blk-plan-title {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
}
.plan-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-2);
}
.plan-mark {
  flex: none;
  width: 12px;
  color: var(--text-4);
}
.plan--completed .plan-mark {
  color: var(--success);
}
.plan--completed {
  color: var(--text-3);
  text-decoration: line-through;
}
.plan--in_progress .plan-mark,
.plan--in_progress {
  color: var(--primary);
  font-weight: 600;
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
  padding: 2px 6px 2px 8px;
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ctx-x {
  border: none;
  background: transparent;
  color: var(--primary);
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 0;
  opacity: 0.7;
}
.ctx-x:hover {
  opacity: 1;
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
.composer-error {
  margin-top: 8px;
  padding: 6px 8px;
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--radius);
  font-size: 12px;
  line-height: 1.6;
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
.chat-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0;
  display: block;
}
.chat-list {
  width: 100%;
  padding: 14px 18px 18px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 100%;
}
.msg--assistant .bubble {
  max-width: 94%;
}
.msg--user .bubble {
  max-width: 78%;
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
