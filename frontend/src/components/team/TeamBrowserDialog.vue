<script setup lang="ts">
/* 其他团队: 团队列表弹窗 — 创建团队 / 进入已加入团队 / 申请加入其他团队 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useTeamsStore } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { teamRoleById } from '@/rbac/permissions'
import { apiError } from '@/api/http'
import Icon from '@/components/Icon.vue'

const rr = useRouter()
const teamsStore = useTeamsStore()
const rbac = useRbacStore()

const kw = ref('')
const actionErr = ref('')
const creating = ref(false)
const showCreate = ref(false)
const form = ref({ name: '', desc: '' })

const open = computed(() => teamsStore.browserOpen)
watch(open, (v) => {
  if (!v) return
  actionErr.value = ''
  kw.value = ''
  showCreate.value = false
  teamsStore.fetchTeams().catch((e) => { actionErr.value = apiError(e) })
})

const filtered = computed(() => {
  const k = kw.value.trim()
  const list = k ? teamsStore.teams.filter((t) => t.name.includes(k) || t.desc.includes(k)) : teamsStore.teams
  /* 已加入的团队排在前面, 便于直接进入 */
  return [...list].sort((a, b) => Number(canEnter(b)) - Number(canEnter(a)))
})

/* 平台管理员可进入全部团队; 普通用户仅能进入已加入的团队 */
function canEnter(t: { id: number }): boolean {
  return rbac.isPlatformAdmin || !!teamsStore.myRoleOf(t.id)
}
const myRole = (t: { id: number }) => teamsStore.myRoleOf(t.id)
const hasPendingApply = (t: { id: number }) => teamsStore.appliedTeamIds.includes(t.id)

function close() {
  teamsStore.closeTeamBrowser()
}

/* 进入: 切换顶栏当前团队; 团队管理页直接展示该团队详情 */
function enter(t: { id: number }) {
  teamsStore.setActiveTeam(t.id)
  close()
  if (rr.currentRoute.value.path.startsWith('/teams/')) rr.push('/teams')
}

async function applyJoin(t: { id: number; name: string }) {
  actionErr.value = ''
  try {
    await teamsStore.apply(t.id)
  } catch (e) {
    actionErr.value = apiError(e)
  }
}

async function create() {
  if (!form.value.name.trim() || creating.value) return
  creating.value = true
  actionErr.value = ''
  try {
    const team = await teamsStore.createTeam(form.value.name.trim(), form.value.desc.trim())
    showCreate.value = false
    form.value = { name: '', desc: '' }
    teamsStore.setActiveTeam(team.id)
    close()
    rr.push('/teams')
  } catch (e) {
    actionErr.value = apiError(e)
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <div v-if="open" class="o-modal-mask" @click.self="close">
    <div class="o-modal team-browser">
      <div class="browser-head">
        <div class="o-modal__title">其他团队</div>
        <button class="o-btn o-btn--sm" @click="close">关闭</button>
      </div>

      <div class="o-operate-bar" style="margin-bottom: 14px">
        <div class="o-operate-left">
          <input v-model="kw" class="o-input" placeholder="搜索团队..." />
        </div>
        <div class="o-operate-right">
          <button v-permission="'team:create'" class="o-btn o-btn--primary" @click="showCreate = !showCreate">
            + 创建团队
          </button>
        </div>
      </div>

      <div v-if="showCreate" class="create-panel">
        <div class="o-form-item">
          <label class="o-form-item__label required">团队名称</label>
          <input v-model="form.name" class="o-input" placeholder="如：广州机房 C 区运维组" />
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label">描述</label>
          <input v-model="form.desc" class="o-input" placeholder="职责、负责范围" />
        </div>
        <div class="o-modal__foot">
          <button class="o-btn" @click="showCreate = false">取消</button>
          <button class="o-btn o-btn--primary" :disabled="creating" @click="create">{{ creating ? '创建中…' : '创建' }}</button>
        </div>
      </div>

      <div v-if="actionErr" class="o-badge o-badge--danger" style="margin-bottom: 12px">{{ actionErr }}</div>

      <div v-if="!filtered.length" class="o-empty">
        <div class="icon"><Icon name="users" :size="28" /></div>
        <div>暂无团队</div>
      </div>

      <div v-else class="browser-grid">
        <div v-for="t in filtered" :key="t.id" class="o-card team-card">
          <div class="team-head">
            <div class="team-name" :title="t.name">{{ t.name }}</div>
            <span v-if="myRole(t)" class="o-badge o-badge--brand">我 · {{ teamRoleById(myRole(t)!)?.name }}</span>
          </div>
          <div class="team-desc" :title="t.desc">{{ t.desc || '—' }}</div>
          <div class="team-stats">
            <span><b>{{ t.memberCount }}</b> 成员</span>
            <span><b>{{ t.taskCount }}</b> 任务</span>
          </div>
          <div class="team-foot">
            <span class="o-mono o-text-3">建于 {{ (t.createdAt || '').slice(0, 10) }}</span>
            <button v-if="canEnter(t)" class="o-btn o-btn--sm o-btn--primary enter-btn" @click="enter(t)">
              <span class="btn-ico">进入<Icon name="arrow-right" :size="12" /></span>
            </button>
            <button
              v-else
              class="o-btn o-btn--sm"
              :disabled="hasPendingApply(t)"
              @click="applyJoin(t)"
            >
              {{ hasPendingApply(t) ? '申请中...' : '+ 申请加入' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.team-browser {
  width: 980px;
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 120px);
  overflow: auto;
}
.browser-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.browser-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));
  gap: 14px;
}
.team-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px;
}
.team-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.team-name {
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.team-desc {
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 42px;
}
.team-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
  color: var(--text-2);
}
.team-stats b {
  color: var(--text-1);
  font-weight: 600;
}
.team-foot {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.btn-ico {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.enter-btn {
  min-width: 88px;
  justify-content: center;
}
.create-panel {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  margin-bottom: 16px;
  background: var(--surface-2, var(--surface));
}
</style>
