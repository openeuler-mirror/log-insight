<script setup lang="ts">
/* 知识库首页: 卡片网格; 点卡片进详情; 设置弹窗改配置 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { apiError } from '@/api/http'
import {
  createKb, deleteKb, listKbs, updateKb, type KnowledgeBase,
} from '@/api/kb'
import Icon from '@/components/Icon.vue'

const rr = useRouter()
const teamsStore = useTeamsStore()
const rbac = useRbacStore()

const teamId = computed(() => teamsStore.activeTeamId)
const canManage = computed(() =>
  !!teamId.value && (rbac.isPlatformAdmin || teamsStore.can(teamId.value, 'kb:manage')))
const canView = computed(() =>
  !!teamId.value && (rbac.isPlatformAdmin || teamsStore.can(teamId.value, 'kb:view')))

const loading = ref(false)
const err = ref('')
const kbs = ref<KnowledgeBase[]>([])

const showCreate = ref(false)
const formName = ref('')
const formDesc = ref('')
const formVector = ref(false)
const formEmbedModel = ref('')
const formEmbedBase = ref('')
const formEmbedKey = ref('')
const creating = ref(false)

const settingsKb = ref<KnowledgeBase | null>(null)
const setName = ref('')
const setDesc = ref('')
const setTemplate = ref('')
const setEmbedBase = ref('')
const setEmbedKey = ref('')
const saving = ref(false)

async function reload() {
  if (!teamId.value || !canView.value) {
    kbs.value = []
    return
  }
  loading.value = true
  err.value = ''
  try {
    kbs.value = await listKbs(teamId.value)
  } catch (e) {
    err.value = apiError(e)
  } finally {
    loading.value = false
  }
}

function openKb(id: number) {
  rr.push({ name: 'knowledge-detail', params: { kbId: String(id) } })
}

function openSettings(e: Event, kb: KnowledgeBase) {
  e.stopPropagation()
  if (!canManage.value) return
  settingsKb.value = kb
  setName.value = kb.name
  setDesc.value = kb.description || ''
  setTemplate.value = kb.caseTemplate || ''
  setEmbedBase.value = kb.embeddingConfig?.apiBase || ''
  setEmbedKey.value = ''
}

async function onCreate() {
  if (!teamId.value || !formName.value.trim()) return
  if (formVector.value) {
    if (!formEmbedModel.value.trim()) {
      err.value = '启用向量时请填写 Embedding 模型 ID'
      return
    }
    if (!formEmbedKey.value.trim()) {
      err.value = '启用向量时请填写 Embedding API Key（可与团队 Chat 密钥不同）'
      return
    }
  }
  creating.value = true
  err.value = ''
  try {
    const kb = await createKb(teamId.value, {
      name: formName.value.trim(),
      description: formDesc.value.trim(),
      vectorEnabled: formVector.value,
      embeddingConfig: formVector.value
        ? {
            model: formEmbedModel.value.trim(),
            api_base: formEmbedBase.value.trim(),
            api_key: formEmbedKey.value.trim(),
          }
        : {},
    })
    showCreate.value = false
    formName.value = ''
    formDesc.value = ''
    formVector.value = false
    formEmbedModel.value = ''
    formEmbedBase.value = ''
    formEmbedKey.value = ''
    await reload()
    openKb(kb.id)
  } catch (e) {
    err.value = apiError(e)
  } finally {
    creating.value = false
  }
}

async function onSaveSettings() {
  if (!teamId.value || !settingsKb.value) return
  saving.value = true
  err.value = ''
  try {
    const body: Parameters<typeof updateKb>[2] = {
      name: setName.value.trim(),
      description: setDesc.value,
      caseTemplate: setTemplate.value,
    }
    if (settingsKb.value.vectorEnabled) {
      body.embeddingApiBase = setEmbedBase.value.trim()
      const key = setEmbedKey.value.trim()
      if (key) body.embeddingApiKey = key
    }
    await updateKb(teamId.value, settingsKb.value.id, body)
    settingsKb.value = null
    setEmbedKey.value = ''
    await reload()
  } catch (e) {
    err.value = apiError(e)
  } finally {
    saving.value = false
  }
}

async function onDeleteFromSettings() {
  if (!teamId.value || !settingsKb.value) return
  if (!confirm('确认软删除该知识库？删除后列表中不再显示。')) return
  try {
    await deleteKb(teamId.value, settingsKb.value.id)
    settingsKb.value = null
    await reload()
  } catch (e) {
    err.value = apiError(e)
  }
}

watch(teamId, () => reload())

onMounted(async () => {
  if (!teamsStore.teams.length) await teamsStore.fetchTeams().catch(() => {})
  const list = rbac.isPlatformAdmin
    ? teamsStore.teams
    : teamsStore.teams.filter((t) => !!rbac.myTeamRoles[String(t.id)])
  if (list.length && !list.some((t) => t.id === teamsStore.activeTeamId)) {
    teamsStore.setActiveTeam(list[0].id)
  }
  await reload()
})
</script>

<template>
  <div class="kb-page">
    <div class="kb-head">
      <div>
        <h1 class="title">知识库</h1>
        <p class="sub">当前团队：{{ teamsStore.activeTeam?.name || '未选择' }} · 点击卡片进入详情</p>
      </div>
      <button v-if="canManage" class="btn primary" @click="showCreate = true">
        <Icon name="plus" :size="14" /> 新建知识库
      </button>
    </div>

    <div v-if="!teamId" class="empty">请先在顶栏选择或加入团队</div>
    <div v-else-if="!canView" class="empty">无知识库查看权限</div>
    <div v-else-if="err" class="err">{{ err }}</div>
    <div v-else-if="loading" class="empty muted">加载中…</div>
    <div v-else-if="!kbs.length" class="empty">
      暂无知识库
      <button v-if="canManage" class="btn primary" style="margin-top:12px" @click="showCreate = true">创建第一个</button>
    </div>

    <div v-else class="grid">
      <article
        v-for="k in kbs"
        :key="k.id"
        class="card"
        role="button"
        tabindex="0"
        @click="openKb(k.id)"
        @keydown.enter="openKb(k.id)"
      >
        <button
          v-if="canManage"
          class="card-set"
          title="设置"
          @click="openSettings($event, k)"
        >
          <Icon name="pencil" :size="14" />
        </button>
        <div class="card-ico"><Icon name="folder" :size="22" /></div>
        <h3 class="card-name">{{ k.name }}</h3>
        <p class="card-desc">{{ k.description || '暂无描述' }}</p>
        <div class="card-foot">
          <span v-if="!k.vectorEnabled" class="badge">仅关键词</span>
          <span v-else class="badge ok">向量检索</span>
        </div>
      </article>
    </div>

    <!-- 新建 -->
    <div v-if="showCreate" class="mask" @click.self="showCreate = false">
      <div class="dialog">
        <h3>新建知识库</h3>
        <label>名称<input v-model="formName" maxlength="128" /></label>
        <label>描述<input v-model="formDesc" maxlength="512" /></label>
        <div class="mode">
          <label class="radio">
            <input v-model="formVector" type="radio" :value="false" />
            <span><b>仅关键词 / Grep</b> — 不启用向量，不支持语义检索</span>
          </label>
          <label class="radio">
            <input v-model="formVector" type="radio" :value="true" />
            <span><b>启用向量检索</b> — 建库后 Embedding 不可更改</span>
          </label>
        </div>
        <template v-if="formVector">
          <label>Embedding 模型 ID
            <input v-model="formEmbedModel" placeholder="如 text-embedding-v3" />
          </label>
          <label>Embedding 接口地址（可选）
            <input v-model="formEmbedBase" placeholder="https://api.example.com/v1，留空用默认" />
          </label>
          <label>Embedding API Key
            <input v-model="formEmbedKey" type="password" autocomplete="off" placeholder="与团队 Chat 密钥可不同" />
          </label>
          <div class="hint">密钥按库加密保存并锁定；仅用于本库向量化，不会回传明文。</div>
        </template>
        <div class="dialog-actions">
          <button class="btn" @click="showCreate = false">取消</button>
          <button
            class="btn primary"
            :disabled="creating || !formName.trim() || (formVector && (!formEmbedModel.trim() || !formEmbedKey.trim()))"
            @click="onCreate"
          >
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 设置 -->
    <div v-if="settingsKb" class="mask" @click.self="settingsKb = null">
      <div class="dialog wide">
        <h3>知识库设置 · {{ settingsKb.name }}</h3>
        <label>名称<input v-model="setName" maxlength="128" /></label>
        <label>描述<input v-model="setDesc" maxlength="512" /></label>
        <div class="hint" v-if="settingsKb.vectorHint">{{ settingsKb.vectorHint }}</div>
        <template v-else>
          <div class="hint">
            Embedding 模型已锁定：{{ settingsKb.embeddingConfig?.model || '—' }}（建库后不可更改）
          </div>
          <label>Embedding 接口地址
            <input v-model="setEmbedBase" placeholder="https://api.example.com/v1，可留空" />
          </label>
          <label>Embedding API Key
            <input
              v-model="setEmbedKey"
              type="password"
              autocomplete="off"
              :placeholder="`留空保持 ${settingsKb.embeddingConfig?.keyHint || '原密钥'}；填写则轮换`"
            />
          </label>
        </template>
        <label>案例模板（空=系统默认）
          <textarea v-model="setTemplate" rows="10" class="ta" />
        </label>
        <div class="dialog-actions between">
          <button class="btn danger" @click="onDeleteFromSettings">删除知识库</button>
          <div class="row">
            <button class="btn" @click="settingsKb = null">取消</button>
            <button class="btn primary" :disabled="saving || !setName.trim()" @click="onSaveSettings">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kb-page { width: 100%; }
.kb-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; gap: 12px; }
.title { font-size: 20px; margin: 0 0 4px; color: var(--text-1); }
.sub, .muted { color: var(--text-2); font-size: 13px; margin: 0; }
.err { color: var(--danger-deep); background: var(--danger-soft); padding: 8px 12px; border-radius: var(--radius); margin-bottom: 12px; }
.empty { padding: 56px 24px; text-align: center; color: var(--text-2); display: flex; flex-direction: column; align-items: center; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 280px));
  gap: 16px;
  width: 100%;
}
.card {
  position: relative;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 18px 16px 14px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
  min-height: 148px;
  display: flex;
  flex-direction: column;
}
.card:hover {
  border-color: var(--primary);
  box-shadow: 0 4px 14px rgba(30, 111, 255, .08);
}
.card-set {
  position: absolute;
  top: 8px;
  right: 8px;
  border: 0;
  background: var(--surface-3);
  width: 28px;
  height: 28px;
  border-radius: var(--radius);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--text-2);
}
.card-set:hover { color: var(--primary); background: var(--primary-soft); }
.card-ico { color: var(--primary); margin-bottom: 10px; }
.card-name { margin: 0 0 6px; font-size: 16px; color: var(--text-1); padding-right: 28px; }
.card-desc {
  margin: 0;
  flex: 1;
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-foot { margin-top: 12px; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 99px; background: var(--warning-soft); color: var(--warning-deep); }
.badge.ok { background: var(--success-soft); color: var(--success-deep); }

.btn { border: 1px solid var(--border); background: var(--surface); border-radius: var(--radius); padding: 6px 12px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; }
.btn.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.btn.danger { color: var(--danger-deep); border-color: var(--danger); }
.mask { position: fixed; inset: 0; background: rgba(15,23,42,.35); display: flex; align-items: center; justify-content: center; z-index: 40; }
.dialog { width: min(440px, 92vw); background: var(--surface); border-radius: var(--radius-lg); padding: 20px; display: flex; flex-direction: column; gap: 12px; max-height: 90vh; overflow: auto; }
.dialog.wide { width: min(560px, 94vw); }
.dialog h3 { margin: 0; }
.dialog label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--text-2); }
.dialog input { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px; font-size: 14px; color: var(--text-1); }
.ta { width: 100%; font-family: inherit; font-size: 13px; border: 1px solid var(--border); border-radius: var(--radius); padding: 8px; resize: vertical; color: var(--text-1); }
.mode { display: flex; flex-direction: column; gap: 8px; }
.radio { flex-direction: row !important; align-items: flex-start; gap: 8px !important; }
.hint { font-size: 12px; color: var(--text-2); background: var(--surface-3); padding: 8px 10px; border-radius: var(--radius); }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.dialog-actions.between { justify-content: space-between; align-items: center; }
.row { display: flex; gap: 8px; }
</style>
