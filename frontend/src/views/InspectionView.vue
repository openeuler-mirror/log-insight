<script setup lang="ts">
/* 智能巡检: 当前团队 (顶栏切换) 的巡检与解析任务列表 + 发起巡检 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { useTasksStore, type AnalyzeTask } from '@/stores/tasks'
import { PARSER_DEFS, parserList } from '@/parser/parsers'
import { get, post, apiError } from '@/api/http'
import type { CodeStat } from '@/parser/aggregate'
import { fmtTime, sevBadgeClass, sevLabel, type Severity } from '@/parser/types'
import type { EChartsOption } from 'echarts'
import Icon from '@/components/Icon.vue'
import ChartBase from '@/components/ChartBase.vue'

const rr = useRouter()
const teamsStore = useTeamsStore()
const rbac = useRbacStore()
const tasksStore = useTasksStore()

const page = ref(1)
const PAGE_SIZE = 10
const typeFilter = ref('')      // '' | 'inspection' | 解析器类型
const statusFilter = ref('')
const taskSearch = ref('')
const tasksErr = ref('')
const tasksLoading = ref(false)
const ready = ref(false)
let pollTimer: number | undefined

/* 可切换团队: 平台管理员全部可见, 普通用户仅已加入 */
const availableTeams = computed(() =>
  rbac.isPlatformAdmin
    ? teamsStore.teams
    : teamsStore.teams.filter((t) => !!teamsStore.myRoleOf(t.id)),
)
const team = computed(() => teamsStore.activeTeam)
const teamId = computed(() => teamsStore.activeTeamId)

function ensureActiveTeam() {
  const list = availableTeams.value
  if (!list.length) {
    teamsStore.setActiveTeam(null)
    return
  }
  if (!list.some((t) => t.id === teamsStore.activeTeamId)) teamsStore.setActiveTeam(list[0].id)
}

/* ---------- 任务列表 (服务端分页) ---------- */
function stopTaskPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

async function loadTasks(silent = false) {
  if (!teamId.value) return
  if (!silent) tasksLoading.value = true
  tasksErr.value = ''
  try {
    await tasksStore.fetchTeamTasks(teamId.value, {
      statusFilter: statusFilter.value || undefined,
      taskType: typeFilter.value === 'inspection' ? 'inspection' : typeFilter.value ? 'analyze' : undefined,
      parserType: typeFilter.value && typeFilter.value !== 'inspection' ? typeFilter.value : undefined,
      kw: taskSearch.value.trim() || undefined,
      page: page.value,
      pageSize: PAGE_SIZE,
    })
  } catch (e) {
    tasksErr.value = apiError(e)
  } finally {
    tasksLoading.value = false
    scheduleTaskPoll()
  }
}

/* 有排队/进行中的任务时每 2s 静默刷新进度 */
function scheduleTaskPoll() {
  stopTaskPoll()
  if (tasks.value.some((t) => t.status === 'pending' || t.status === 'running')) {
    pollTimer = window.setTimeout(() => loadTasks(true), 2000)
  }
}

const tasks = computed<AnalyzeTask[]>(() => tasksStore.lastPage?.items ?? [])
const taskTotal = computed(() => tasksStore.lastPage?.total ?? 0)
const totalPages = computed(() => Math.max(1, Math.ceil(taskTotal.value / PAGE_SIZE)))

watch([typeFilter, statusFilter, taskSearch], () => {
  page.value = 1
  loadTasks()
})
watch(() => teamsStore.activeTeamId, () => {
  page.value = 1
  if (ready.value) loadTasks()
})
watch(availableTeams, ensureActiveTeam)
onMounted(async () => {
  if (!teamsStore.teams.length) await teamsStore.fetchTeams().catch(() => {})
  ensureActiveTeam()
  ready.value = true
  await loadTasks()
})
onBeforeUnmount(stopTaskPoll)

function sparkOf(taskId: string): Record<string, any>[] {
  return tasksStore.lastPage?.sparks?.[taskId] ?? []
}
function criticalOf(t: AnalyzeTask): number {
  return sparkOf(t.id).reduce((s, b) => s + (b.critical ?? 0), 0)
}
function errorOf(t: AnalyzeTask): number {
  return sparkOf(t.id).reduce((s, b) => s + (b.error ?? 0), 0)
}

/* 任务状态徽章 (后端 6 态) */
const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  pending: { label: '排队中', cls: 'o-badge--outline' },
  running: { label: '执行中', cls: 'o-badge--brand' },
  done: { label: '已完成', cls: 'o-badge--success' },
  failed: { label: '失败', cls: 'o-badge--danger' },
  cancelled: { label: '已取消', cls: 'o-badge--grey' },
  interrupted: { label: '已中断', cls: 'o-badge--grey' },
}
function statusBadge(s: string) {
  return STATUS_BADGE[s] ?? { label: s, cls: 'o-badge--outline' }
}
function statusLabel(t: AnalyzeTask): string {
  if (t.status === 'running') {
    return `${t.type === 'inspection' ? '巡检中' : '解析中'} ${t.progress}%`
  }
  return statusBadge(t.status).label
}
/* 任务类型: 解析任务展示解析器名, 巡检/诊断任务展示任务类型 */
function taskTypeLabel(t: AnalyzeTask): string {
  if (t.type === 'inspection') return '智能巡检'
  if (t.type === 'diagnosis') return '智能诊断'
  return PARSER_DEFS[t.parserType]?.name ?? t.parserType
}
function taskTypeBadge(t: AnalyzeTask): string {
  if (t.type === 'inspection') return 'o-badge--brand'
  if (t.type === 'diagnosis') return 'o-badge--grey'
  return 'o-badge--outline'
}
function resultLink(t: AnalyzeTask): string {
  return t.type === 'inspection' ? `/inspection/${t.id}` : `/tasks/${t.id}`
}
/* spark / Top 错误码仅解析任务有事件明细 */
function canExpand(t: AnalyzeTask): boolean {
  return t.type === 'analyze'
}

/* ---------- 展开行：spark 图 + Top 错误码 (懒加载 stats) ---------- */
const expanded = ref<string[]>([])
const topCodeCache = ref<Record<string, CodeStat[]>>({})
const statsLoading = ref<Record<string, boolean>>({})

async function toggleExpand(id: string) {
  if (expanded.value.includes(id)) {
    expanded.value = expanded.value.filter((x) => x !== id)
    return
  }
  expanded.value = [...expanded.value, id]
  if (!topCodeCache.value[id]) {
    statsLoading.value[id] = true
    try {
      const stats = await get<{ codes: CodeStat[] }>(`/tasks/${id}/stats`)
      topCodeCache.value[id] = stats.codes.slice(0, 3)
    } catch {
      topCodeCache.value[id] = []
    } finally {
      statsLoading.value[id] = false
    }
  }
}

const SEV_COLORS: Record<Severity, string> = {
  critical: '#ef4444', error: '#f59e0b', warning: '#fbbf24', info: '#1e6fff',
}
function sparkOption(taskId: string): EChartsOption {
  const buckets = sparkOf(taskId)
  const seriesKeys = (['critical', 'error', 'warning'] as const).filter((k) => buckets.some((b) => b[k] > 0))
  return {
    grid: { left: 34, right: 8, top: 8, bottom: 20 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: buckets.map((b) => b.label), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: seriesKeys.map((s) => ({
      name: sevLabel(s),
      type: 'bar',
      stack: 't',
      barMaxWidth: 10,
      itemStyle: { color: SEV_COLORS[s] },
      data: buckets.map((b) => b[s] ?? 0),
    })),
  }
}

const taskErr = ref('')
async function removeTask(taskId: string, taskName: string) {
  if (!window.confirm(`确认删除任务「${taskName}」？删除后不可恢复`)) return
  taskErr.value = ''
  try {
    await tasksStore.removeTask(taskId)
    await loadTasks()
  } catch (e) {
    taskErr.value = apiError(e)
  }
}

function can(perm: string): boolean {
  return teamId.value != null && teamsStore.can(teamId.value, perm)
}

/* ---------- 发起巡检 (归属当前团队) ---------- */
const MAX_FILES = 20
const inspectionOpen = ref(false)
const inspectionFiles = ref<File[]>([])
const inspectionName = ref('')
const inspectionErr = ref('')
const inspectionTip = ref('')
const inspectionSubmitting = ref(false)
const inspectionInput = ref<HTMLInputElement>()

const inspectionSize = computed(() => inspectionFiles.value.reduce((s, f) => s + f.size, 0))

function openInspection() {
  inspectionFiles.value = []
  inspectionName.value = ''
  inspectionErr.value = ''
  inspectionTip.value = ''
  inspectionOpen.value = true
}
function pickInspectionFiles() {
  inspectionInput.value?.click()
}
function addInspectionFiles(e: Event) {
  const input = e.target as HTMLInputElement
  const picked = Array.from(input.files ?? [])
  input.value = ''
  for (const f of picked) {
    if (inspectionFiles.value.length >= MAX_FILES) break
    if (inspectionFiles.value.some((x) => x.name === f.name && x.size === f.size)) continue
    inspectionFiles.value.push(f)
  }
}
function removeInspectionFile(i: number) {
  inspectionFiles.value.splice(i, 1)
}
async function submitInspection() {
  if (!inspectionFiles.value.length || inspectionSubmitting.value || !teamId.value) return
  inspectionSubmitting.value = true
  inspectionErr.value = ''
  try {
    const fd = new FormData()
    for (const f of inspectionFiles.value) fd.append('files', f)
    fd.append('task_type', 'inspection')
    fd.append('task_name', inspectionName.value.trim() ||
      (inspectionFiles.value.length === 1
        ? inspectionFiles.value[0].name.replace(/\.[^.]+$/, '')
        : `${inspectionFiles.value.length} 个日志巡检`))
    fd.append('team_id', String(teamId.value))
    await post<{ taskId: string; status: string }>('/tasks/upload', fd)
    inspectionOpen.value = false
    inspectionTip.value = '巡检任务已创建，正在排队执行'
    await loadTasks()
  } catch (e) {
    inspectionErr.value = apiError(e)
  } finally {
    inspectionSubmitting.value = false
  }
}
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>智能巡检</h2>
    </div>
    <div v-if="team" class="o-operate-right">
      <select v-model="typeFilter" class="o-select">
        <option value="">全部类型</option>
        <option value="inspection">智能巡检</option>
        <option v-for="p in parserList()" :key="p.type" :value="p.type">{{ p.name }}</option>
      </select>
      <select v-model="statusFilter" class="o-select">
        <option value="">全部状态</option>
        <option value="done">已完成</option>
        <option value="running">执行中</option>
        <option value="pending">排队中</option>
        <option value="failed">失败</option>
        <option value="cancelled">已取消</option>
      </select>
      <input v-model="taskSearch" class="o-input" placeholder="搜索任务名称 / 文件" />
      <button v-if="can('task:create')" class="o-btn" @click="openInspection">发起巡检</button>
      <button v-if="can('task:create')" class="o-btn o-btn--primary" @click="rr.push(`/teams/${teamId}/new-task`)">
        + 创建解析任务
      </button>
    </div>
  </div>

  <div v-if="!team" class="o-empty o-card">
    <div class="icon"><Icon name="users" :size="28" /></div>
    <div>暂无可用团队</div>
  </div>

  <template v-else>
    <div v-if="tasksErr || taskErr" class="o-badge o-badge--danger" style="margin-bottom: 12px">{{ tasksErr || taskErr }}</div>
    <div v-if="inspectionTip" class="o-mono inspection-tip"><Icon name="check" :size="13" /> {{ inspectionTip }}</div>

    <div class="o-mono o-text-3" style="font-size: 12.5px; margin-bottom: 10px">
      {{ tasksLoading ? '加载中…' : `共 ${taskTotal} 个任务` }}
    </div>

    <div v-if="!tasks.length && !tasksLoading" class="o-empty">
      <div class="icon"><Icon name="search" :size="28" /></div>
      <div>暂无任务</div>
    </div>

    <div v-else class="o-table-wrap">
      <table class="o-table o-table--sticky-action">
        <thead>
          <tr>
            <th style="width: 30px"></th>
            <th>名称 / 文件</th><th>类型</th><th>事件数</th><th>致命 / 错误</th><th>创建人</th><th>创建时间</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="t in tasks" :key="t.id">
            <tr>
              <td>
                <button
                  v-if="canExpand(t)"
                  class="o-expand-btn"
                  :title="expanded.includes(t.id) ? '收起明细' : '展开明细'"
                  @click="toggleExpand(t.id)"
                >
                  <Icon :name="expanded.includes(t.id) ? 'caret-down' : 'caret-right'" :size="13" />
                </button>
              </td>
              <td>
                <div class="task-name" :title="t.name || t.filename">{{ t.name || t.filename }}</div>
                <div class="task-path o-mono" :title="t.filename">{{ t.filename }}</div>
              </td>
              <td><span class="o-badge" :class="taskTypeBadge(t)">{{ taskTypeLabel(t) }}</span></td>
              <td class="o-mono num">{{ t.type === 'analyze' ? t.eventCount : '—' }}</td>
              <td class="o-mono num">
                <template v-if="t.type === 'analyze'">
                  <span style="color: var(--danger)">{{ criticalOf(t) }}</span>
                  /
                  <span style="color: var(--warning)">{{ errorOf(t) }}</span>
                </template>
                <span v-else>—</span>
              </td>
              <td>{{ t.creatorName || '—' }}</td>
              <td class="o-mono o-text-3">{{ fmtTime(t.createdAt) }}</td>
              <td>
                <span class="o-badge" :class="statusBadge(t.status).cls">{{ statusLabel(t) }}</span>
              </td>
              <td>
                <div class="o-row" style="gap: 2px">
                  <router-link class="o-btn o-btn--sm o-btn--text" :to="resultLink(t)">查看结果</router-link>
                  <button v-if="can('task:delete')" class="o-btn o-btn--sm o-btn--text o-btn--danger" @click="removeTask(t.id, t.name || t.filename)">删除</button>
                </div>
              </td>
            </tr>
            <tr v-if="canExpand(t) && expanded.includes(t.id)" class="o-expand-tr">
              <td></td>
              <td colspan="8">
                <div class="expand-body">
                  <div class="expand-chart">
                    <div class="expand-title">事件时序（按级别堆叠）</div>
                    <ChartBase :option="sparkOption(t.id)" height="150px" />
                  </div>
                  <div class="expand-side">
                    <div class="expand-title">Top 错误码</div>
                    <div v-if="statsLoading[t.id]" class="o-mono o-text-3">统计加载中…</div>
                    <template v-else-if="(topCodeCache[t.id] ?? []).length">
                      <div v-for="c in topCodeCache[t.id]" :key="c.name" class="code-chip">
                        <span class="o-badge" :class="sevBadgeClass(c.severity as Severity)">{{ c.name }}</span>
                        <span class="o-mono o-text-3">{{ c.category }} · {{ c.value }} 次</span>
                      </div>
                    </template>
                    <div v-else class="o-mono o-text-3">暂无错误码统计</div>
                    <router-link class="o-btn o-btn--sm" :to="`/tasks/${t.id}`" style="margin-top: 10px; width: fit-content">
                      <span class="btn-ico">查看完整分析<Icon name="arrow-right" :size="12" /></span>
                    </router-link>
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div class="o-pagination" v-if="totalPages > 1">
      <button :disabled="page <= 1" @click="page--; loadTasks()">‹</button>
      <button v-for="p in totalPages" :key="p" :class="{ active: page === p }" @click="page = p; loadTasks()">{{ p }}</button>
      <button :disabled="page >= totalPages" @click="page++; loadTasks()">›</button>
    </div>

    <!-- 发起巡检弹窗（归属当前团队，规则作用域为通用 ∪ 本团队） -->
    <div v-if="inspectionOpen" class="o-modal-mask" @click.self="inspectionOpen = false">
      <div class="o-modal inspection-modal">
        <div class="o-modal__title">发起智能巡检 — {{ team.name }}</div>
        <div class="o-form-item">
          <label class="o-form-item__label">任务名称</label>
          <input v-model="inspectionName" class="o-input" placeholder="留空则按文件自动命名" />
        </div>
        <input
          ref="inspectionInput"
          type="file"
          multiple
          accept=".zip,.tar,.gz,.tgz,.log,.txt,.json,.jsonl"
          hidden
          @change="addInspectionFiles"
        />
        <div class="o-row" style="justify-content: space-between; margin-bottom: 8px">
          <span class="o-text-3" style="font-size: 13px">日志文件（最多 {{ MAX_FILES }} 个）</span>
          <button class="o-btn o-btn--sm" @click="pickInspectionFiles">＋ 添加日志</button>
        </div>
        <div v-if="!inspectionFiles.length" class="inspection-empty">尚未选择文件</div>
        <ul v-else class="staged-list">
          <li v-for="(f, i) in inspectionFiles" :key="f.name + f.size">
            <span class="o-mono">{{ f.name }}</span>
            <span class="o-mono o-text-3">{{ (f.size / 1024).toFixed(1) }} KB</span>
            <button class="o-btn o-btn--sm o-btn--danger del-x" title="移除" @click="removeInspectionFile(i)">×</button>
          </li>
        </ul>
        <div v-if="inspectionFiles.length" class="o-mono o-text-3" style="font-size: 12px">
          合计 {{ (inspectionSize / 1024).toFixed(1) }} KB
        </div>
        <div v-if="inspectionErr" class="o-badge o-badge--danger">{{ inspectionErr }}</div>
        <div class="o-modal__foot">
          <button class="o-btn" @click="inspectionOpen = false">取消</button>
          <button class="o-btn o-btn--primary" :disabled="inspectionSubmitting || !inspectionFiles.length" @click="submitInspection">
            {{ inspectionSubmitting ? '提交中…' : '发起巡检' }}
          </button>
        </div>
      </div>
    </div>
  </template>
</template>

<style scoped>
.btn-ico {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.inspection-tip {
  color: var(--primary);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.task-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-path {
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.expand-body {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 20px;
}
@media (max-width: 900px) {
  .expand-body { grid-template-columns: 1fr; }
}
.expand-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 8px;
}
.code-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.inspection-modal {
  width: 640px;
}
.inspection-empty {
  padding: 20px 0;
  text-align: center;
  font-size: 13px;
  color: var(--text-3);
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  margin-bottom: 10px;
}
.staged-list {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
  max-height: 220px;
  overflow: auto;
}
.staged-list li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border);
}
.staged-list li:last-child {
  border-bottom: none;
}
.staged-list li > span:first-child {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.del-x {
  font-size: 15px;
  font-weight: 600;
}
</style>
