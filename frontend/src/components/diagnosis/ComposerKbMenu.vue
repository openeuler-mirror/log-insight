<script setup lang="ts">
/* 诊断输入区: 多选知识库 + TopK（不选=本会话不启用，无弱提示） */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { listKbs, type KnowledgeBase } from '@/api/kb'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  teamId: number | null
  selectedIds: number[]
  topK: number
  canSearch: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  change: [payload: { selectedIds: number[]; topK: number }]
}>()

const open = ref(false)
const loading = ref(false)
const kbs = ref<KnowledgeBase[]>([])
const localTopK = ref(props.topK)
const rootRef = ref<HTMLElement | null>(null)

const label = computed(() => {
  const n = props.selectedIds.length
  if (!n) return '知识库'
  return `知识库 · ${n}`
})

async function load() {
  if (!props.teamId || !props.canSearch) {
    kbs.value = []
    return
  }
  loading.value = true
  try {
    kbs.value = await listKbs(props.teamId)
  } catch {
    kbs.value = []
  } finally {
    loading.value = false
  }
}

function toggleKb(id: number) {
  const set = new Set(props.selectedIds)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  emit('change', { selectedIds: [...set], topK: localTopK.value })
}

function onTopKBlur() {
  let v = Math.round(Number(localTopK.value) || 10)
  v = Math.min(50, Math.max(1, v))
  localTopK.value = v
  emit('change', { selectedIds: [...props.selectedIds], topK: v })
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  if (rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false
}

watch(() => props.teamId, () => { void load() })
watch(() => props.topK, (v) => { localTopK.value = v })
watch(open, (v) => { if (v) void load() })

onMounted(() => {
  document.addEventListener('mousedown', onDocClick)
  void load()
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocClick))
</script>

<template>
  <div ref="rootRef" class="kb-menu">
    <button
      type="button"
      class="kb-btn"
      :class="{ active: selectedIds.length, open }"
      :disabled="disabled || !teamId || !canSearch"
      :title="!canSearch ? '无知识库检索权限' : !teamId ? '请先选择团队' : '为本会话选择知识库'"
      @click="open = !open"
    >
      <Icon name="folder" :size="13" />
      <span>{{ label }}</span>
    </button>

    <div v-if="open" class="panel">
      <div class="panel-head">
        <span>本会话知识库</span>
        <label class="topk">
          TopK
          <input v-model.number="localTopK" type="number" min="1" max="50" @change="onTopKBlur" @blur="onTopKBlur" />
        </label>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!kbs.length" class="empty">当前团队暂无知识库</div>
      <ul v-else class="list">
        <li v-for="k in kbs" :key="k.id">
          <label class="row">
            <input
              type="checkbox"
              :checked="selectedIds.includes(k.id)"
              @change="toggleKb(k.id)"
            />
            <span class="name">{{ k.name }}</span>
            <span v-if="!k.vectorEnabled" class="tag">关键词</span>
          </label>
        </li>
      </ul>
      <p class="tip">不勾选则本会话不启用知识库</p>
    </div>
  </div>
</template>

<style scoped>
.kb-menu { position: relative; }
.kb-btn {
  display: inline-flex; align-items: center; gap: 5px;
  border: 1px solid var(--border); background: var(--surface);
  border-radius: var(--radius); padding: 4px 8px; font-size: 12.5px;
  color: var(--text-2); cursor: pointer;
}
.kb-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.kb-btn.active { border-color: var(--primary-tint); background: var(--primary-soft); color: var(--primary); }
.kb-btn:disabled { opacity: .5; cursor: not-allowed; }
.panel {
  position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 30;
  width: min(300px, 80vw); background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  box-shadow: 0 8px 28px rgba(15, 23, 42, .12); padding: 10px;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; color: var(--text-2); margin-bottom: 8px; gap: 8px;
}
.topk { display: inline-flex; align-items: center; gap: 4px; }
.topk input {
  width: 52px; border: 1px solid var(--border); border-radius: var(--radius);
  padding: 2px 6px; font-size: 12px;
}
.list { list-style: none; margin: 0; padding: 0; max-height: 220px; overflow: auto; }
.row {
  display: flex; align-items: center; gap: 8px; padding: 6px 4px;
  font-size: 13px; cursor: pointer; border-radius: var(--radius);
}
.row:hover { background: var(--surface-3); }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-1); }
.tag { font-size: 11px; color: var(--warning-deep); background: var(--warning-soft); padding: 1px 6px; border-radius: 99px; }
.empty, .tip { margin: 6px 0 0; font-size: 12px; color: var(--text-3); }
</style>
