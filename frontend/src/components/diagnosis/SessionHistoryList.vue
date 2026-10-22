<script setup lang="ts">
/* 左栏·历史会话: 仅当前用户自己的会话, 支持重命名与删除 */
import { ref } from 'vue'
import { apiError } from '@/api/http'
import { deleteSession, renameSession, type SessionInfo } from '@/api/diagnosis'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  sessions: SessionInfo[]
  activeId: string
}>()
const emit = defineEmits<{
  select: [session: SessionInfo]
  changed: []
}>()

const editingId = ref('')
const editingTitle = ref('')
const busyId = ref('')
const err = ref('')

function fmtTime(value?: string) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return hm
  const md = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`
}

function pick(s: SessionInfo) {
  if (busyId.value || editingId.value) return
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
  busyId.value = s.id
  try {
    await renameSession(s.id, title)
    editingId.value = ''
    emit('changed')
  } catch (e) {
    err.value = apiError(e)
  } finally {
    busyId.value = ''
  }
}

async function remove(s: SessionInfo) {
  if (!window.confirm(`删除会话「${s.title || '新的诊断'}」？聊天记录会一起删除。`)) return
  busyId.value = s.id
  try {
    await deleteSession(s.id)
    emit('changed')
  } catch (e) {
    err.value = apiError(e)
  } finally {
    busyId.value = ''
  }
}
</script>

<template>
  <div class="hist-body">
    <p v-if="err" class="hist-err">{{ err }}</p>
    <div v-if="!props.sessions.length" class="hist-empty">还没有历史会话</div>
    <div v-else class="hist-scroll">
      <div
        v-for="s in props.sessions"
        :key="s.id"
        class="hist-row"
        :class="{ 'is-active': s.id === activeId, 'is-busy': busyId === s.id }"
        :title="s.title || '新的诊断'"
        @click="pick(s)"
      >
        <div class="hist-main">
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
          <span v-else class="hist-title">{{ s.title || '新的诊断' }}</span>
          <span class="hist-meta">
            {{ fmtTime(s.updatedAt) }}
            <template v-if="s.messageCount">· {{ s.messageCount }} 条</template>
          </span>
        </div>
        <div class="hist-actions" @click.stop>
          <button
            class="hist-icon"
            title="重命名"
            :disabled="busyId === s.id"
            @click="startRename(s)"
          >
            <Icon name="pencil" :size="14" />
          </button>
          <button
            class="hist-icon hist-del"
            title="删除"
            :disabled="busyId === s.id"
            @click="remove(s)"
          >
            <Icon name="trash" :size="14" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hist-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.hist-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 2px 6px 6px;
}
.hist-empty {
  padding: 18px 12px;
  font-size: 12.5px;
  color: var(--text-4);
  text-align: center;
}
.hist-err {
  margin: 6px 10px;
  color: var(--danger);
  font-size: 12px;
}
.hist-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 6px 7px 10px;
  border-radius: var(--radius);
  cursor: pointer;
}
.hist-row:hover {
  background: var(--surface);
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
  gap: 1px;
}
.hist-title {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-row.is-active .hist-title {
  color: var(--primary);
  font-weight: 600;
}
.hist-meta {
  font-size: 11px;
  color: var(--text-4);
  white-space: nowrap;
}
.hist-rename {
  height: 26px;
  padding: 0 6px;
  font-size: 13px;
}
.hist-actions {
  flex: none;
  display: inline-flex;
  gap: 1px;
  opacity: 0;
  transition: opacity 0.12s;
}
.hist-row:hover .hist-actions {
  opacity: 1;
}
.hist-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--radius);
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
}
.hist-icon:hover:not(:disabled) {
  background: var(--surface-3);
  color: var(--text-1);
}
.hist-icon:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.hist-del:hover:not(:disabled) {
  color: var(--danger);
}
</style>
