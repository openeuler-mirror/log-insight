<script setup lang="ts">
/* 个人知识库: 当前用户私有的运维知识笔记 CRUD + 关键词/标签检索
   - 笔记按 owner_id 严格隔离, 后端强制过滤, 前端仅能看到本人数据
   - 检索: 标题/正文关键字 (ILIKE) + 标签包含 */
import { onMounted, reactive, ref } from 'vue'
import { get, post, put, del, apiError } from '@/api/http'
import { fmtTime } from '@/parser/types'
import Icon from '@/components/Icon.vue'

interface NoteRow {
  id: number
  title: string
  content: string
  tags: string[]
  sourceType: string
  sourceTaskId: string | null
  createdAt: string
  updatedAt: string
}

const notes = ref<NoteRow[]>([])
const loading = ref(false)
const loadErr = ref('')

/* 筛选: kw 关键字(标题/正文), tag 标签 */
const kw = ref('')
const tag = ref('')

function queryString(): string {
  const p = new URLSearchParams()
  if (kw.value.trim()) p.set('q', kw.value.trim())
  if (tag.value.trim()) p.set('tag', tag.value.trim())
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function load() {
  loading.value = true
  loadErr.value = ''
  try {
    notes.value = await get<NoteRow[]>(`/knowledge${queryString()}`)
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

/* ---------- 新建 / 编辑弹窗 ---------- */
const showEdit = ref(false)
const editingId = ref<number | null>(null)
const saveErr = ref('')
const saving = ref(false)
const form = reactive({ title: '', content: '', tags: '', source_type: 'manual' })

function openEdit(row?: NoteRow) {
  saveErr.value = ''
  editingId.value = row?.id ?? null
  form.title = row?.title ?? ''
  form.content = row?.content ?? ''
  form.tags = (row?.tags ?? []).join(', ')
  form.source_type = row?.sourceType ?? 'manual'
  showEdit.value = true
}

/* 逗号(中/英文)分隔的标签文本 → 去空去重数组 */
function parseTags(text: string): string[] {
  const out: string[] = []
  for (const raw of text.split(/[,，]/)) {
    const t = raw.trim()
    if (t && !out.includes(t)) out.push(t)
  }
  return out
}

async function save() {
  if (!form.title.trim()) {
    saveErr.value = '请填写标题'
    return
  }
  saveErr.value = ''
  saving.value = true
  try {
    const body = {
      title: form.title.trim(),
      content: form.content,
      tags: parseTags(form.tags),
      source_type: form.source_type,
    }
    if (editingId.value) await put(`/knowledge/${editingId.value}`, body)
    else await post('/knowledge', body)
    showEdit.value = false
    await load()
  } catch (e) {
    saveErr.value = apiError(e)
  } finally {
    saving.value = false
  }
}

async function remove(row: NoteRow) {
  if (!window.confirm(`确认删除笔记「${row.title}」？`)) return
  loadErr.value = ''
  try {
    await del(`/knowledge/${row.id}`)
    await load()
  } catch (e) {
    loadErr.value = apiError(e)
  }
}
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>个人知识库</h2>
      <span class="o-mono o-text-3">沉淀排查经验与案例，仅本人可见</span>
    </div>
    <div class="o-operate-right">
      <input
        v-model="kw" class="o-input" style="width: 220px"
        placeholder="搜索标题 / 正文，回车检索" @keyup.enter="load"
      />
      <input
        v-model="tag" class="o-input" style="width: 150px"
        placeholder="按标签筛选" @keyup.enter="load"
      />
      <button class="o-btn" @click="load">检索</button>
      <button class="o-btn o-btn--primary" @click="openEdit()">＋ 新增笔记</button>
    </div>
  </div>

  <div v-if="loadErr" class="o-mono err-line"><Icon name="alert" :size="13" /> {{ loadErr }}</div>

  <div v-if="loading" class="o-text-3" style="padding: 32px 0; font-size: 13px">加载中…</div>
  <div v-else-if="!notes.length" class="o-card">
    <div class="o-empty" style="padding: 56px 0">
      <div class="icon"><Icon name="search" :size="28" /></div>
      <div>还没有笔记，点击右上角「新增笔记」开始沉淀</div>
    </div>
  </div>
  <div v-else class="note-grid">
    <div v-for="n in notes" :key="n.id" class="note-card" @click="openEdit(n)">
      <div class="note-head">
        <b class="note-title">{{ n.title }}</b>
        <span class="note-time o-mono o-text-3">{{ fmtTime(n.updatedAt) }}</span>
      </div>
      <div class="note-body o-text-3">{{ n.content || '（无正文）' }}</div>
      <div class="note-foot">
        <span v-for="t in n.tags" :key="t" class="o-badge o-badge--outline">{{ t }}</span>
        <span class="note-actions">
          <button class="o-btn o-btn--text o-btn--sm" @click.stop="openEdit(n)">编辑</button>
          <button class="o-btn o-btn--text o-btn--sm danger" @click.stop="remove(n)">删除</button>
        </span>
      </div>
    </div>
  </div>

  <!-- 新增/编辑弹窗 -->
  <div v-if="showEdit" class="o-modal-mask" @click.self="showEdit = false">
    <div class="o-modal">
      <div class="o-modal__title">{{ editingId ? '编辑笔记' : '新增笔记' }}</div>
      <div class="o-form-item">
        <label class="o-form-item__label required">标题</label>
        <input v-model="form.title" class="o-input" placeholder="如：Kafka 消费堆积排查思路" />
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label">标签</label>
        <input v-model="form.tags" class="o-input" placeholder="逗号分隔，如：kafka, 积压, 排查" />
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label">正文</label>
        <textarea
          v-model="form.content" class="o-textarea" rows="10"
          placeholder="记录现象、定位过程、根因与处置方式…"
        ></textarea>
      </div>
      <div v-if="saveErr" class="o-mono err-line"><Icon name="alert" :size="13" /> {{ saveErr }}</div>
      <div class="o-modal__foot">
        <button class="o-btn" @click="showEdit = false">取消</button>
        <button class="o-btn o-btn--primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.err-line {
  color: var(--danger);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.note-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.note-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  padding: 16px 18px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.note-card:hover {
  border-color: var(--primary);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}
.note-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.note-title {
  font-size: 14.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note-time {
  font-size: 12px;
  white-space: nowrap;
}
.note-body {
  font-size: 13px;
  line-height: 1.7;
  max-height: 66px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  white-space: pre-wrap;
  word-break: break-word;
}
.note-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: auto;
}
.note-actions {
  margin-left: auto;
  white-space: nowrap;
}
.note-actions .danger {
  color: var(--danger);
}
</style>
