<script setup lang="ts">
/* 智能诊断: 三栏工作台
   左: 文件资源管理器 + 底端「运行状态」停靠面板(可折叠最小化)   中: 文件阅读器   右: 会话页签(1/2/3) + 对话 + 输入区
   说明: 当前为前端交互原型, 消息与文件内容为内置示例, 后续对接 agent / 大模型 / 文件接口。 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import DiagnosisFileTree from '@/components/DiagnosisFileTree.vue'
import DiagnosisFileReader from '@/components/DiagnosisFileReader.vue'
import DiagnosisInspector from '@/components/DiagnosisInspector.vue'
import { readFile, type Attach, type ReaderTab } from '@/components/diagnosisFiles'

/* ---------------- 三栏宽度拖拽 ---------------- */
const LEFT_MIN = 220
const LEFT_MAX = 460
const RIGHT_MIN = 320
const RIGHT_MAX = 640
const leftW = ref(280)
const rightW = ref(420)

/* 侧边栏折叠 */
const leftCollapsed = ref(false)
const rightCollapsed = ref(false)

type Side = 'left' | 'right'
let dragging: Side | null = null
let startX = 0
let startW = 0

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function onMove(e: MouseEvent) {
  if (!dragging) return
  const dx = e.clientX - startX
  if (dragging === 'left') leftW.value = clamp(startW + dx, LEFT_MIN, LEFT_MAX)
  else rightW.value = clamp(startW - dx, RIGHT_MIN, RIGHT_MAX)
}
function stopDrag() {
  dragging = null
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', stopDrag)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}
function startDrag(side: Side, e: MouseEvent) {
  dragging = side
  startX = e.clientX
  startW = side === 'left' ? leftW.value : rightW.value
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', stopDrag)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
onBeforeUnmount(stopDrag)

/* ---------------- 中·文件阅读器 (多标签页) ---------------- */
function toTab(f: Attach): ReaderTab {
  const p = readFile(f)
  return { ...f, kind: p.kind, content: p.content, original: p.content, lang: p.lang }
}

const INITIAL_FILE: Attach = {
  id: 'f-parsedmsg',
  name: 'messages.log',
  path: 'opt/tasks/20261015_1011/messages.log',
  size: '18.2 MB',
}
const tabs = ref<ReaderTab[]>([toTab(INITIAL_FILE)])
const activeTabId = ref(INITIAL_FILE.id)

function openFile(f: Attach) {
  const exist = tabs.value.find((t) => t.id === f.id)
  if (exist) {
    activeTabId.value = exist.id
    return
  }
  tabs.value.push(toTab(f))
  activeTabId.value = f.id
}
function closeTab(id: string) {
  const i = tabs.value.findIndex((t) => t.id === id)
  if (i < 0) return
  tabs.value.splice(i, 1)
  if (activeTabId.value === id) activeTabId.value = tabs.value[Math.max(0, i - 1)]?.id ?? ''
}
function onTabEdit(payload: { id: string; content: string }) {
  const t = tabs.value.find((x) => x.id === payload.id)
  if (t) t.content = payload.content
}
function saveTab(id: string) {
  const t = tabs.value.find((x) => x.id === id)
  if (t) t.original = t.content
}
function revertTab(id: string) {
  const t = tabs.value.find((x) => x.id === id)
  if (t) t.content = t.original
}

/* ---------------- 上下文 (由左侧文件树拖入 / 手动移除) ---------------- */
const attached = ref<Attach[]>([
  { id: 'f-messages', name: 'messages', path: 'var/log/messages', size: '18.2 MB' },
  { id: 'f-errorlog', name: 'error.log', path: 'var/log/nginx/error.log', size: '6.8 MB' },
])
function detachFile(id: string) {
  attached.value = attached.value.filter((a) => a.id !== id)
}

/* 放置区高亮: dragenter / dragleave 计数, 避免掠过子元素时抖动 */
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
  let files: Attach[] = []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) files = parsed as Attach[]
  } catch {
    return
  }
  for (const f of files) {
    if (!f?.id || attached.value.some((a) => a.id === f.id)) continue
    attached.value.push({ id: f.id, name: f.name, path: f.path, size: f.size })
  }
}

/* ---------------- 右·对话消息 ---------------- */
type Block =
  | { kind: 'text'; text: string }
  | { kind: 'list'; title?: string; items: string[] }
  | { kind: 'code'; lang: string; code: string }
  | { kind: 'tool'; name: string; args: string; status: 'done' | 'running'; ms: number; result?: string }

interface Message {
  id: number
  role: 'user' | 'assistant'
  time: string
  tokens?: string
  files?: Attach[]
  blocks: Block[]
}

let mid = 0
const DEMO_CPU: Message[] = [
  {
    id: ++mid,
    role: 'user',
    time: '10:12',
    files: [
      { id: 'f-messages', name: 'messages', path: 'var/log/messages', size: '18.2 MB' },
      { id: 'f-errorlog', name: 'error.log', path: 'var/log/nginx/error.log', size: '6.8 MB' },
    ],
    blocks: [
      { kind: 'text', text: '北京A 集群从 10:05 开始 CPU 使用率飙到 95%，业务接口 P99 明显变慢。帮我定位下原因。' },
    ],
  },
  {
    id: ++mid,
    role: 'assistant',
    time: '10:12',
    tokens: '↑1.2k ↓640',
    blocks: [
      { kind: 'text', text: '我先做一轮环境采样，确认是日志解析侧还是系统侧的问题。' },
      { kind: 'tool', name: 'read_log_file', args: '/var/log/messages · 10:00–10:15', status: 'done', ms: 320, result: '命中 12 条 OOM / timeout 相关记录' },
      { kind: 'tool', name: 'perf_top_snapshot', args: 'node-01 · batch=3', status: 'done', ms: 1240, result: 'loginsight-parser 峰值 480% CPU' },
      { kind: 'tool', name: 'sar_cpu_report', args: '10:00–10:20 · -u -q', status: 'done', ms: 860, result: 'runq-sz 12, %iowait 1.2 (排除 IO 等待)' },
      { kind: 'text', text: '采样结论：CPU 集中在 loginsight-parser 进程，且 %iowait 很低，可排除磁盘 IO。' },
      {
        kind: 'list',
        title: '根因初判',
        items: [
          '规则引擎在处理「超长单行日志」(>64KB) 时进入正则回溯，CPU 被打满；',
          '同一任务下 3 个分片并发解析，放大了资源争用；',
          '慢查询未设置超时，导致任务无法自愈退出。',
        ],
      },
    ],
  },
  {
    id: ++mid,
    role: 'user',
    time: '10:16',
    blocks: [{ kind: 'text', text: '有没有相似的历史案例可以参考？' }],
  },
  {
    id: ++mid,
    role: 'assistant',
    time: '10:16',
    tokens: '↑0.4k ↓210',
    blocks: [
      { kind: 'tool', name: 'search_similar_case', args: 'kb://cpu-spike · topk=3', status: 'done', ms: 540, result: '返回 2 条高相似案例 (0.91 / 0.87)' },
      {
        kind: 'list',
        title: '相似案例',
        items: [
          'INC-2261 上海B：超长行日志触发回溯，处置方式与本例一致；',
          'INC-1988 广州C：分片并发过高导致 CPU 抖动，靠降并发缓解。',
        ],
      },
    ],
  },
]

const DEMO_NGINX: Message[] = [
  {
    id: ++mid,
    role: 'user',
    time: '09:26',
    blocks: [{ kind: 'text', text: 'nginx 从 09:20 开始大量 502，帮忙看下。' }],
  },
  {
    id: ++mid,
    role: 'assistant',
    time: '09:26',
    tokens: '↑0.6k ↓320',
    blocks: [
      { kind: 'text', text: '我先看 upstream 连接情况与错误日志。' },
      {
        kind: 'tool',
        name: 'read_log_file',
        args: '/var/log/nginx/error.log · 09:20–09:26',
        status: 'done',
        ms: 280,
        result: 'upstream 连接超时 128 次',
      },
      { kind: 'text', text: '上游连接池被打满，建议扩容 worker 连接数并加超时重试。' },
    ],
  },
]

/* ---------------- 右·会话 (页签 1/2/3, 每个页签一段独立对话) ---------------- */
interface Session {
  id: number
  messages: Message[]
}

let sid = 0
const sessions = ref<Session[]>([
  { id: ++sid, messages: DEMO_CPU },
  { id: ++sid, messages: DEMO_NGINX },
  { id: ++sid, messages: [] },
])
const activeSession = ref(sessions.value[0].id)
const messages = computed<Message[]>(
  () => sessions.value.find((s) => s.id === activeSession.value)?.messages ?? [],
)

function addSession() {
  const s: Session = { id: ++sid, messages: [] }
  sessions.value.push(s)
  activeSession.value = s.id
}
function removeSession(id: number) {
  if (sessions.value.length <= 1) return
  const i = sessions.value.findIndex((s) => s.id === id)
  if (i < 0) return
  sessions.value.splice(i, 1)
  if (activeSession.value === id) activeSession.value = sessions.value[Math.max(0, i - 1)].id
}

const thinking = ref(false)
const running = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

async function scrollBottom() {
  await nextTick()
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}
onMounted(scrollBottom)

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms))
const nowHM = () => new Date().toTimeString().slice(0, 5)

type TextBlock = { kind: 'text'; text: string }
type ToolBlock = { kind: 'tool'; name: string; args: string; status: 'done' | 'running'; ms: number; result?: string }

/* 逐字流式输出 (逐块修改响应式对象以驱动重渲染) */
async function streamText(msg: Message, text: string) {
  const blk = reactive<TextBlock>({ kind: 'text', text: '' })
  msg.blocks.push(blk)
  const step = 3
  for (let i = 0; i < text.length; i += step) {
    blk.text = text.slice(0, i + step)
    await sleep(18)
    await scrollBottom()
  }
  blk.text = text
}

/* 工具调用: 先"执行中", 结束后回填耗时与结果, 并实时同步侧栏运行状态列表 */
async function runTool(msg: Message, name: string, args: string, result: string, ms: number) {
  const blk = reactive<ToolBlock>({ kind: 'tool', name, args, status: 'running', ms: 0 })
  msg.blocks.push(blk)
  const row = reactive({
    name,
    target: args,
    status: 'running' as 'running' | 'done',
    ms: null as number | null,
  })
  tools.value.push(row)
  await scrollBottom()
  await sleep(ms)
  blk.status = 'done'
  blk.ms = ms
  blk.result = result
  row.status = 'done'
  row.ms = ms
  await scrollBottom()
}

/* 演示脚本: 接入 agent 后由 SSE / WebSocket 事件驱动, 这里用定时器模拟事件流 */
async function runAgent(list: Message[]) {
  running.value = true
  thinking.value = true
  await sleep(650)
  thinking.value = false

  const am = reactive<Message>({ id: ++mid, role: 'assistant', time: nowHM(), blocks: [] })
  list.push(am)

  await streamText(am, '收到，我先抓取与这次问题相关的日志片段和系统指标。')
  await runTool(am, 'read_log_file', '/var/log/messages · 最近 30 分钟', '命中 12 条 OOM / timeout 记录', 900)
  await runTool(am, 'perf_top_snapshot', 'node-01 · batch=3', 'loginsight-parser 峰值 481% CPU', 1100)
  await streamText(am, '日志与采样一致：CPU 被解析进程占满，%iowait 很低，可先排除磁盘 IO。')

  setTodo('关联日志错误码与调用链', 'done')
  await runTool(am, 'sar_cpu_report', '10:00–10:20 · -u -q', 'runq-sz 12，%iowait 1.2', 800)
  await streamText(am, '三组证据指向同一处，给出根因与处置建议：')
  am.blocks.push({
    kind: 'list',
    title: '根因初判',
    items: [
      '规则引擎在超长单行日志（>64KB）上触发正则回溯，CPU 被打满；',
      '同任务 3 个分片并发解析，放大了资源争用；',
      '慢规则缺少超时保护，任务无法自愈退出。',
    ],
  })
  setTodo('输出根因结论与处置建议', 'done')

  am.tokens = '↑1.1k ↓520'
  modelInfo.input += 1100
  modelInfo.output += 520
  modelInfo.total = modelInfo.input + modelInfo.output
  modelInfo.usedPct = Math.min(100, Math.round((modelInfo.total / 128000) * 100))
  running.value = false
  await scrollBottom()
}

function send() {
  const text = draft.value.trim()
  if (!text || running.value) return
  const list = messages.value
  list.push({
    id: ++mid,
    role: 'user',
    time: nowHM(),
    files: attached.value.length ? attached.value.map((f) => ({ ...f })) : undefined,
    blocks: [{ kind: 'text', text }],
  })
  draft.value = ''
  attached.value = []
  scrollBottom()
  void runAgent(list)
}

/* ---------------- 右·输入区 ---------------- */
const draft = ref('')
const agentMode = ref('agent')
const AGENT_MODES = [
  { key: 'agent', label: 'Agent 模式', hint: '自动规划并调用工具' },
  { key: 'chat', label: '对话模式', hint: '仅大模型问答，不调用工具' },
  { key: 'deep', label: '深度诊断', hint: '多轮规划 + 并行工具编排' },
]
const model = ref('deepseek-v3')
const MODELS = [
  { key: 'deepseek-v3', label: 'DeepSeek-V3' },
  { key: 'doubao-pro', label: '豆包 Pro 32K' },
  { key: 'qwen-max', label: '通义千问 Max' },
  { key: 'gpt-4o', label: 'GPT-4o' },
]

/* ---------------- 左·运行状态 (停靠于侧栏底端, 可折叠最小化) ---------------- */
const inspectorOpen = ref(true)

const modelInfo = reactive({
  name: 'DeepSeek-V3',
  provider: '深度求索',
  window: '128K',
  usedPct: 12,
  input: 12480,
  output: 3206,
  total: 15686,
  cost: '¥0.18',
  latency: '1.2s',
})

type TodoStatus = 'done' | 'doing' | 'todo'
const todos = ref<{ text: string; status: TodoStatus }[]>([
  { text: '采集系统与解析器 CPU 采样', status: 'done' },
  { text: '定位高占用进程与线程栈', status: 'done' },
  { text: '检索相似历史案例', status: 'done' },
  { text: '关联日志错误码与调用链', status: 'doing' },
  { text: '输出根因结论与处置建议', status: 'todo' },
])
const todoDone = computed(() => todos.value.filter((t) => t.status === 'done').length)

const tools = ref<{ name: string; target: string; status: 'done' | 'running'; ms: number | null }[]>([
  { name: 'read_log_file', target: '/var/log/messages', status: 'done', ms: 320 },
  { name: 'perf_top_snapshot', target: 'node-01', status: 'done', ms: 1240 },
  { name: 'sar_cpu_report', target: '10:00–10:20', status: 'done', ms: 860 },
  { name: 'search_similar_case', target: 'kb://cpu-spike', status: 'done', ms: 540 },
  { name: 'correlate_error_code', target: 'E-TIMEOUT-504', status: 'running', ms: null },
])

function setTodo(text: string, status: TodoStatus) {
  const t = todos.value.find((x) => x.text === text)
  if (t) t.status = status
}
</script>

<template>
  <div class="diag">
    <!-- ============ 左侧栏: 文件资源管理器 + 底端运行状态 ============ -->
    <aside v-if="!leftCollapsed" class="pane pane--left" :style="{ width: leftW + 'px' }">
      <DiagnosisFileTree :active-id="activeTabId" @open="openFile">
        <template #actions>
          <button class="icon-btn" title="收起左侧栏" @click="leftCollapsed = true">«</button>
        </template>
      </DiagnosisFileTree>

      <!-- 底端停靠: 运行状态(Todo List / 模型信息 / 工具调用), 可折叠最小化为一条底栏 -->
      <div class="insp-dock" :class="{ 'is-collapsed': !inspectorOpen }">
        <div class="insp-head">
          <span class="pane-title">运行状态</span>
          <span v-if="!inspectorOpen" class="insp-mini o-mono o-text-3">
            Todo {{ todoDone }}/{{ todos.length }} · {{ modelInfo.total.toLocaleString('en-US') }} tokens
          </span>
          <button
            class="icon-btn insp-toggle"
            :title="inspectorOpen ? '折叠运行状态' : '展开运行状态'"
            @click="inspectorOpen = !inspectorOpen"
          >
            {{ inspectorOpen ? '▾' : '▸' }}
          </button>
        </div>
        <DiagnosisInspector
          v-show="inspectorOpen"
          :model-info="modelInfo"
          :todos="todos"
          :tools="tools"
        />
      </div>
    </aside>
    <button v-else class="rail rail--left" title="展开左侧栏" @click="leftCollapsed = false">›</button>

    <div v-if="!leftCollapsed" class="resizer" @mousedown="startDrag('left', $event)"></div>

    <!-- ============ 中间主体: 文件阅读器 ============ -->
    <section class="pane pane--center">
      <DiagnosisFileReader
        :tabs="tabs"
        :active-id="activeTabId"
        @activate="activeTabId = $event"
        @close="closeTab"
        @edit="onTabEdit"
        @save="saveTab"
        @revert="revertTab"
      />
    </section>

    <div v-if="!rightCollapsed" class="resizer" @mousedown="startDrag('right', $event)"></div>

    <!-- ============ 右侧栏: 会话页签 + 对话 + 输入区 ============ -->
    <aside v-if="!rightCollapsed" class="pane pane--right" :style="{ width: rightW + 'px' }">
      <div class="pane-head chat-tabs">
        <div class="tab-strip">
          <button
            v-for="(s, i) in sessions"
            :key="s.id"
            class="chat-tab"
            :class="{ 'is-active': s.id === activeSession }"
            :title="'会话 ' + (i + 1)"
            @click="activeSession = s.id"
          >
            <span class="chat-tab-no">{{ i + 1 }}</span>
            <span
              v-if="sessions.length > 1"
              class="chat-tab-x"
              title="关闭会话"
              @click.stop="removeSession(s.id)"
              >✕</span
            >
          </button>
          <button class="chat-tab-add" title="新建会话" @click="addSession">＋</button>
        </div>
        <div class="o-row" style="gap: 8px">
          <span class="o-badge" :class="running ? 'o-badge--success' : 'o-badge--outline'">{{
            running ? '● 运行中' : '● 空闲'
          }}</span>
          <button class="o-btn o-btn--sm" title="清空当前会话" @click="messages.length = 0">清空</button>
          <button class="icon-btn" title="收起右侧栏" @click="rightCollapsed = true">»</button>
        </div>
      </div>

      <div ref="bodyRef" class="chat-body">
        <div v-for="m in messages" :key="m.id" class="msg" :class="`msg--${m.role}`">
          <div class="avatar" :class="`avatar--${m.role}`">{{ m.role === 'user' ? '我' : 'AI' }}</div>
          <div class="bubble">
            <div class="msg-head">
              <b>{{ m.role === 'user' ? '我' : '智能诊断助手' }}</b>
              <span class="o-mono o-text-3">{{ m.time }}</span>
              <span v-if="m.tokens" class="o-badge o-badge--outline">{{ m.tokens }}</span>
            </div>

            <div v-if="m.files?.length" class="msg-files">
              <span v-for="f in m.files" :key="f.id" class="msg-file o-mono" :title="f.path">📄 {{ f.name }}</span>
            </div>

            <template v-for="(b, i) in m.blocks" :key="i">
              <p v-if="b.kind === 'text'" class="blk-text">{{ b.text }}</p>

              <div v-else-if="b.kind === 'tool'" class="blk-tool">
                <span class="tool-dot" :class="{ 'is-run': b.status === 'running' }"></span>
                <span class="o-mono tool-name">{{ b.name }}</span>
                <span class="o-mono o-text-3 tool-args">{{ b.args }}</span>
                <span class="o-mono o-text-3 tool-ms">{{ b.ms }}ms</span>
                <div v-if="b.result" class="tool-result">{{ b.result }}</div>
              </div>

              <ul v-else-if="b.kind === 'list'" class="blk-list">
                <li v-if="b.title" class="blk-list-title">{{ b.title }}</li>
                <li v-for="(it, j) in b.items" :key="j">{{ it }}</li>
              </ul>

              <pre v-else class="blk-code"><code>{{ b.code }}</code></pre>
            </template>
          </div>
        </div>

        <div v-if="thinking" class="msg msg--assistant">
          <div class="avatar avatar--assistant">AI</div>
          <div class="bubble">
            <div class="typing"><span></span><span></span><span></span> 正在分析…</div>
          </div>
        </div>

        <div v-if="!messages.length && !thinking" class="chat-empty">
          <div class="chat-empty-ico">🩺</div>
          <p>新会话已就绪</p>
          <span>描述你的诊断问题，或从左侧目录勾选 / 拖拽文件作为分析上下文。</span>
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
        <div v-if="attached.length" class="ctx-chips">
          <span class="ctx-label">上下文 {{ attached.length }}</span>
          <span v-for="f in attached" :key="f.id" class="ctx-chip o-mono" :title="f.path">
            {{ f.name }}
            <button class="ctx-x" title="移除" @click="detachFile(f.id)">×</button>
          </span>
          <button class="o-btn o-btn--text ctx-clear" @click="attached = []">清空</button>
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
    <button v-else class="rail rail--right" title="展开右侧栏" @click="rightCollapsed = false">‹</button>
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
  flex: none;
  background: var(--surface-3);
}
.pane--center {
  flex: 1;
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

/* ---------- 拖拽条 ---------- */
.resizer {
  flex: none;
  width: 6px;
  cursor: col-resize;
  background: transparent;
  position: relative;
  z-index: 2;
}
.resizer::after {
  content: '';
  position: absolute;
  inset: 0 2px;
  background: var(--border);
  transition: background 0.15s;
}
.resizer:hover::after {
  background: var(--primary);
}

/* ---------- 侧边栏折叠 ---------- */
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
.rail {
  flex: none;
  width: 26px;
  padding: 0;
  border: none;
  cursor: pointer;
  background: var(--surface-3);
  color: var(--text-3);
  font-size: 14px;
  transition: all 0.15s;
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
