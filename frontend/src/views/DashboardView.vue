<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { get, apiError } from '@/api/http'
import { useRbacStore } from '@/stores/rbac'
import { useTeamsStore } from '@/stores/teams'
import { PARSER_DEFS } from '@/parser/parsers'
import { fmtTime } from '@/parser/types'
import type { AnalyzeTask } from '@/stores/tasks'
import type { EChartsOption } from 'echarts'
import ChartBase from '@/components/ChartBase.vue'
import Icon from '@/components/Icon.vue'

const route = useRoute()
const rbac = useRbacStore()
const teamsStore = useTeamsStore()

const denied = computed(() => (route.query.denied as string) || '')

interface DashboardData {
  tasks: number
  taskTypes: Record<string, number>
  kpi: { total: number; critical: number; error: number; warning: number }
  recentTasks: AnalyzeTask[]
  topCodes: { name: string; value: number }[]
}

const data = ref<DashboardData | null>(null)
const loadErr = ref('')
const loading = ref(false)
const ready = ref(false)

/* 工作台与顶栏当前团队同作用域 */
const team = computed(() => teamsStore.activeTeam)
const teamId = computed(() => teamsStore.activeTeamId)
const availableTeams = computed(() =>
  rbac.isPlatformAdmin
    ? teamsStore.teams
    : teamsStore.teams.filter((t) => !!teamsStore.myRoleOf(t.id)),
)

function ensureActiveTeam() {
  const list = availableTeams.value
  if (!list.length) {
    teamsStore.setActiveTeam(null)
    return
  }
  if (!list.some((t) => t.id === teamsStore.activeTeamId)) teamsStore.setActiveTeam(list[0].id)
}

async function load() {
  if (!teamId.value) return
  loading.value = true
  loadErr.value = ''
  try {
    data.value = await get<DashboardData>('/dashboard', { team_id: teamId.value })
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    loading.value = false
  }
}

watch(() => teamsStore.activeTeamId, () => {
  if (ready.value) load()
})

onMounted(async () => {
  if (!teamsStore.teams.length) await teamsStore.fetchTeams().catch(() => {})
  ensureActiveTeam()
  ready.value = true
  await load()
})

const codeChart = computed<EChartsOption>(() => {
  const top = data.value?.topCodes ?? []
  return {
    grid: { left: 70, right: 30, top: 16, bottom: 28 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: top.map((t) => t.name).reverse(), axisLabel: { fontFamily: 'Menlo, Consolas, monospace' } },
    series: [
      {
        type: 'bar',
        data: top.map((t) => t.value).reverse(),
        itemStyle: { color: '#1e6fff' },
        barMaxWidth: 18,
        label: { show: true, position: 'right' },
      },
    ],
  }
})

function taskTypeLabel(t: AnalyzeTask): string {
  if (t.type === 'inspection') return '智能巡检'
  if (t.type === 'diagnosis') return '智能诊断'
  return PARSER_DEFS[t.parserType]?.name ?? '解析任务'
}
function taskTypeBadge(t: AnalyzeTask): string {
  if (t.type === 'inspection') return 'o-badge--brand'
  if (t.type === 'diagnosis') return 'o-badge--grey'
  return 'o-badge--outline'
}
function resultLink(t: AnalyzeTask): string {
  return t.type === 'inspection' ? `/inspection/${t.id}` : `/tasks/${t.id}`
}

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
  const badge = statusBadge(t.status)
  return t.status === 'running' ? `${badge.label} ${t.progress}%` : badge.label
}
</script>

<template>
  <div class="o-badge o-badge--danger" v-if="denied" style="margin-bottom: 16px">
    无权访问「{{ denied }}」—— 当前角色未授予该菜单权限
  </div>

  <div class="o-page-head">
    <h2>
      工作台
      <span v-if="team" class="o-badge o-badge--outline">{{ team.name }}</span>
    </h2>
    <button class="o-btn o-btn--sm" :disabled="loading || !teamId" @click="load">
      <span class="btn-ico"><Icon name="refresh" :size="13" />{{ loading ? '刷新中…' : '刷新' }}</span>
    </button>
  </div>

  <div v-if="!teamId" class="o-empty o-card">
    <div class="icon"><Icon name="users" :size="28" /></div>
    <div>尚未进入团队</div>
    <button class="o-btn o-btn--primary" @click="teamsStore.openTeamBrowser()">浏览团队</button>
  </div>

  <div v-else-if="loadErr" class="o-empty o-card">
    <div class="icon"><Icon name="alert" :size="28" /></div>
    <div>{{ loadErr }}</div>
  </div>

  <template v-else-if="data">
    <div class="o-kpis" style="margin-bottom: 20px">
      <div class="o-kpi">
        <b>{{ data.tasks }}</b>
        <span>任务总数 · 巡检 {{ data.taskTypes.inspection ?? 0 }} / 解析 {{ data.taskTypes.analyze ?? 0 }} / 诊断 {{ data.taskTypes.diagnosis ?? 0 }}</span>
      </div>
      <div class="o-kpi"><b>{{ data.kpi.total }}</b><span>日志事件总量</span></div>
      <div class="o-kpi"><b style="color: var(--danger)">{{ data.kpi.critical }}</b><span>致命事件</span></div>
      <div class="o-kpi"><b style="color: var(--warning)">{{ data.kpi.error }}</b><span>错误事件</span></div>
    </div>

    <div class="grid">
      <div class="o-card">
        <div class="o-card__title">错误码 Top 8</div>
        <ChartBase v-if="data.topCodes.length" :option="codeChart" height="320px" />
        <div v-else class="o-empty" style="padding: 60px 0">
          <div class="icon"><Icon name="search" :size="28" /></div>
          <div>暂无数据</div>
        </div>
      </div>

      <div class="o-card">
        <div class="o-card__title">近期任务</div>
        <table class="o-table">
          <thead>
            <tr><th>任务</th><th>类型</th><th>状态</th><th>事件数</th><th>时间</th></tr>
          </thead>
          <tbody>
            <tr v-if="!data.recentTasks.length">
              <td colspan="5"><div class="o-empty" style="padding: 36px 0">暂无任务</div></td>
            </tr>
            <tr v-for="t in data.recentTasks" :key="t.id">
              <td>
                <router-link :to="resultLink(t)">{{ t.name || t.filename }}</router-link>
                <div class="o-mono o-text-3">{{ t.creatorName }}</div>
              </td>
              <td><span class="o-badge" :class="taskTypeBadge(t)">{{ taskTypeLabel(t) }}</span></td>
              <td><span class="o-badge" :class="statusBadge(t.status).cls">{{ statusLabel(t) }}</span></td>
              <td class="o-mono">{{ t.eventCount }}</td>
              <td class="o-mono o-text-3">{{ fmtTime(t.createdAt) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>
</template>

<style scoped>
.btn-ico {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.grid {
  display: grid;
  grid-template-columns: 5fr 7fr;
  gap: 16px;
}
@media (max-width: 1080px) {
  .grid { grid-template-columns: 1fr; }
}
</style>
