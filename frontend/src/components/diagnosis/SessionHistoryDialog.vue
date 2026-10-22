<script setup lang="ts">
/* 历史会话: 按数据域展示 (我的 + 所在团队), 支持搜索/重命名/删除/继续 */
import { computed, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import { deleteSession, listSessions, renameSession, type SessionInfo } from '@/api/diagnosis'
import { useRbacStore } from '@/stores/rbac'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  open: boolean
  activeId: string
  teams: { id: number; name: string }[]
}>()
const emit = defineEmits<{
  close: []
  select: [session: SessionInfo]
  changed: []
}>()

const rbac = useRbacStore()
const rows = ref<SessionInfo[]>([])
const loading = ref(false)
const err = ref('')
const keyword = ref('')
const mineOnly = ref(false)
const teamFilter = ref<number | 0>(0)
const editingId = ref('')
const editingTitle = ref('')
const busy = ref('')

const shown = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return rows.value.filter((s) => {
    if (mineOnly.value && s.userId !== rbac.currentUserId) return false
    if (teamFilter.value && s.teamId !== teamFilter.value) return false
    if (!q) return true
    return [s.title, s.teamName, s.ownerName, s.modelName, s.model]
      .some((v) => (v || '').toLowerCase().includes(q))
  })
})

async function load() {
  loading.value = true
  err.value = ''
  try {
    rows.value = await listSessions()
  } catch (e) {
    err.value = apiError(e)
  } finally {
    loading.value = false
  }
}

watch(() => props.open, (open) => {
  if (!open) {
    editingId.value = ''
    return
  }
  keyword.value = ''
  teamFilter.value = 0
  void load()
})

function isMine(s: SessionInfo) {
  return s.userId === rbac.currentUserId
}

function ownerLabel(s: SessionInfo) {
  return isMine(s) ? '我' : (s.ownerName || '成员')
}

function fmtTime(value?: string) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return `今天 ${hm}`
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === now.getFullYear() ? `${md} ${hm}` : `${d.getFullYear()}-${md}`
}

function pick(s: SessionInfo) {
  if (busy.value || editingId.value) return
  emit('select', s)
}

function startRename(s: SessionInfo) {
  editingId.value = s.id
  editingTitle.value = s.title || ''
}

async function saveRename(s: SessionInfo) {
  const title = editingTitle.value.trim()
  if (!title) {
    editingId.value = ''
    return
  }
  busy.value = s.id
  try {
    const updated = await renameSession(s.id, title)
    s.title = updated.title
    editingId.value = ''
    emit('changed')
  } catch (e) {
    err.value = apiError(e)
  } finally {
    busy.value = ''
  }
}

async function remove(s: SessionInfo) {
  if (!window.confirm(`删除会话「${s.title || '新的诊断'}」？聊天记录会一起删除。`)) return
  busy.value = s.id
  try {
    await deleteSession(s.id)
    rows.value = rows.value.filter((r) => r.id !== s.id)
    emit('changed')
  } catch (e) {
    err.value = apiError(e)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div v-if="open" class="o-modal-mask" @click.self="emit('close')">
    <div class="o-modal hist">
      <div class="hist-head">
        <b>历史会话</b>
        <span class="hist-count">{{ shown.length }}</span>
        <button class="hist-icon hist-close" title="关闭" @click="emit('close')"><Icon name="close" :size="14" /></button>
      </div>

      <div class="hist-tools">
        <input v-model="keyword" class="o-input" placeholder="搜索标题、团队或模型" />
        <select v-model="teamFilter" class="o-select">
          <option :value="0">全部团队</option>
          <option v-for="t in teams" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
        <label class="hist-mine">
          <input v-model="mineOnly" type="checkbox" />
          只看我的
        </label>
      </div>

      <p v-if="err" class="hist-err">{{ err }}</p>

      <div class="hist-list">
        <div v-if="loading" class="hist-empty">正在加载…</div>
        <div v-else-if="!shown.length" class="hist-empty">没有符合条件的会话</div>
        <template v-else>
        <div
          v-for="s in shown"
          :key="s.id"
          class="hist-row"
          :class="{ 'is-active': s.id === activeId, 'is-busy': busy === s.id }"
          @click="pick(s)"
        >
          <div class="hist-main">
            <div class="hist-title">
              <input
                v-if="editingId === s.id"
                v-model="editingTitle"
                class="o-input hist-rename"
                autofocus
                @click.stop
                @keyup.enter.stop="saveRename(s)"
                @keyup.esc.stop="editingId = ''"
                @blur="saveRename(s)"
              />
              <template v-else>{{ s.title || '新的诊断' }}</template>
              <span v-if="s.id === activeId" class="hist-now">当前</span>
            </div>
            <div class="hist-meta">
              <span class="o-badge o-badge--outline">{{ s.teamName || '未归属团队' }}</span>
              <span v-if="!isMine(s)" class="hist-owner">{{ ownerLabel(s) }}</span>
              <span>{{ fmtTime(s.updatedAt) }}</span>
              <span>{{ s.messageCount }} 条消息</span>
              <span class="o-mono hist-model">{{ s.modelName || s.model || '—' }}</span>
            </div>
          </div>
          <div class="hist-actions" @click.stop>
            <button
              v-if="s.canManage"
              class="hist-icon"
              title="重命名"
              :disabled="busy === s.id"
              @click="startRename(s)"
            >
              <Icon name="pencil" :size="13" />
            </button>
            <button
              v-if="s.canManage"
              class="hist-icon hist-del"
              title="删除"
              :disabled="busy === s.id"
              @click="remove(s)"
            >
              <Icon name="trash" :size="13" />
            </button>
          </div>
        </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hist {
  width: 720px;
  display: flex;
  flex-direction: column;
  max-height: min(720px, calc(100vh - 96px));
}
.hist-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}
.hist-head b {
  font-size: 16px;
  font-weight: 600;
}
.hist-count {
  min-width: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg);
  color: var(--text-3);
  font-size: 11px;
  text-align: center;
}
.hist-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-3);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.hist-icon:hover:not(:disabled) {
  background: var(--bg);
  color: var(--text-1);
}
.hist-icon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.hist-close {
  margin-left: auto;
  font-size: 12px;
}
.hist-tools {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.hist-tools .o-input {
  flex: 1;
  min-width: 0;
}
.hist-tools .o-select {
  width: 150px;
  flex: none;
}
.hist-mine {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12.5px;
  color: var(--text-3);
  cursor: pointer;
}
.hist-mine input {
  accent-color: var(--primary);
}
.hist-err {
  margin-bottom: 8px;
  color: var(--danger);
  font-size: 12.5px;
}
.hist-list {
  flex: 1;
  min-height: 160px;
  overflow: auto;
  border-top: 1px solid var(--border);
}
.hist-empty {
  padding: 40px 0;
  color: var(--text-4);
  font-size: 12.5px;
  text-align: center;
}
.hist-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 8px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.hist-row:hover {
  background: var(--bg);
}
.hist-row.is-active {
  background: var(--primary-soft);
}
.hist-row.is-busy {
  opacity: 0.6;
  pointer-events: none;
}
.hist-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.hist-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-now {
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--primary);
  color: #fff;
  font-size: 10px;
  font-weight: 400;
}
.hist-rename {
  height: 28px;
  font-size: 13px;
}
.hist-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  color: var(--text-4);
  white-space: nowrap;
  overflow: hidden;
}
.hist-owner {
  color: var(--text-3);
}
.hist-model {
  overflow: hidden;
  text-overflow: ellipsis;
}
.hist-actions {
  flex: none;
  display: inline-flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.12s;
}
.hist-row:hover .hist-actions {
  opacity: 1;
}
.hist-del:hover {
  color: var(--danger);
}
</style>
