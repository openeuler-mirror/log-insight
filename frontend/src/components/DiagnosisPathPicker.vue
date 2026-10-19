<script setup lang="ts">
/* 服务端路径选择弹窗: 浏览后端可访问的目录, 选择目录(dir)或文件(file) */
import { ref, watch } from 'vue'
import { fetchList, fetchRoots, type FsEntry, type FsRoot } from '@/api/fs'
import { apiError } from '@/api/http'
import { fmtSize } from '@/components/diagnosisFiles'

const props = defineProps<{ open: boolean; mode: 'dir' | 'file' }>()
const emit = defineEmits<{ select: [path: string]; close: [] }>()

const roots = ref<FsRoot[]>([])
const cwd = ref('')
const entries = ref<FsEntry[]>([])
const loading = ref(false)
const error = ref('')

async function openAt(path: string) {
  cwd.value = path
  loading.value = true
  error.value = ''
  try {
    entries.value = (await fetchList(path)).entries
  } catch (e) {
    entries.value = []
    error.value = apiError(e)
  } finally {
    loading.value = false
  }
}

async function init() {
  error.value = ''
  try {
    const r = await fetchRoots()
    roots.value = r.roots
    const start = cwd.value || r.home || r.roots[0]?.path || ''
    if (start) await openAt(start)
  } catch (e) {
    error.value = apiError(e)
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) void init()
  },
)

/** 上级目录(兼容 Windows 盘符根 E:\ 与 POSIX 根 /) */
function parentOf(p: string): string | null {
  const norm = p.replace(/[\\/]+$/, '')
  const i = Math.max(norm.lastIndexOf('\\'), norm.lastIndexOf('/'))
  if (i < 0) return null
  const parent = norm.slice(0, i)
  if (!parent) return null
  return /^[A-Za-z]:$/.test(parent) ? `${parent}\\` : parent
}
const parent = () => parentOf(cwd.value)

function onEntry(e: FsEntry) {
  if (e.kind === 'dir') void openAt(e.path)
  else if (props.mode === 'file') emit('select', e.path)
}
</script>

<template>
  <div v-if="open" class="pk-mask" @click.self="emit('close')">
    <div class="pk">
      <div class="pk-head">
        <span class="pk-title">{{ mode === 'dir' ? '选择目录' : '选择文件' }}</span>
        <button class="icon-btn" title="关闭" @click="emit('close')">✕</button>
      </div>

      <div class="pk-roots">
        <button
          v-for="r in roots"
          :key="r.path"
          class="pk-root"
          :class="{ 'is-active': cwd === r.path }"
          :title="r.path"
          @click="openAt(r.path)"
        >
          {{ r.name }}
        </button>
        <button class="pk-root" :disabled="!parent()" @click="parent() && openAt(parent()!)">↑ 上级</button>
      </div>

      <div class="pk-path o-mono" :title="cwd">{{ cwd || '—' }}</div>

      <div class="pk-list">
        <div v-if="loading" class="pk-empty">加载中…</div>
        <template v-else>
          <div
            v-for="e in entries"
            :key="e.path"
            class="pk-row"
            :class="{ 'is-file': e.kind === 'file', 'is-disabled': e.kind === 'file' && mode === 'dir' }"
            @click="onEntry(e)"
          >
            <span class="pk-ico">{{ e.kind === 'dir' ? '📁' : '📄' }}</span>
            <span class="pk-name">{{ e.name }}</span>
            <span v-if="e.kind === 'file'" class="o-mono pk-size">{{ fmtSize(e.size) }}</span>
          </div>
          <div v-if="!entries.length" class="pk-empty">空目录</div>
        </template>
      </div>

      <div v-if="error" class="pk-err">{{ error }}</div>

      <div class="pk-foot">
        <span class="pk-tip">{{
          mode === 'dir' ? '进入目标目录后点击「选择当前目录」' : '点击文件即选中'
        }}</span>
        <button
          v-if="mode === 'dir'"
          class="o-btn o-btn--primary o-btn--sm"
          :disabled="!cwd"
          @click="emit('select', cwd)"
        >
          选择当前目录
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pk-mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 25, 40, 0.32);
}
.pk {
  display: flex;
  flex-direction: column;
  width: min(560px, 92vw);
  max-height: 74vh;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2, var(--shadow-1));
  overflow: hidden;
}
.pk-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
}
.pk-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.pk-roots {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 12px 0;
}
.pk-root {
  padding: 2px 9px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}
.pk-root:hover:not(:disabled) {
  border-color: var(--primary);
  color: var(--primary);
}
.pk-root.is-active {
  background: var(--primary-soft);
  border-color: var(--primary);
  color: var(--primary);
}
.pk-root:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.pk-path {
  flex: none;
  padding: 8px 12px;
  font-size: 11.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pk-list {
  flex: 1 1 auto;
  min-height: 120px;
  overflow: auto;
  border-top: 1px solid var(--border);
  padding: 4px 6px;
}
.pk-row {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 8px;
  border-radius: var(--radius);
  font-size: 12.5px;
  cursor: pointer;
}
.pk-row:hover {
  background: var(--surface-3);
}
.pk-row.is-disabled {
  opacity: 0.5;
  cursor: default;
}
.pk-row.is-disabled:hover {
  background: transparent;
}
.pk-ico {
  flex: none;
  font-size: 12px;
}
.pk-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}
.pk-size {
  flex: none;
  font-size: 10.5px;
  color: var(--text-4);
}
.pk-empty {
  padding: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--text-4);
}
.pk-err {
  flex: none;
  padding: 6px 12px;
  font-size: 11.5px;
  color: var(--danger-deep);
  border-top: 1px solid var(--border);
}
.pk-foot {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
.pk-tip {
  font-size: 11px;
  color: var(--text-4);
}
</style>
