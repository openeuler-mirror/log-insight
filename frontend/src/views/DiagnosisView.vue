<script setup lang="ts">
/* 智能诊断: 三栏 Agent 对话工作台
   左: 会话列表(上) + 打开的目录/文件(下)   中: 对话消息 + 输入区   右: 模型信息 / Todo / 工具调用
   说明: 当前为前端交互原型, 消息与洞察数据为内置示例, 后续对接 agent / 大模型接口。 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import DiagnosisFileTree from '@/components/DiagnosisFileTree.vue'
import DiagnosisInspector from '@/components/DiagnosisInspector.vue'

/* ---------------- 三栏宽度拖拽 ---------------- */
const LEFT_MIN = 190
const LEFT_MAX = 420
const RIGHT_MIN = 250
const RIGHT_MAX = 480
const leftW = ref(250)
const rightW = ref(318)

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

/* ---------------- 左·会话列表 ---------------- */
interface Session {
  id: number
  title: string
  time: string
  preview: string
}
const sessions = ref<Session[]>([
  { id: 1, title: '北京A 集群 CPU 飙升诊断', time: '10:12', preview: '已定位: 解析器死循环' },
  { id: 2, title: 'nginx 502 突发排查', time: '09:26', preview: '上游连接池被打满' },
  { id: 3, title: '磁盘 IO 队列深度异常', time: '昨天', preview: '待确认是否落盘抖动' },
  { id: 4, title: '登录服务 500 根因定位', time: '10-13', preview: 'bcrypt 版本不兼容' },
  { id: 5, title: 'syslog 采集延迟排查', time: '10-12', preview: '已给出缓冲调参建议' },
])
const activeSession = ref(1)
const activeTitle = computed(() => sessions.value.find((s) => s.id === activeSession.value)?.title ?? '未命名会话')

function removeSession(id: number) {
  sessions.value = sessions.value.filter((s) => s.id !== id)
  if (activeSession.value === id) activeSession.value = sessions.value[0]?.id ?? 0
}

/* ---------------- 中·输入区上下文 (由左侧文件树拖入 / 手动移除) ---------------- */
interface Attach {
  id: string
  name: string
  path: string
  size?: string
}

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

/* ---------------- 中·对话消息 ---------------- */
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
const messages = ref<Message[]>([
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
      { kind: 'text', text: '采样结论：CPU 集中在 loginsight-parser 进程，且 %iowait 很低，可排除磁盘 IO。进一步看该进程的线程栈：' },
      { kind: 'code', lang: 'bash', code: 'ps -o pid,pcpu,etime,cmd -C loginsight-parser\n#  6821 481.3  00:07:42  loginsight-parser --task 20261015_1011' },
      {
        kind: 'list',
        title: '根因初判',
        items: [
          '规则引擎在处理「超长单行日志」(>64KB) 时进入正则回溯，CPU 被打满；',
          '同一任务下 3 个分片并发解析，放大了资源争用；',
          '慢查询未设置超时，导致任务无法自愈退出。',
        ],
      },
      { kind: 'text', text: '建议：为单行长度加硬上限并对该规则加超时保护；把分片并发降到 2。需要我继续关联错误码与调用链吗？' },
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
])

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

/* 工具调用: 先"执行中", 结束后回填耗时与结果, 并实时同步右侧栏列表 */
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

function setTodo(text: string, status: TodoStatus) {
  const t = todos.value.find((x) => x.text === text)
  if (t) t.status = status
}

/* 演示脚本: 接入 agent 后由 SSE / WebSocket 事件驱动, 这里用定时器模拟事件流 */
async function runAgent() {
  running.value = true
  thinking.value = true
  await sleep(650)
  thinking.value = false

  const am = reactive<Message>({ id: ++mid, role: 'assistant', time: nowHM(), blocks: [] })
  messages.value.push(am)

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
  messages.value.push({
    id: ++mid,
    role: 'user',
    time: nowHM(),
    files: attached.value.length ? attached.value.map((f) => ({ ...f })) : undefined,
    blocks: [{ kind: 'text', text }],
  })
  draft.value = ''
  attached.value = []
  scrollBottom()
  void runAgent()
}

/* ---------------- 中·输入区 ---------------- */
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

/* ---------------- 右·模型信息 ---------------- */
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

/* ---------------- 右·Todo List ---------------- */
type TodoStatus = 'done' | 'doing' | 'todo'
const todos = ref<{ text: string; status: TodoStatus }[]>([
  { text: '采集系统与解析器 CPU 采样', status: 'done' },
  { text: '定位高占用进程与线程栈', status: 'done' },
  { text: '检索相似历史案例', status: 'done' },
  { text: '关联日志错误码与调用链', status: 'doing' },
  { text: '输出根因结论与处置建议', status: 'todo' },
])

/* ---------------- 右·工具调用列表 ---------------- */
const tools = ref<{ name: string; target: string; status: 'done' | 'running'; ms: number | null }[]>([
  { name: 'read_log_file', target: '/var/log/messages', status: 'done', ms: 320 },
  { name: 'perf_top_snapshot', target: 'node-01', status: 'done', ms: 1240 },
  { name: 'sar_cpu_report', target: '10:00–10:20', status: 'done', ms: 860 },
  { name: 'search_similar_case', target: 'kb://cpu-spike', status: 'done', ms: 540 },
  { name: 'correlate_error_code', target: 'E-TIMEOUT-504', status: 'running', ms: null },
])
</script>

<template>
  <div class="diag">
    <!-- ============ 左侧栏 ============ -->
    <aside v-if="!leftCollapsed" class="pane pane--left" :style="{ width: leftW + 'px' }">
      <div class="pane-head">
        <span class="pane-title">会话</span>
        <div class="o-row" style="gap: 6px">
          <button class="o-btn o-btn--sm o-btn--primary" title="新建会话">＋ 新建</button>
          <button class="icon-btn" title="收起左侧栏" @click="leftCollapsed = true">«</button>
        </div>
      </div>
      <div class="session-list">
        <button
          v-for="s in sessions"
          :key="s.id"
          class="session"
          :class="{ 'is-active': s.id === activeSession }"
          @click="activeSession = s.id"
        >
          <span class="session-top">
            <b class="session-title">{{ s.title }}</b>
            <span class="o-mono o-text-3 session-time">{{ s.time }}</span>
            <span class="session-del" title="删除会话" @click.stop="removeSession(s.id)">✕</span>
          </span>
          <span class="session-preview">{{ s.preview }}</span>
        </button>
        <div v-if="!sessions.length" class="o-empty" style="padding: 24px 0; font-size: 12.5px">暂无会话</div>
      </div>

      <DiagnosisFileTree />
    </aside>
    <button v-else class="rail rail--left" title="展开左侧栏" @click="leftCollapsed = false">›</button>

    <div v-if="!leftCollapsed" class="resizer" @mousedown="startDrag('left', $event)"></div>

    <!-- ============ 中间主体 ============ -->
    <section class="pane pane--center">
      <div class="chat-head">
        <div class="chat-title">
          <b>{{ activeTitle }}</b>
          <span class="o-badge" :class="running ? 'o-badge--success' : 'o-badge--outline'">{{
            running ? '● 运行中' : '● 空闲'
          }}</span>
        </div>
        <div class="o-row" style="gap: 8px">
          <button class="o-btn o-btn--sm">导出对话</button>
          <button class="o-btn o-btn--sm">清空</button>
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
            <span class="o-mono o-text-3 pick-hint">{{ AGENT_MODES.find((a) => a.key === agentMode)?.hint }}</span>
          </div>
          <button class="o-btn o-btn--primary" :disabled="!draft.trim() || running" @click="send">
            {{ running ? '分析中…' : '发送 ➤' }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="!rightCollapsed" class="resizer" @mousedown="startDrag('right', $event)"></div>

    <!-- ============ 右侧栏 ============ -->
    <aside v-if="!rightCollapsed" class="pane pane--right" :style="{ width: rightW + 'px' }">
      <div class="pane-head">
        <span class="pane-title">模型信息</span>
        <div class="o-row" style="gap: 6px">
          <span class="o-badge o-badge--brand">{{ modelInfo.name }}</span>
          <button class="icon-btn" title="收起右侧栏" @click="rightCollapsed = true">»</button>
        </div>
      </div>
      <DiagnosisInspector :model-info="modelInfo" :todos="todos" :tools="tools" />
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
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  flex: none;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
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

/* ---------- 左·会话 ---------- */
.session-list {
  flex: 1 1 45%;
  min-height: 0;
  overflow: auto;
  padding: 8px;
}
.session {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  text-align: left;
  border: 1px solid transparent;
  border-radius: var(--radius);
  background: transparent;
  padding: 9px 10px;
  cursor: pointer;
  font: inherit;
}
.session:is(:hover, :focus-visible) {
  background: var(--surface);
}
.session.is-active {
  background: var(--surface);
  border-color: var(--primary-tint);
  box-shadow: var(--shadow-1);
}
.session-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.session-title {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session.is-active .session-title {
  color: var(--primary);
}
.session-time {
  flex: none;
}
.session-del {
  flex: none;
  width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: var(--text-4);
  font-size: 10px;
  opacity: 0;
  transition: all 0.15s;
}
.session:is(:hover, :focus-within) .session-time {
  display: none;
}
.session:is(:hover, :focus-within) .session-del {
  opacity: 1;
}
.session-del:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.session-preview {
  font-size: 12px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 中·头部 ---------- */
.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  flex: none;
}
.chat-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.chat-title b {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---------- 中·消息 ---------- */
.chat-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  background: var(--surface);
}
.msg {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.msg--user {
  flex-direction: row-reverse;
}
.avatar {
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
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
  max-width: 74%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
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
  margin-bottom: 8px;
}
.msg-head b {
  font-size: 12.5px;
}
.msg-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.msg-file {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 2px 8px;
  font-size: 11.5px;
  color: var(--text-2);
}
.blk-text {
  font-size: 13.5px;
  line-height: 1.75;
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
  gap: 8px;
  margin: 8px 0;
  padding: 8px 10px 8px 12px;
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
}
.tool-args {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-ms {
  flex: none;
}
.tool-result {
  flex-basis: 100%;
  font-size: 12.5px;
  color: var(--text-2);
  padding-left: 15px;
}
.blk-list {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 13.5px;
  line-height: 1.8;
}
.blk-list-title {
  list-style: none;
  margin-left: -18px;
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 2px;
}
.blk-code {
  margin: 8px 0;
  padding: 10px 12px;
  background: #1f2a3a;
  color: #d7e3f4;
  border-radius: var(--radius);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.7;
  overflow: auto;
}
.typing {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
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

/* ---------- 中·输入区 ---------- */
.composer {
  flex: none;
  position: relative;
  border-top: 1px solid var(--border);
  padding: 12px 16px 14px;
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
  min-height: 66px;
  font-family: var(--font);
  font-size: 13.5px;
}
.composer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
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
.pick-hint {
  font-size: 11.5px;
}

</style>