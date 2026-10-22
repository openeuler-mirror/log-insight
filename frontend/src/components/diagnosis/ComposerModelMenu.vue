<script setup lang="ts">
/* 输入框内的模型/推理强度两级菜单 (参考 DSH) */
import { computed, nextTick, ref, watch } from 'vue'
import { fetchTeamModelReasoning } from '@/api/teamLlm'
import type { AgentStatus, CatalogTeam } from '@/api/diagnosis'
import Icon from '@/components/Icon.vue'

interface Entry {
  teamId: number
  teamName: string
  rowId: number
  modelId: string
  name: string
  provider: string
  providerLabel: string
}

const props = defineProps<{
  catalog: CatalogTeam[]
  status: AgentStatus | null
  disabled?: boolean
}>()
const emit = defineEmits<{
  change: [payload: { teamId: number; modelRowId: number; reasoning: string }]
}>()

const open = ref(false)
const pane = ref<'root' | 'model' | 'effort'>('root')
const keyword = ref('')
const levels = ref<{ id: string; name: string }[]>([])
const levelsLoading = ref(false)
const active = ref(0)
const listRef = ref<HTMLElement | null>(null)

const entries = computed<Entry[]>(() =>
  props.catalog.flatMap((team) =>
    team.models.map((m) => ({
      teamId: team.id,
      teamName: team.name,
      rowId: m.rowId,
      modelId: m.modelId,
      name: m.name,
      provider: m.provider,
      providerLabel: m.providerLabel,
    })),
  ),
)

const current = computed<Entry | undefined>(() => {
  const teamId = props.status?.teamId
  if (teamId) {
    // 先对齐正在使用的模型 (历史会话可能不同于团队当前选择), 再回落到团队选择
    const byModel = entries.value.find(
      (e) => e.teamId === teamId && (e.modelId === props.status?.model || e.name === props.status?.modelName),
    )
    if (byModel) return byModel
    const team = props.catalog.find((t) => t.id === teamId)
    const rowId = team?.selection?.modelRowId
    if (team && rowId) {
      const hit = team.models.find((m) => m.rowId === rowId)
      if (hit) return entries.value.find((e) => e.rowId === hit.rowId)
    }
  }
  return entries.value[0]
})

const shown = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter(
    (e) => e.modelId.toLowerCase().includes(q)
      || e.name.toLowerCase().includes(q)
      || e.providerLabel.toLowerCase().includes(q)
      || e.teamName.toLowerCase().includes(q),
  )
})

const effortLabel = computed(() => {
  const value = props.status?.reasoning || ''
  if (!value) return '默认'
  return levels.value.find((l) => l.id === value)?.name || value
})

const modelLabel = computed(() => props.status?.modelName || props.status?.model || '选择模型')
const empty = computed(() => !entries.value.length)
const grouped = computed(() => {
  const out: { key: string; teamName: string; providerLabel: string; items: Entry[] }[] = []
  for (const entry of shown.value) {
    const key = `${entry.teamId}:${entry.provider}`
    const last = out[out.length - 1]
    if (last && last.key === key) last.items.push(entry)
    else out.push({ key, teamName: entry.teamName, providerLabel: entry.providerLabel, items: [entry] })
  }
  return out
})

async function loadLevels(entry?: Entry) {
  if (!entry) {
    levels.value = []
    return
  }
  levelsLoading.value = true
  try {
    const res = await fetchTeamModelReasoning(entry.teamId, entry.rowId)
    levels.value = res.levels || []
  } catch {
    levels.value = []
  } finally {
    levelsLoading.value = false
  }
}

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    pane.value = 'root'
    keyword.value = ''
    active.value = 0
    await loadLevels(current.value)
  }
}

function close() {
  open.value = false
  pane.value = 'root'
}

function pickModel(entry: Entry) {
  emit('change', { teamId: entry.teamId, modelRowId: entry.rowId, reasoning: '' })
  close()
}

function pickEffort(value: string) {
  if (!current.value) return
  emit('change', {
    teamId: current.value.teamId,
    modelRowId: current.value.rowId,
    reasoning: value,
  })
  close()
}

function openPane(next: 'model' | 'effort') {
  pane.value = next
  active.value = 0
  keyword.value = ''
  if (next === 'effort') void loadLevels(current.value)
  nextTick(() => listRef.value?.focus())
}

function move(step: number) {
  const max = pane.value === 'model'
    ? shown.value.length - 1
    : levels.value.length
  if (max < 0) return
  active.value = Math.min(max, Math.max(0, active.value + step))
}

function onKeydown(e: KeyboardEvent) {
  if (!open.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    if (pane.value === 'root') close()
    else pane.value = 'root'
    return
  }
  if (e.key === 'Tab' && e.shiftKey && pane.value !== 'root') {
    e.preventDefault()
    pane.value = 'root'
    return
  }
  if (pane.value === 'root') {
    if (e.key === 'ArrowDown') { e.preventDefault(); openPane('model') }
    return
  }
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
  else if (e.key === 'Enter') {
    e.preventDefault()
    if (pane.value === 'model') {
      const entry = shown.value[active.value]
      if (entry) pickModel(entry)
    } else {
      const level = active.value === 0 ? '' : levels.value[active.value - 1]?.id
      if (level !== undefined) pickEffort(level || '')
    }
  }
}

watch(() => props.catalog, () => {
  if (open.value) void loadLevels(current.value)
})
</script>

<template>
  <div class="menu-wrap" :class="{ disabled }" @keydown="onKeydown">
    <button
      class="trigger"
      type="button"
      :title="empty ? '团队尚未启用模型' : '选择模型与推理强度'"
      :disabled="disabled || empty"
      @click="toggle"
    >
      <span class="model">{{ modelLabel }}</span>
      <span v-if="status?.reasoning" class="effort">{{ effortLabel }}</span>
      <span class="chev" :class="{ open }"><Icon name="caret-down" :size="12" /></span>
    </button>

    <div v-if="open" class="backdrop" @click="close"></div>
    <div v-if="open" class="menu" :class="`pane-${pane}`">
      <template v-if="pane === 'root'">
        <button class="row root-row" type="button" @click="openPane('model')">
          <span class="row-label">模型</span>
          <span class="row-value">{{ modelLabel }}</span>
          <span class="row-chev"><Icon name="chevron-right" :size="12" /></span>
        </button>
        <button class="row root-row" type="button" :disabled="!current" @click="openPane('effort')">
          <span class="row-label">推理强度</span>
          <span class="row-value">{{ effortLabel }}</span>
          <span class="row-chev"><Icon name="chevron-right" :size="12" /></span>
        </button>
      </template>

      <template v-else-if="pane === 'model'">
        <div class="pane-head">
          <button class="back" type="button" @click="pane = 'root'"><Icon name="chevron-left" :size="12" />模型</button>
          <input
            v-if="entries.length > 4"
            v-model="keyword"
            class="search"
            placeholder="搜索模型或团队"
            @input="active = 0"
          />
        </div>
        <div ref="listRef" class="list" tabindex="-1">
          <div v-if="!shown.length" class="empty">没有匹配的模型</div>
          <template v-for="group in grouped" :key="group.key">
            <div class="group-head">{{ group.teamName }} · {{ group.providerLabel }}</div>
            <button
              v-for="entry in group.items"
              :key="entry.rowId"
              type="button"
              class="row"
              :class="{ on: entry.rowId === current?.rowId }"
              @click="pickModel(entry)"
            >
              <span class="row-main">
                <span class="name">{{ entry.name }}</span>
                <span v-if="entry.name !== entry.modelId" class="mid">{{ entry.modelId }}</span>
              </span>
              <span v-if="entry.rowId === current?.rowId" class="tick"><Icon name="check" :size="13" /></span>
            </button>
          </template>
        </div>
      </template>

      <template v-else>
        <div class="pane-head">
          <button class="back" type="button" @click="pane = 'root'"><Icon name="chevron-left" :size="12" />推理强度</button>
        </div>
        <div ref="listRef" class="list" tabindex="-1">
          <div v-if="levelsLoading" class="empty">正在读取模型的推理能力…</div>
          <template v-else>
            <button
              type="button"
              class="row"
              :class="{ on: !status?.reasoning }"
              @click="pickEffort('')"
            >
              <span class="row-main"><span class="name">默认</span></span>
              <span v-if="!status?.reasoning" class="tick"><Icon name="check" :size="13" /></span>
            </button>
            <button
              v-for="level in levels.filter((l) => l.id)"
              :key="level.id"
              type="button"
              class="row"
              :class="{ on: status?.reasoning === level.id }"
              @click="pickEffort(level.id)"
            >
              <span class="row-main"><span class="name">{{ level.name || level.id }}</span></span>
              <span v-if="status?.reasoning === level.id" class="tick"><Icon name="check" :size="13" /></span>
            </button>
            <div v-if="!levels.length" class="empty">该模型未标注推理档位</div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.menu-wrap {
  position: relative;
  flex: none;
}
.trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 260px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.trigger:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.trigger:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.model {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.effort {
  flex: none;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 10.5px;
}
.chev {
  opacity: 0.6;
  display: inline-flex;
  align-items: center;
  transition: transform 0.15s;
}
.chev.open {
  transform: rotate(180deg);
}
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 41;
  width: 380px;
  max-width: calc(100vw - 40px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  padding: 6px;
  display: flex;
  flex-direction: column;
  max-height: 60vh;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-1);
  font: inherit;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}
.row:hover,
.row.on {
  background: var(--bg);
}
.root-row .row-label {
  flex: none;
  color: var(--text-3);
  font-size: 12px;
  width: 68px;
}
.root-row .row-value {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.row-chev {
  color: var(--text-4);
}
.pane-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 6px 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.back {
  flex: none;
  border: none;
  background: transparent;
  color: var(--primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.search {
  flex: 1;
  min-width: 0;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-1);
  font: inherit;
  font-size: 12px;
  outline: none;
}
.list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  outline: none;
}
.group-head {
  padding: 8px 10px 4px;
  font-size: 10.5px;
  letter-spacing: 0.02em;
  color: var(--text-4);
  position: sticky;
  top: 0;
  background: var(--surface);
}
.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.name {
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mid {
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--text-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tick {
  flex: none;
  color: var(--primary);
  display: inline-flex;
  align-items: center;
}
.empty {
  padding: 18px 10px;
  color: var(--text-4);
  font-size: 12px;
  text-align: center;
}
</style>
