<script setup lang="ts">
/* 左·目录文件树(资源管理器)
   展开 / 折叠 + 多选(目录整选 / 半选) + 拖拽文件到对话框作为提问上下文。
   拖拽载荷: JSON 化的文件列表 [{id,name,path,size}], 由对话区的 drop 处理。 */
import { computed, ref } from 'vue'

interface FsNode {
  id: string
  name: string
  kind: 'dir' | 'file'
  size?: string
  children?: FsNode[]
}

interface Attach {
  id: string
  name: string
  path: string
  size?: string
}

const tree = ref<FsNode[]>([
  {
    id: 'root',
    name: 'loginsight-workspace',
    kind: 'dir',
    children: [
      {
        id: 'd-var',
        name: 'var',
        kind: 'dir',
        children: [
          {
            id: 'd-varlog',
            name: 'log',
            kind: 'dir',
            children: [
              { id: 'f-messages', name: 'messages', kind: 'file', size: '18.2 MB' },
              { id: 'f-secure', name: 'secure', kind: 'file', size: '3.1 MB' },
              {
                id: 'd-nginx',
                name: 'nginx',
                kind: 'dir',
                children: [
                  { id: 'f-access', name: 'access.log', kind: 'file', size: '240 MB' },
                  { id: 'f-errorlog', name: 'error.log', kind: 'file', size: '6.8 MB' },
                ],
              },
            ],
          },
        ],
      },
      {
        id: 'd-opt',
        name: 'opt',
        kind: 'dir',
        children: [
          {
            id: 'd-tasks',
            name: 'tasks',
            kind: 'dir',
            children: [
              {
                id: 'd-1011',
                name: '20261015_1011',
                kind: 'dir',
                children: [
                  { id: 'f-parsedmsg', name: 'messages.log', kind: 'file', size: '18.2 MB' },
                  { id: 'f-parse', name: 'parse_result.json', kind: 'file', size: '2.4 MB' },
                  { id: 'f-sar', name: 'perf_sample.sar', kind: 'file', size: '860 KB' },
                ],
              },
            ],
          },
        ],
      },
      {
        id: 'd-etc',
        name: 'etc',
        kind: 'dir',
        children: [
          {
            id: 'd-net',
            name: 'network-scripts',
            kind: 'dir',
            children: [{ id: 'f-eth0', name: 'ifcfg-eth0', kind: 'file', size: '512 B' }],
          },
          { id: 'f-selinux', name: 'selinux', kind: 'file', size: '1.2 KB' },
        ],
      },
    ],
  },
])

const expanded = ref<Record<string, boolean>>({ root: true, 'd-var': true, 'd-varlog': true, 'd-opt': true })

interface TreeRow {
  node: FsNode
  depth: number
  path: string
}
const treeRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (nodes: FsNode[], depth: number, prefix: string) => {
    for (const n of nodes) {
      const path = prefix ? `${prefix}/${n.name}` : n.name
      out.push({ node: n, depth, path })
      if (n.kind === 'dir' && expanded.value[n.id]) walk(n.children ?? [], depth + 1, path)
    }
  }
  walk(tree.value, 0, '')
  return out
})

/* 全量文件索引 (与展开状态无关, 便于目录整选 / 拖拽) */
const allFiles = computed<Record<string, Attach>>(() => {
  const map: Record<string, Attach> = {}
  const walk = (nodes: FsNode[], prefix: string) => {
    for (const n of nodes) {
      const path = prefix ? `${prefix}/${n.name}` : n.name
      if (n.kind === 'file') map[n.id] = { id: n.id, name: n.name, path, size: n.size }
      else walk(n.children ?? [], path)
    }
  }
  walk(tree.value, '')
  return map
})

function toggleExpand(id: string) {
  expanded.value[id] = !expanded.value[id]
}

/* 多选: 选中目录等价于选中其下全部文件 */
const selectedIds = ref<string[]>([])
const fileIdsOf = (n: FsNode): string[] => (n.kind === 'file' ? [n.id] : (n.children ?? []).flatMap(fileIdsOf))
const isChecked = (n: FsNode) => {
  const ids = fileIdsOf(n)
  return ids.length > 0 && ids.every((id) => selectedIds.value.includes(id))
}
const isIndeterminate = (n: FsNode) => {
  const ids = fileIdsOf(n)
  const c = ids.filter((id) => selectedIds.value.includes(id)).length
  return c > 0 && c < ids.length
}
function toggleSelect(n: FsNode) {
  const ids = fileIdsOf(n)
  if (!ids.length) return
  const all = ids.every((id) => selectedIds.value.includes(id))
  selectedIds.value = all
    ? selectedIds.value.filter((id) => !ids.includes(id))
    : Array.from(new Set([...selectedIds.value, ...ids]))
}

/* 拖拽: 已多选时拖动选区, 否则拖动该行对应的文件 */
function onDragStart(row: TreeRow, e: DragEvent) {
  const ids =
    row.node.kind === 'file' && selectedIds.value.includes(row.node.id) && selectedIds.value.length > 1
      ? selectedIds.value
      : fileIdsOf(row.node)
  const files = ids.map((id) => allFiles.value[id]).filter((f): f is Attach => !!f)
  if (!files.length) return
  e.dataTransfer?.setData('text/plain', JSON.stringify(files))
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}
</script>

<template>
  <div class="pane-sub-head">
    <span class="pane-title">目录文件</span>
    <span class="o-mono o-text-3">{{ selectedIds.length }} 已选</span>
  </div>
  <div class="tree">
    <div
      v-for="r in treeRows"
      :key="r.node.id"
      class="tree-row"
      :class="{ 'is-sel': r.node.kind === 'file' && selectedIds.includes(r.node.id) }"
      :draggable="fileIdsOf(r.node).length > 0"
      :style="{ paddingLeft: 6 + r.depth * 14 + 'px' }"
      :title="r.path"
      @click="toggleSelect(r.node)"
      @dragstart="onDragStart(r, $event)"
    >
      <span
        class="tree-caret"
        :class="{ 'is-open': !!expanded[r.node.id], 'is-leaf': r.node.kind === 'file' }"
        @click.stop="r.node.kind === 'dir' && toggleExpand(r.node.id)"
        >{{ r.node.kind === 'dir' ? '▸' : '' }}</span
      >
      <input
        class="tree-check"
        type="checkbox"
        :checked="isChecked(r.node)"
        :indeterminate.prop="isIndeterminate(r.node)"
        @click.stop="toggleSelect(r.node)"
      />
      <span class="tree-ico">{{ r.node.kind === 'dir' ? (expanded[r.node.id] ? '📂' : '📁') : '📄' }}</span>
      <span class="tree-name">{{ r.node.name }}</span>
      <span v-if="r.node.size" class="o-mono tree-size">{{ r.node.size }}</span>
    </div>
  </div>
  <div class="tree-hint">勾选 / 拖拽文件到对话框，作为提问上下文</div>
</template>

<style scoped>
.pane-sub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px 6px;
  border-top: 1px solid var(--border);
  flex: none;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.tree {
  flex: 1 1 55%;
  min-height: 0;
  overflow: auto;
  padding: 0 6px 6px;
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding-right: 6px;
  border-radius: var(--radius);
  font-size: 12.5px;
  cursor: pointer;
  user-select: none;
}
.tree-row:hover {
  background: var(--surface);
}
.tree-row.is-sel {
  background: var(--primary-soft);
}
.tree-caret {
  flex: none;
  width: 14px;
  text-align: center;
  color: var(--text-3);
  font-size: 10px;
  transition: transform 0.15s;
}
.tree-caret.is-open {
  transform: rotate(90deg);
}
.tree-caret.is-leaf {
  cursor: default;
}
.tree-check {
  flex: none;
  width: 13px;
  height: 13px;
  cursor: pointer;
  accent-color: var(--primary);
}
.tree-ico {
  flex: none;
  font-size: 12px;
}
.tree-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-2);
}
.tree-row.is-sel .tree-name {
  color: var(--primary);
  font-weight: 600;
}
.tree-size {
  flex: none;
  font-size: 10.5px;
  color: var(--text-4);
}
.tree-hint {
  flex: none;
  padding: 6px 14px 8px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-4);
}
</style>
