<script setup lang="ts">
/* 智能巡检详情: 单个巡检任务的执行结果展示 (健康评分 / 检查项结论 / 摘要 / 规则命中) */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post, apiError, TOKEN_KEY } from '@/api/http'
import { fmtTime } from '@/parser/types'

interface InspectionTask {
  id: string
  type: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  filename: string
  name?: string
  progress: number
  error?: string
  createdAt: string
  finishedAt?: string | null
  logSize: number
  creatorName: string
}

interface InspectionFinding {
  checker: string
  passed: boolean
  severity: string
  message: string
  description?: string
}

interface InspectionRuleDetail {
  ruleId: number
  ruleName: string
  severity: string
  level: string
  lineNo: number
  matchedText: string
  raw: string
  checker: string
}

interface InspectionReport {
  taskId: string
  healthScore: number | null
  summary: string
  findings: InspectionFinding[]
  content: { ruleHits?: number; metricCount?: number; ruleDetails?: InspectionRuleDetail[] }
  llm: boolean
  createdAt?: string | null
}

interface RuleSuggestionItem {
  name: string
  description: string
  matchType: string
  pattern: string
  severity: string
  reason: string
  hitCount: number | null
  sample: string
}

interface RuleSuggestionResult {
  taskId: string
  assessment: string
  items: Record<string, any>[]
  model: string
  updatedAt?: string | null
}

const STATUS_BADGE: Record<InspectionTask['status'], { label: string; cls: string }> = {
  pending: { label: '排队中', cls: 'o-badge--grey' },
  running: { label: '巡检中', cls: 'o-badge--warning' },
  done: { label: '已完成', cls: 'o-badge--success' },
  failed: { label: '失败', cls: 'o-badge--danger' },
  cancelled: { label: '已取消', cls: 'o-badge--grey' },
  interrupted: { label: '已中断', cls: 'o-badge--danger' },
}

/* 巡检检查项级别 (error/warn/info) → 徽章样式 */
const SEV_BADGE: Record<string, { label: string; cls: string }> = {
  critical: { label: '致命', cls: 'o-badge--danger' },
  error: { label: '错误', cls: 'o-badge--danger' },
  warn: { label: '告警', cls: 'o-badge--warning' },
  warning: { label: '告警', cls: 'o-badge--warning' },
  info: { label: '信息', cls: 'o-badge--success' },
}

const route = useRoute()
const taskId = computed(() => route.params.taskId as string)

const task = ref<InspectionTask | null>(null)
const report = ref<InspectionReport | null>(null)
const noAccess = ref(false)
const notFound = ref(false)
const loadErr = ref('')
const reportPending = ref(false)
let pollTimer: number | undefined

const running = computed(() => !!task.value && ['pending', 'running'].includes(task.value.status))

async function loadTask() {
  loadErr.value = ''
  try {
    task.value = await get<InspectionTask>(`/tasks/${taskId.value}`)
    noAccess.value = false
    notFound.value = false
  } catch (e: any) {
    if (e?.response?.status === 403) noAccess.value = true
    else if (e?.response?.status === 404) notFound.value = true
    else loadErr.value = apiError(e)
  }
}

async function loadReport() {
  try {
    report.value = await get<InspectionReport>(`/tasks/${taskId.value}/report`)
    reportPending.value = false
  } catch (e: any) {
    report.value = null
    reportPending.value = e?.response?.status === 404
  }
}

/* 排队/巡检中每 2s 轮询; 结束后拉取报告 */
function schedulePoll() {
  stopPoll()
  pollTimer = window.setInterval(async () => {
    await loadTask()
    if (!running.value) {
      stopPoll()
      if (task.value?.status === 'done') await loadReport()
    }
  }, 2000)
}
function stopPoll() {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

const findings = computed(() => report.value?.findings ?? [])
const failedCount = computed(() => findings.value.filter((f) => !f.passed).length)
const ruleHits = computed(() => report.value?.content?.ruleHits ?? 0)
const metricCount = computed(() => report.value?.content?.metricCount ?? 0)
const score = computed(() => report.value?.healthScore ?? null)
const scoreCls = computed(() => {
  const s = score.value
  if (s === null) return ''
  return s >= 90 ? 'ok' : s >= 70 ? 'warn' : 'bad'
})

/* 检查项展开: 查看详细结论与关联的规则命中明细 */
const expanded = ref<Record<string, boolean>>({})
function toggleRow(checker: string) {
  expanded.value[checker] = !expanded.value[checker]
}
function rulesOf(checker: string): InspectionRuleDetail[] {
  return (report.value?.content?.ruleDetails ?? []).filter((r) => r.checker === checker)
}

/* 智能巡检规则推荐: 按需 SSE 流式评估 + 建议项可编辑 + 接纳跳转新增规则 */
const router = useRouter()
const sugLoaded = ref(false)          // 是否已生成过推荐
const sugStreaming = ref(false)
const sugProgress = ref(0)
const sugAssessment = ref('')
const sugItems = ref<RuleSuggestionItem[]>([])
const sugModel = ref('')
const sugErr = ref('')
let sugEs: EventSource | null = null

function closeSug() {
  sugEs?.close()
  sugEs = null
  sugStreaming.value = false
}

function normItem(raw: Record<string, any>): RuleSuggestionItem {
  return {
    name: raw.name ?? '',
    description: raw.description ?? '',
    matchType: raw.matchType ?? raw.match_type ?? 'keyword',
    pattern: raw.pattern ?? '',
    severity: raw.severity ?? 'warn',
    reason: raw.reason ?? '',
    hitCount: raw.hitCount ?? raw.hit_count ?? null,
    sample: raw.sample ?? '',
  }
}

async function loadSuggestion() {
  try {
    const r = await get<RuleSuggestionResult>(`/tasks/${taskId.value}/rule-suggestions`)
    sugAssessment.value = r.assessment
    sugItems.value = (r.items ?? []).map(normItem)
    sugModel.value = r.model
    sugLoaded.value = true
  } catch {
    sugLoaded.value = false   // 404: 尚未生成, 忽略
  }
}

async function startSuggestion() {
  sugErr.value = ''
  sugAssessment.value = ''
  sugItems.value = []
  sugProgress.value = 0
  try {
    const { runId } = await post<{ runId: string }>(`/tasks/${taskId.value}/rule-suggestions`)
    sugLoaded.value = true
    sugStreaming.value = true
    const token = localStorage.getItem(TOKEN_KEY) ?? ''
    sugEs = new EventSource(
      `/api/tasks/${taskId.value}/rule-suggestions/events?run_id=${encodeURIComponent(runId)}&token=${encodeURIComponent(token)}`)
    sugEs.addEventListener('suggestion_token', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data)
      sugAssessment.value += d.text ?? ''
    })
    sugEs.addEventListener('suggestion', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data)
      if (typeof d.assessment === 'string') sugAssessment.value = d.assessment
      sugItems.value = (d.items ?? []).map(normItem)
    })
    sugEs.addEventListener('progress', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data)
      sugProgress.value = d.progress ?? 0
    })
    sugEs.addEventListener('done', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data)
      if (d.status && d.status !== 'done') sugErr.value = `生成未完成(${d.status})`
      closeSug()
      sugProgress.value = 100
    })
    sugEs.addEventListener('error', (ev) => {
      const me = ev as MessageEvent
      if (me.data) {
        try { sugErr.value = JSON.parse(me.data).message || '规则推荐生成失败' } catch { sugErr.value = '规则推荐生成失败' }
      }
      closeSug()
    })
    sugEs.onerror = () => {
      if (sugStreaming.value && !sugErr.value) {
        sugErr.value = 'SSE 连接中断，请重试'
        closeSug()
      }
    }
  } catch (e) {
    sugErr.value = apiError(e)
  }
}

function removeItem(i: number) {
  sugItems.value.splice(i, 1)
}

/* 接纳某条建议 → 跳转规则管理页, 预填新增弹窗 */
function acceptItem(item: RuleSuggestionItem) {
  router.push({
    path: '/rules',
    query: {
      prefill: '1', name: item.name, description: item.description || '',
      match_type: item.matchType || 'keyword', pattern: item.pattern || '',
      severity: item.severity || 'warn',
    },
  })
}

onMounted(async () => {
  await loadTask()
  if (!task.value) return
  if (running.value) schedulePoll()
  else if (task.value.status === 'done') {
    await loadReport()
    await loadSuggestion()
  }
})
onBeforeUnmount(() => {
  stopPoll()
  closeSug()
})
</script>

<template>
  <div v-if="noAccess" class="o-empty o-card">
    <div class="icon">🔒</div>
    <div>无权查看该巡检任务</div>
    <div class="hint">仅任务创建者、平台管理员或所属团队成员可查看</div>
  </div>
  <div v-else-if="notFound" class="o-empty o-card">
    <div class="icon">📭</div>
    <div>任务不存在或已被删除</div>
  </div>
  <div v-else-if="loadErr" class="o-empty o-card">
    <div class="icon">⚠️</div>
    <div>{{ loadErr }}</div>
  </div>
  <template v-else-if="task">
    <!-- 信息横幅 + 结论概览 -->
    <div class="o-banner">
      <div class="banner-left">
        <router-link to="/inspection" class="back-btn">← 返回智能巡检</router-link>
        <h2>
          {{ task.name || task.filename }}
          <span class="o-badge" :class="STATUS_BADGE[task.status].cls">{{ STATUS_BADGE[task.status].label }}</span>
          <span class="o-badge o-badge--outline">智能巡检</span>
        </h2>
        <div class="meta">
          <span class="o-mono o-text-3">{{ task.filename }}（{{ (task.logSize / 1024).toFixed(1) }} KB）</span>
          <span class="o-text-3">{{ task.creatorName }} · {{ fmtTime(task.createdAt) }}</span>
          <span v-if="task.finishedAt" class="o-text-3">完成于 {{ fmtTime(task.finishedAt) }}</span>
        </div>
      </div>
      <div class="o-banner-stats">
        <div class="o-banner-stat"><b :class="scoreCls">{{ score === null ? '—' : score }}</b><span>健康评分</span></div>
        <div class="o-banner-stat"><b>{{ findings.length }}</b><span>检查项</span></div>
        <div class="o-banner-stat"><b style="color: var(--danger)">{{ failedCount }}</b><span>未通过</span></div>
        <div class="o-banner-stat"><b>{{ ruleHits }}</b><span>规则命中</span></div>
        <div class="o-banner-stat"><b>{{ metricCount }}</b><span>采集指标</span></div>
      </div>
    </div>

    <!-- 巡检中 -->
    <div v-if="running" class="o-card" style="margin-bottom: 16px">
      <div class="o-row" style="gap: 10px">
        <span class="spin" />
        <b>巡检{{ task.status === 'pending' ? '排队中' : '进行中' }}… {{ task.progress }}%</b>
        <span class="o-text-3" style="font-size: 13px">巡检完成后本页将自动刷新展示结果详情</span>
      </div>
    </div>
    <div v-else-if="task.status === 'failed'" class="o-badge o-badge--danger" style="margin-bottom: 16px">
      巡检失败：{{ task.error || '未知错误' }}
    </div>
    <div v-else-if="task.status === 'cancelled' || task.status === 'interrupted'"
         class="o-badge o-badge--grey" style="margin-bottom: 16px">
      巡检已{{ task.status === 'cancelled' ? '取消' : '中断' }}，无结果详情
    </div>

    <!-- 巡检摘要 -->
    <div v-if="report" class="o-card" style="margin-bottom: 16px">
      <div class="o-card__title">
        巡检摘要
        <span class="o-mono o-text-3">{{ report.llm ? 'LLM 智能总结' : '规则引擎总结' }}</span>
      </div>
      <div class="summary">{{ report.summary || '（无摘要）' }}</div>
    </div>

    <!-- 检查项结果 -->
    <div class="o-card">
      <div class="o-card__title">
        检查项结果
        <span class="o-mono o-text-3">{{ findings.length }} 项 · {{ failedCount }} 项未通过</span>
      </div>
      <div class="o-table-wrap">
        <table class="o-table">
          <thead>
            <tr>
              <th style="width: 34px"></th>
              <th>检查项</th>
              <th style="width: 110px">结果</th>
              <th style="width: 90px">级别</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!findings.length">
              <td colspan="5">
                <div class="o-empty" style="padding: 40px 0">
                  <div class="icon">📋</div>
                  <div>{{ reportPending || running ? '巡检结果生成中…' : '暂无检查项结果' }}</div>
                </div>
              </td>
            </tr>
            <template v-for="f in findings" :key="f.checker">
              <tr class="row-clickable" @click="toggleRow(f.checker)">
                <td><span class="caret" :class="{ open: expanded[f.checker] }">▸</span></td>
                <td class="o-mono"><b>{{ f.checker }}</b></td>
                <td>
                  <span class="o-badge" :class="f.passed ? 'o-badge--success' : 'o-badge--danger'">
                    {{ f.passed ? '通过' : '未通过' }}
                  </span>
                </td>
                <td>
                  <span class="o-badge" :class="(SEV_BADGE[f.severity] || SEV_BADGE.info).cls">
                    {{ (SEV_BADGE[f.severity] || SEV_BADGE.info).label }}
                  </span>
                </td>
                <td>{{ f.message }}</td>
              </tr>
              <tr v-if="expanded[f.checker]" class="detail-row">
                <td colspan="5">
                  <div class="find-detail">
                    <div v-if="f.description" class="fd-line">
                      <span class="fd-k">检查项说明</span>{{ f.description }}
                    </div>
                    <div class="fd-line">
                      <span class="fd-k">详细结论</span>{{ f.message || '（无）' }}
                    </div>
                    <div class="fd-rules">
                      <div class="fd-k">关联规则命中（{{ rulesOf(f.checker).length }}）</div>
                      <table v-if="rulesOf(f.checker).length" class="o-table fd-rules-table">
                        <thead>
                          <tr>
                            <th style="width: 200px">规则名</th>
                            <th style="width: 80px">级别</th>
                            <th style="width: 70px">行号</th>
                            <th>命中内容</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="r in rulesOf(f.checker)" :key="`${r.ruleId}-${r.lineNo}`">
                            <td class="o-mono">{{ r.ruleName }}</td>
                            <td>
                              <span class="o-badge" :class="(SEV_BADGE[r.severity] || SEV_BADGE.info).cls">
                                {{ (SEV_BADGE[r.severity] || SEV_BADGE.info).label }}
                              </span>
                            </td>
                            <td class="o-mono o-text-3">{{ r.lineNo || '—' }}</td>
                            <td><div class="o-mono fd-raw">{{ r.matchedText || r.raw }}</div></td>
                          </tr>
                        </tbody>
                      </table>
                      <div v-else class="o-text-3 fd-none">该项未关联到规则命中</div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 智能巡检规则推荐 -->
    <div v-if="task.status === 'done'" class="o-card" style="margin-top: 16px">
      <div class="o-card__title">
        智能巡检规则推荐
        <span class="o-mono o-text-3">
          {{ sugModel ? (sugModel === 'heuristic' ? '启发式评估' : sugModel) : '评估当前规则完善程度' }}
        </span>
      </div>
      <div class="o-row" style="gap: 10px; margin-bottom: 12px; align-items: center; flex-wrap: wrap">
        <button v-if="!sugStreaming" class="o-btn o-btn--primary" @click="startSuggestion">
          {{ sugLoaded ? '↻ 重新评估' : '▶ 智能巡检规则推荐' }}
        </button>
        <button v-else class="o-btn" disabled>评估中 {{ sugProgress }}%</button>
        <span v-if="sugStreaming" class="spin" />
        <span class="o-text-3" style="font-size: 12.5px">
          由大模型评估当前规则覆盖完善程度并推荐可新增的规则项；未配置大模型时自动降级为启发式评估
        </span>
      </div>

      <div v-if="sugErr" class="o-mono" style="color: var(--danger); font-size: 12.5px; margin-bottom: 10px">⚠ {{ sugErr }}</div>

      <template v-if="sugAssessment || sugItems.length">
        <div class="o-form-item">
          <label class="o-form-item__label">完善度评估</label>
          <textarea v-model="sugAssessment" class="o-textarea" rows="5"
                    placeholder="规则完善度评估结论"></textarea>
        </div>
        <div class="sug-head">
          <span class="o-text-3" style="font-size: 12.5px">
            建议规则项（{{ sugItems.length }}），可直接编辑后逐条「接纳」跳转新增
          </span>
        </div>
        <div class="o-table-wrap">
          <table class="o-table">
            <thead>
              <tr>
                <th style="width: 170px">规则名</th>
                <th style="width: 96px">类型</th>
                <th>匹配内容</th>
                <th style="width: 96px">级别</th>
                <th style="width: 170px">建议理由</th>
                <th style="width: 130px"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sugItems.length">
                <td colspan="6"><div class="o-empty" style="padding: 24px 0">未发现明显的规则覆盖缺口</div></td>
              </tr>
              <tr v-for="(it, i) in sugItems" :key="i">
                <td><input v-model="it.name" class="o-input" /></td>
                <td>
                  <select v-model="it.matchType" class="o-select">
                    <option value="keyword">关键字</option>
                    <option value="regex">正则</option>
                  </select>
                </td>
                <td><input v-model="it.pattern" class="o-input mono-input" /></td>
                <td>
                  <select v-model="it.severity" class="o-select">
                    <option value="info">提示</option>
                    <option value="warn">警告</option>
                    <option value="error">错误</option>
                    <option value="critical">严重</option>
                  </select>
                </td>
                <td class="o-text-3 sug-reason">{{ it.reason || '—' }}</td>
                <td class="actions">
                  <button class="o-btn o-btn--primary o-btn--sm" @click="acceptItem(it)">接纳</button>
                  <button class="o-btn o-btn--text o-btn--sm danger" @click="removeItem(i)">忽略</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div v-else-if="!sugStreaming && !sugErr" class="o-text-3" style="font-size: 13px">
        点击上方按钮，让大模型评估当前规则完整度并给出可新增的规则项
      </div>
    </div>
  </template>
  <div v-else class="o-empty o-card">任务加载中…</div>
</template>

<style scoped>
.banner-left .back-btn {
  display: inline-block;
  font-size: 13px;
  color: var(--primary);
  background: var(--primary-soft);
  border-radius: var(--radius);
  padding: 2px 10px;
  margin-bottom: 8px;
}
.banner-left .back-btn:hover {
  background: var(--primary-tint);
}
.banner-left h2 {
  font-size: 19px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
}
.banner-left .meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
  font-size: 12.5px;
  flex-wrap: wrap;
}
.o-banner-stat b.ok { color: var(--success, #00b365); }
.o-banner-stat b.warn { color: var(--warning); }
.o-banner-stat b.bad { color: var(--danger); }
.summary {
  font-size: 13.5px;
  line-height: 1.9;
  color: var(--text-2);
  white-space: pre-wrap;
  background: #fbfcfe;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.spin {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(30, 111, 255, 0.2);
  border-top-color: #1e6fff;
  border-radius: 50%;
  animation: rot 0.8s linear infinite;
  display: inline-block;
  flex: none;
}
@keyframes rot {
  to { transform: rotate(360deg); }
}
.row-clickable {
  cursor: pointer;
}
.row-clickable:hover {
  background: var(--primary-soft, #f2f6ff);
}
.caret {
  display: inline-block;
  color: var(--text-3);
  transition: transform 0.15s;
  font-size: 12px;
}
.caret.open {
  transform: rotate(90deg);
}
.detail-row > td {
  background: #fbfcfe;
  padding: 0 !important;
}
.find-detail {
  padding: 14px 18px 16px;
  border-left: 3px solid var(--primary);
}
.fd-line {
  font-size: 13px;
  line-height: 1.8;
  color: var(--text-2);
  margin-bottom: 6px;
}
.fd-k {
  display: inline-block;
  min-width: 76px;
  margin-right: 8px;
  color: var(--text-3);
  font-size: 12px;
}
.fd-rules {
  margin-top: 10px;
}
.fd-rules-table th {
  font-size: 12px;
  color: var(--text-3);
  font-weight: 500;
}
.fd-rules-table td {
  font-size: 12.5px;
}
.fd-raw {
  font-size: 12px;
  color: var(--text-2);
  word-break: break-all;
  line-height: 1.6;
}
.fd-none {
  font-size: 12.5px;
}
.sug-head {
  margin: 4px 0 10px;
}
.sug-reason {
  font-size: 12px;
  line-height: 1.6;
}
.mono-input {
  font-family: var(--mono);
}
.actions {
  white-space: nowrap;
  text-align: right;
}
.actions .danger {
  color: var(--danger);
}
</style>