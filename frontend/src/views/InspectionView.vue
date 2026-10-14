<script setup lang="ts">
/* 智能巡检: 上传日志包发起巡检 (规则引擎优先 + LLM 总结) + 任务列表实时进度 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { get, post, del, apiError } from '@/api/http'
import { fmtTime } from '@/parser/types'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'

interface InspectionTask {
  id: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
  filename: string
  name?: string
  progress: number
  error?: string
  createdAt: string
  finishedAt?: string
  logSize: number
  eventCount: number
  creatorName: string
}

const STATUS_BADGE: Record<InspectionTask['status'], { label: string; cls: string }> = {
  pending: { label: '排队中', cls: 'o-badge--grey' },
  running: { label: '巡检中', cls: 'o-badge--warning' },
  done: { label: '已完成', cls: 'o-badge--success' },
  failed: { label: '失败', cls: 'o-badge--danger' },
  cancelled: { label: '已取消', cls: 'o-badge--grey' },
  interrupted: { label: '已中断', cls: 'o-badge--danger' },
}

const tasks = ref<InspectionTask[]>([])
const loading = ref(false)
const loadErr = ref('')
const uploadTip = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement>()   // 多选: 一次选多个日志
let pollTimer: number | undefined

/* 待巡检文件暂存区: 「上传日志」加入, 「发起巡检」将暂存多文件合并为一个任务提交 */
const MAX_FILES = 20
const pendingFiles = ref<File[]>([])
const pendingSize = computed(() => pendingFiles.value.reduce((s, f) => s + f.size, 0))
function addFiles(e: Event) {
  const input = e.target as HTMLInputElement
  const picked = Array.from(input.files ?? [])
  input.value = ''
  for (const f of picked) {
    if (pendingFiles.value.length >= MAX_FILES) break
    if (pendingFiles.value.some((x) => x.name === f.name && x.size === f.size)) continue
    pendingFiles.value.push(f)
  }
}
function removePending(i: number) {
  pendingFiles.value.splice(i, 1)
}

/* 巡检规则作用团队 (可选): 决定本次巡检生效的「通用 ∪ 本团队」规则集 */
const teamsStore = useTeamsStore()
const rbac = useRbacStore()
const uploadTeamId = ref<number | null>(null)
const myTeams = computed(() => teamsStore.teams.filter(
  t => rbac.isPlatformAdmin || teamsStore.myRoleOf(t.id)))

const hasActive = computed(() =>
  tasks.value.some((t) => t.status === 'pending' || t.status === 'running'),
)

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const data = await get<InspectionTask[]>('/tasks', { type: 'inspection', limit: 50 })
    tasks.value = data
    loadErr.value = ''
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    loading.value = false
  }
  schedulePoll()
}

/* 有排队/运行中任务时每 2s 轮询刷新进度 */
function schedulePoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = undefined
  }
  if (hasActive.value) {
    pollTimer = setTimeout(() => load(true), 2000)
  }
}

function pickFiles() {
  fileInput.value?.click()
}

/* 发起巡检: 将暂存的多文件作为一个任务提交 (后端遍历解析, 合并成一次巡检) */
async function startInspection() {
  if (!pendingFiles.value.length || uploading.value) return
  uploading.value = true
  uploadTip.value = ''
  loadErr.value = ''
  try {
    const fd = new FormData()
    for (const f of pendingFiles.value) fd.append('files', f)
    fd.append('task_type', 'inspection')
    fd.append('task_name', pendingFiles.value.length === 1
      ? pendingFiles.value[0].name.replace(/\.[^.]+$/, '')
      : `${pendingFiles.value.length} 个日志巡检`)
    if (uploadTeamId.value != null) fd.append('team_id', String(uploadTeamId.value))
    const data = await post<{ taskId: string; status: string }>('/tasks/upload', fd)
    uploadTip.value = `巡检任务已创建（${data.taskId.slice(0, 8)}…），正在排队执行`
    pendingFiles.value = []
    await load(true)
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    uploading.value = false
  }
}

/* 删除自己的任务 (× ): 已结束/进行中均可删, 进行中会同时请求 Worker 停止 */
async function removeTask(t: InspectionTask) {
  if (!window.confirm(`确认删除任务「${t.name || t.filename}」？删除后不可恢复`)) return
  loadErr.value = ''
  try {
    await del(`/tasks/${t.id}`)
    await load(true)
  } catch (e) {
    loadErr.value = apiError(e)
  }
}

onMounted(() => {
  load()
  teamsStore.fetchTeams().catch(() => { /* 团队列表不可用时仅用通用规则 */ })
})
onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>智能巡检</h2>
      <span class="o-mono o-text-3">规则引擎优先匹配，LLM 智能化总结（未配置时降级纯规则）</span>
    </div>
    <div class="o-operate-right">
      <select v-model="uploadTeamId" class="o-select" style="width: 190px" title="选择本次巡检生效的团队规则作用域（可选）">
        <option :value="null">仅通用规则</option>
        <option v-for="t in myTeams" :key="t.id" :value="t.id">{{ t.name }} 专属规则</option>
      </select>
      <input ref="fileInput" type="file" multiple accept=".zip,.tar,.gz,.tgz,.log,.txt,.json,.jsonl" hidden @change="addFiles" />
      <button class="o-btn" @click="pickFiles">＋ 上传日志</button>
      <button class="o-btn o-btn--primary" :disabled="uploading || !pendingFiles.length" @click="startInspection">
        {{ uploading ? '提交中…' : `发起巡检${pendingFiles.length ? `（${pendingFiles.length}）` : ''}` }}
      </button>
    </div>
  </div>

  <div v-if="uploadTip" class="o-mono" style="color: var(--primary); margin-bottom: 12px">✓ {{ uploadTip }}</div>
  <div v-if="loadErr" class="o-mono" style="color: var(--danger); margin-bottom: 12px">⚠ {{ loadErr }}</div>

  <div v-if="pendingFiles.length" class="o-card">
    <div class="o-card__title">
      待巡检日志
      <span class="o-mono o-text-3">{{ pendingFiles.length }} 个文件 · 合计 {{ (pendingSize / 1024).toFixed(1) }} KB · 将合并为一个巡检任务</span>
    </div>
    <ul class="staged-list">
      <li v-for="(f, i) in pendingFiles" :key="f.name + f.size">
        <span class="o-mono">{{ f.name }}</span>
        <span class="o-mono o-text-3">{{ (f.size / 1024).toFixed(1) }} KB</span>
        <button class="o-btn o-btn--sm o-btn--danger del-x" title="移除" @click="removePending(i)">×</button>
      </li>
    </ul>
  </div>

  <div class="o-card">
    <div class="o-card__title">
      巡检任务
      <span class="o-mono o-text-3">{{ tasks.length }} 条 · 仅显示自己发起的巡检（管理员可见全部）</span>
    </div>
    <div class="o-table-wrap">
      <table class="o-table">
        <thead>
          <tr>
            <th>任务</th><th>状态</th><th style="width: 200px">进度</th><th>日志大小</th><th>事件数</th><th>创建时间</th><th>完成时间</th><th style="width: 96px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!tasks.length && !loading">
            <td colspan="8">
              <div class="o-empty" style="padding: 48px 0">
                <div class="icon">🔍</div>
                <div>暂无巡检任务 — 点击「上传日志」选择一个或多个日志后「发起巡检」</div>
              </div>
            </td>
          </tr>
          <tr v-for="t in tasks" :key="t.id">
            <td>
              <b>{{ t.name || t.filename }}</b>
              <div class="o-mono o-text-3">{{ t.id }}</div>
            </td>
            <td>
              <span class="o-badge" :class="STATUS_BADGE[t.status].cls">{{ STATUS_BADGE[t.status].label }}</span>
              <div v-if="t.error" class="o-mono err">{{ t.error }}</div>
            </td>
            <td>
              <div class="progress">
                <div
                  class="progress-bar"
                  :class="{ bad: t.status === 'failed' || t.status === 'interrupted' }"
                  :style="{ width: (t.status === 'done' ? 100 : t.progress) + '%' }"
                ></div>
              </div>
              <span class="o-mono o-text-3">{{ t.status === 'done' ? 100 : t.progress }}%</span>
            </td>
            <td class="o-mono">{{ t.logSize ? (t.logSize / 1024).toFixed(1) + ' KB' : '—' }}</td>
            <td class="o-mono">{{ t.eventCount || '—' }}</td>
            <td class="o-mono o-text-3">{{ fmtTime(t.createdAt) }}</td>
            <td class="o-mono o-text-3">{{ t.finishedAt ? fmtTime(t.finishedAt) : '—' }}</td>
            <td class="actions">
              <router-link class="o-btn o-btn--sm" :to="`/inspection/${t.id}`">查看详情</router-link>
              <button class="o-btn o-btn--sm o-btn--danger del-x" title="删除任务" @click="removeTask(t)">×</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.err {
  color: var(--danger);
  font-size: 11.5px;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.progress {
  display: inline-block;
  width: 120px;
  height: 6px;
  border-radius: 3px;
  background: var(--border);
  overflow: hidden;
  vertical-align: middle;
  margin-right: 8px;
}
.progress-bar {
  height: 100%;
  border-radius: 3px;
  background: var(--primary);
  transition: width 0.4s ease;
}
.progress-bar.bad {
  background: var(--danger);
}
.staged-list {
  list-style: none;
  margin: 0;
  padding: 0;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.del-x {
  font-size: 15px;
  font-weight: 600;
}
.actions {
  white-space: nowrap;
}
/* 操作列两个按钮统一高度, 宽度按内容自适应 */
.actions .o-btn {
  height: 24px;
  padding: 0 10px;
}
.actions .o-btn + .o-btn {
  margin-left: 6px;
}
</style>
