<script setup lang="ts">
/* 团队大模型用量: 按人 / 按模型 / 按天 */
import { computed, onMounted, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import { fetchMyTeamUsage, fetchTeamUsage, type UsageRow } from '@/api/teamLlm'
import Icon from '@/components/Icon.vue'

const props = defineProps<{ teamId: number; canViewAll: boolean }>()

type GroupBy = 'user' | 'model' | 'day'
const groupBy = ref<GroupBy>('user')
const start = ref('')
const end = ref('')
const rows = ref<UsageRow[]>([])
const loading = ref(false)
const error = ref('')

const groups: { key: GroupBy; label: string }[] = [
  { key: 'user', label: '按成员' },
  { key: 'model', label: '按模型' },
  { key: 'day', label: '按天' },
]

const totals = computed(() => rows.value.reduce(
  (acc, row) => ({
    requests: acc.requests + (row.requests || 0),
    inputTokens: acc.inputTokens + (row.inputTokens || 0),
    outputTokens: acc.outputTokens + (row.outputTokens || 0),
    thoughtTokens: acc.thoughtTokens + (row.thoughtTokens || 0),
    totalTokens: acc.totalTokens + (row.totalTokens || 0),
  }),
  { requests: 0, inputTokens: 0, outputTokens: 0, thoughtTokens: 0, totalTokens: 0 },
))

function fmt(n: number | undefined): string {
  return (n || 0).toLocaleString('en-US')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = props.canViewAll
      ? await fetchTeamUsage(props.teamId, { start: start.value, end: end.value, groupBy: groupBy.value })
      : await fetchMyTeamUsage(props.teamId, { start: start.value, end: end.value })
    rows.value = res.rows || []
  } catch (e) {
    error.value = apiError(e)
    rows.value = []
  } finally {
    loading.value = false
  }
}

watch([groupBy, () => props.teamId], () => { void load() })
onMounted(() => { void load() })
</script>

<template>
  <div class="usage">
    <div class="usage-head">
      <div>
        <h3>模型用量</h3>
        <p>{{ canViewAll ? '团队成员在不同模型上的调用次数与 token 消耗。' : '你在此团队下的模型调用与 token 消耗。' }}</p>
      </div>
      <div class="filters">
        <input v-model="start" class="o-input" type="date" title="开始日期" />
        <span class="sep">至</span>
        <input v-model="end" class="o-input" type="date" title="结束日期" />
        <button class="o-btn o-btn--sm" :disabled="loading" @click="load">
          {{ loading ? '查询中…' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="canViewAll" class="tabs">
      <button
        v-for="g in groups"
        :key="g.key"
        :class="{ on: groupBy === g.key }"
        @click="groupBy = g.key"
      >
        {{ g.label }}
      </button>
    </div>

    <div class="summary">
      <div><b>{{ fmt(totals.requests) }}</b><span>调用次数</span></div>
      <div><b>{{ fmt(totals.inputTokens) }}</b><span>输入 token</span></div>
      <div><b>{{ fmt(totals.outputTokens) }}</b><span>输出 token</span></div>
      <div><b>{{ fmt(totals.thoughtTokens) }}</b><span>思考 token</span></div>
      <div><b>{{ fmt(totals.totalTokens) }}</b><span>合计 token</span></div>
    </div>

    <div v-if="error" class="err"><Icon name="alert" :size="13" /> {{ error }}</div>

    <div class="o-card">
      <div class="o-table-wrap">
        <table class="o-table">
          <thead>
            <tr>
              <th v-if="groupBy === 'user' && canViewAll">成员</th>
              <th v-if="groupBy === 'user' || groupBy === 'model'">供应商</th>
              <th v-if="groupBy === 'user' || groupBy === 'model'">模型</th>
              <th v-if="groupBy === 'day'">日期</th>
              <th>调用</th>
              <th>输入</th>
              <th>输出</th>
              <th>缓存读</th>
              <th>思考</th>
              <th>合计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!rows.length && !loading">
              <td colspan="10"><div class="o-empty" style="padding: 40px 0">暂无用量记录</div></td>
            </tr>
            <tr v-for="(row, i) in rows" :key="i">
              <td v-if="groupBy === 'user' && canViewAll"><b>{{ row.userName || `用户#${row.userId}` }}</b></td>
              <td v-if="groupBy === 'user' || groupBy === 'model'" class="o-mono">{{ row.provider }}</td>
              <td v-if="groupBy === 'user' || groupBy === 'model'">
                {{ row.modelName || row.model }}
                <span class="o-text-3 mono">{{ row.model }}</span>
              </td>
              <td v-if="groupBy === 'day'" class="o-mono">{{ row.day }}</td>
              <td class="o-mono">{{ fmt(row.requests) }}</td>
              <td class="o-mono">{{ fmt(row.inputTokens) }}</td>
              <td class="o-mono">{{ fmt(row.outputTokens) }}</td>
              <td class="o-mono">{{ fmt(row.cachedReadTokens) }}</td>
              <td class="o-mono">{{ fmt(row.thoughtTokens) }}</td>
              <td class="o-mono"><b>{{ fmt(row.totalTokens) }}</b></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.usage {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.usage-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.usage-head h3 {
  font-size: 15px;
}
.usage-head p {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-3);
}
.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.filters .o-input {
  width: 150px;
}
.sep {
  font-size: 12px;
  color: var(--text-4);
}
.tabs {
  display: flex;
  gap: 6px;
}
.tabs button {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  font: inherit;
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
}
.tabs button.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
.summary div {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.summary b {
  font-size: 18px;
}
.summary span {
  font-size: 11.5px;
  color: var(--text-3);
}
.err {
  color: var(--danger);
  font-size: 12.5px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.mono {
  font-size: 10.5px;
  display: block;
}
</style>
