<script setup lang="ts">
/* 勾选团队要用的模型 + 逐项配置显示名称 */
import { computed, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import {
  discoverTeamModels,
  listTeamModels,
  saveTeamModels,
  setDefaultTeamModel,
  type TeamModel,
} from '@/api/teamLlm'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  open: boolean
  teamId: number
  provider: string
  providerLabel: string
}>()
const emit = defineEmits<{ close: []; changed: [] }>()

interface Item {
  id: string
  name: string
  selected: boolean
  displayName: string
}

const items = ref<Item[]>([])
const keyword = ref('')
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const failed = ref(false)
const found = ref(0)
const manualId = ref('')
const defaultId = ref('')

const shown = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (i) => i.id.toLowerCase().includes(q) || i.name.toLowerCase().includes(q),
  )
})
const selectedCount = computed(() => items.value.filter((i) => i.selected).length)

function reset() {
  items.value = []
  keyword.value = ''
  message.value = ''
  failed.value = false
  found.value = 0
  manualId.value = ''
  defaultId.value = ''
}

async function load() {
  if (!props.open || !props.provider) return
  reset()
  loading.value = true
  let saved: TeamModel[] = []
  try {
    saved = await listTeamModels(props.teamId, props.provider)
  } catch {
    saved = []
  }
  const savedMap = new Map(saved.map((m) => [m.modelId, m]))
  defaultId.value = saved.find((m) => m.isDefault)?.modelId || ''
  try {
    const res = await discoverTeamModels(props.teamId, props.provider)
    found.value = res.models?.length || 0
    message.value = res.message || ''
    failed.value = !res.ok
    const seen = new Set<string>()
    const list: Item[] = []
    for (const m of res.models || []) {
      if (seen.has(m.id)) continue
      seen.add(m.id)
      const row = savedMap.get(m.id)
      list.push({
        id: m.id,
        name: m.name || m.id,
        selected: !!row?.enabled,
        displayName: row?.displayName || m.name || '',
      })
    }
    for (const row of saved) {
      if (seen.has(row.modelId)) continue
      seen.add(row.modelId)
      list.push({
        id: row.modelId,
        name: row.displayName || row.modelId,
        selected: row.enabled,
        displayName: row.displayName || '',
      })
    }
    items.value = list
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
    items.value = saved.map((row) => ({
      id: row.modelId,
      name: row.displayName || row.modelId,
      selected: row.enabled,
      displayName: row.displayName || '',
    }))
  } finally {
    loading.value = false
  }
}

watch(() => [props.open, props.provider], ([open]) => {
  if (open) void load()
}, { immediate: true })

function toggle(item: Item) {
  item.selected = !item.selected
  if (item.selected && !defaultId.value) defaultId.value = item.id
  if (!item.selected && defaultId.value === item.id) defaultId.value = ''
}

function addManual() {
  const id = manualId.value.trim()
  if (!id) return
  if (!items.value.some((i) => i.id === id)) {
    items.value.unshift({ id, name: id, selected: true, displayName: '' })
  }
  const hit = items.value.find((i) => i.id === id)
  if (hit) hit.selected = true
  if (!defaultId.value) defaultId.value = id
  manualId.value = ''
}

async function save() {
  const picked = items.value.filter((i) => i.selected)
  if (!picked.length) {
    message.value = '请至少勾选一个模型'
    failed.value = true
    return
  }
  saving.value = true
  message.value = ''
  failed.value = false
  try {
    const rows = await saveTeamModels(
      props.teamId,
      props.provider,
      picked.map((i, index) => ({
        modelId: i.id,
        displayName: i.displayName.trim(),
        enabled: true,
        sortOrder: index,
      })),
    )
    if (defaultId.value) {
      const target = rows.find((r) => r.modelId === defaultId.value)
      if (target && !target.isDefault) await setDefaultTeamModel(props.teamId, target.id)
    }
    emit('changed')
    emit('close')
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    saving.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="mask" @click.self="emit('close')">
      <div class="dialog" role="dialog" aria-modal="true" aria-label="选择团队模型">
        <div class="head">
          <div>
            <b>选择模型 · {{ providerLabel }}</b>
            <p>从供应商 /models 勾选团队要用的模型，并可为每个模型设置显示名称。</p>
          </div>
          <button class="x" title="关闭" @click="emit('close')"><Icon name="close" :size="14" /></button>
        </div>

        <div class="tools">
          <input v-model="keyword" class="o-input" placeholder="搜索模型（ID 或名称）" />
          <span class="meta">
            {{ loading ? '正在读取…' : `共 ${found || items.length} 个 · 已选 ${selectedCount}` }}
          </span>
        </div>

        <div class="body">
          <div v-if="loading" class="empty">正在从供应商读取模型…</div>
          <div v-else-if="!shown.length" class="empty">
            <p>{{ message || '没有读到模型' }}</p>
            <div class="manual">
              <input v-model="manualId" class="o-input" placeholder="手动输入模型 ID" @keyup.enter="addManual" />
              <button class="o-btn o-btn--sm" @click="addManual">添加</button>
            </div>
          </div>
          <template v-else>
            <div v-for="item in shown" :key="item.id" class="item" :class="{ on: item.selected }">
              <label class="pick">
                <input type="checkbox" :checked="item.selected" @change="toggle(item)" />
                <span class="id">{{ item.id }}</span>
                <span v-if="item.name && item.name !== item.id" class="origname">{{ item.name }}</span>
              </label>
              <div class="name-row">
                <input
                  v-model="item.displayName"
                  class="o-input"
                  :disabled="!item.selected"
                  placeholder="显示名称（留空用模型 ID）"
                />
                <label class="def" :class="{ off: !item.selected }" title="团队默认模型">
                  <input v-model="defaultId" type="radio" :value="item.id" :disabled="!item.selected" />
                  默认
                </label>
              </div>
            </div>
            <div class="manual">
              <input v-model="manualId" class="o-input" placeholder="列表里没有？手动输入模型 ID" @keyup.enter="addManual" />
              <button class="o-btn o-btn--sm" @click="addManual">添加</button>
            </div>
          </template>
        </div>

        <div class="foot">
          <span class="msg" :class="{ err: failed }">{{ message }}</span>
          <button class="o-btn" @click="emit('close')">取消</button>
          <button class="o-btn o-btn--primary" :disabled="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(18, 28, 45, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.dialog {
  width: 640px;
  max-width: 100%;
  max-height: calc(100vh - 40px);
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border);
}
.head b {
  font-size: 15px;
}
.head p {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-3);
}
.x {
  width: 26px;
  height: 26px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
}
.tools {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
}
.tools .o-input {
  flex: 1;
}
.meta {
  flex: none;
  font-size: 12px;
  color: var(--text-3);
}
.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.empty {
  padding: 28px 8px;
  color: var(--text-3);
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}
.item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--radius);
}
.item.on {
  border-color: var(--border);
  background: var(--bg);
}
.pick {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  min-width: 0;
}
.pick .id {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pick .origname {
  font-size: 11.5px;
  color: var(--text-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 22px;
}
.name-row .o-input {
  flex: 1;
  min-width: 0;
  height: 32px;
}
.def {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--text-3);
  cursor: pointer;
}
.def.off {
  opacity: 0.45;
  cursor: not-allowed;
}
.manual {
  display: flex;
  gap: 8px;
  padding: 10px 10px 4px;
}
.manual .o-input {
  flex: 1;
}
.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px 16px;
  border-top: 1px solid var(--border);
}
.msg {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--success);
}
.msg.err {
  color: var(--danger);
}
</style>
