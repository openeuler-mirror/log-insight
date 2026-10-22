<script setup lang="ts">
/* 左·日志文件树: 点击打开, 勾选加入本次诊断上下文 */
import { computed, ref, watch } from 'vue'
import FoldChevron from '@/components/diagnosis/FoldChevron.vue'
import Icon from '@/components/Icon.vue'
import type { Attach } from '@/components/diagnosisFiles'
import type { FileNode } from '@/api/diagnosis'

const props = defineProps<{
  nodes: FileNode[]
  activeId?: string
  loading?: boolean
}>()
const emit = defineEmits<{ open: [file: Attach]; select: [files: Attach[]] }>()

const expanded = ref<Record<string, boolean>>({})
watch(
  () => props.nodes,
  (nodes) => {
    for (const n of nodes) {
      if (expanded.value[n.id] === undefined) expanded.value[n.id] = true
    }
  },
  { immediate: true },
)

const fmtSize = (bytes?: number) => {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB'
}

interface TreeRow {
  node: FileNode
  depth: number
  path: string
}

const treeRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (nodes: FileNode[], depth: number, prefix: string) => {
    for (const n of nodes) {
      const path = prefix ? `${prefix}/${n.name}` : n.name
      out.push({ node: n, depth, path })
      if (n.kind === 'dir' && expanded.value[n.id]) walk(n.children ?? [], depth + 1, path)
    }
  }
  walk(props.nodes, 0, '')
  return out
})

const allFiles = computed<Record<string, Attach>>(() => {
  const map: Record<string, Attach> = {}
  const walk = (nodes: FileNode[], prefix: string) => {
    for (const n of nodes) {
      const path = prefix ? `${prefix}/${n.name}` : n.name
      if (n.kind === 'file') {
        map[n.id] = { id: n.id, name: n.name, path, size: fmtSize(n.size) }
      } else {
        walk(n.children ?? [], path)
      }
    }
  }
  walk(props.nodes, '')
  return map
})

const toggleExpand = (id: string) => {
  expanded.value[id] = !expanded.value[id]
}

const selectedIds = ref<string[]>([])
const fileIdsOf = (n: FileNode): string[] =>
  n.kind === 'file' ? [n.id] : (n.children ?? []).flatMap(fileIdsOf)
const isChecked = (n: FileNode) => {
  const ids = fileIdsOf(n)
  return ids.length > 0 && ids.every((id) => selectedIds.value.includes(id))
}
const isIndeterminate = (n: FileNode) => {
  const ids = fileIdsOf(n)
  const c = ids.filter((id) => selectedIds.value.includes(id)).length
  return c > 0 && c < ids.length
}
const toggleSelect = (n: FileNode) => {
  const ids = fileIdsOf(n)
  if (!ids.length) return
  const all = ids.every((id) => selectedIds.value.includes(id))
  selectedIds.value = all
    ? selectedIds.value.filter((id) => !ids.includes(id))
    : Array.from(new Set([...selectedIds.value, ...ids]))
}

watch(
  selectedIds,
  (ids) => emit('select', ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f)),
  { deep: true },
)

const onRowClick = (n: FileNode) => {
  if (n.kind === 'dir') {
    toggleExpand(n.id)
    return
  }
  const f = allFiles.value[n.id]
  if (f) emit('open', f)
}

const onDragStart = (row: TreeRow, e: DragEvent) => {
  const ids =
    row.node.kind === 'file' && selectedIds.value.includes(row.node.id) && selectedIds.value.length > 1
      ? selectedIds.value
      : fileIdsOf(row.node)
  const files = ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f)
  if (!files.length) return
  e.dataTransfer?.setData('text/plain', JSON.stringify(files))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}

defineExpose({
  clear: () => (selectedIds.value = []),
  select: (id: string) => {
    if (!selectedIds.value.includes(id)) selectedIds.value = [...selectedIds.value, id]
  },
  deselect: (id: string) => {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
  },
})
</script>

<template>
  <div class="tree">
    <div v-if="loading" class="tree-empty">正在加载…</div>
    <slot v-else-if="!nodes.length" name="empty">
      <div class="tree-empty">
        还没有可分析的日志。<br />先去「创建解析任务」上传日志，或直接把日志粘贴到右侧提问。
      </div>
    </slot>
    <template v-else>
      <div
        v-for="r in treeRows"
        :key="r.node.id"
        class="tree-row"
        :class="{
          'is-sel': r.node.kind === 'file' && selectedIds.includes(r.node.id),
          'is-open': r.node.id === activeId,
        }"
        :draggable="fileIdsOf(r.node).length > 0"
        :style="{ paddingLeft: 6 + r.depth * 14 + 'px' }"
        :title="r.path"
        @click="onRowClick(r.node)"
        @dragstart="onDragStart(r, $event)"
      >
        <span
          class="tree-caret"
          :class="{ 'is-leaf': r.node.kind === 'file' }"
          @click.stop="r.node.kind === 'dir' && toggleExpand(r.node.id)"
        >
          <FoldChevron v-if="r.node.kind === 'dir'" :open="!!expanded[r.node.id]" />
        </span>
        <input
          class="tree-check"
          type="checkbox"
          :aria-label="'选择 ' + r.node.name"
          :checked="isChecked(r.node)"
          :indeterminate.prop="isIndeterminate(r.node)"
          @click.stop="toggleSelect(r.node)"
        />
        <span class="tree-ico">
          <Icon
            :name="r.node.kind === 'dir' ? (expanded[r.node.id] ? 'folder-open' : 'folder') : 'file'"
            :size="14"
          />
        </span>
        <span class="tree-name">{{ r.node.name }}</span>
        <span v-if="r.node.size" class="o-mono tree-size">{{ fmtSize(r.node.size) }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tree {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 6px;
}
.tree-empty {
  padding: 18px 12px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--text-3);
  text-align: center;
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding-right: 6px;
  border-radius: var(--radius);
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}
.tree-row:hover {
  background: var(--surface);
}
.tree-row.is-sel {
  background: var(--primary-soft);
}
.tree-row.is-open {
  box-shadow: inset 2px 0 0 var(--primary);
}
.tree-caret {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
}
.tree-caret.is-leaf {
  cursor: default;
}
.tree-check {
  flex: none;
  width: 14px;
  height: 14px;
  cursor: pointer;
  accent-color: var(--primary);
}
.tree-ico {
  flex: none;
  display: inline-flex;
  align-items: center;
  color: var(--text-4);
}
.tree-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}
.tree-row.is-sel .tree-name,
.tree-row.is-open .tree-name {
  color: var(--primary);
  font-weight: 600;
}
.tree-size {
  flex: none;
  font-size: 11px;
  color: var(--text-4);
}
</style>
