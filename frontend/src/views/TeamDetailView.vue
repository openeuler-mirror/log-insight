<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTeamsStore, type TeamDetail } from '@/stores/teams'
import { useRbacStore } from '@/stores/rbac'
import { fmtTime } from '@/parser/types'
import { TEAM_ROLES, teamRoleById } from '@/rbac/permissions'
import { apiError } from '@/api/http'
import Icon from '@/components/Icon.vue'
import TeamRolesPanel from '@/components/TeamRolesPanel.vue'
import TeamAuditPanel from '@/components/TeamAuditPanel.vue'
import TeamLlmPanel from '@/components/team/TeamLlmPanel.vue'
import TeamUsagePanel from '@/components/team/TeamUsagePanel.vue'
import RulesView from '@/views/RulesView.vue'

const route = useRoute()
const teamsStore = useTeamsStore()
const rbac = useRbacStore()

/* 团队管理直接展示顶栏当前团队的详情; /teams/:id 仍支持按 id 打开 */
const routeTeamId = computed(() => String(route.params.id ?? ''))
const teamId = computed(() => routeTeamId.value || String(teamsStore.activeTeamId ?? ''))
const team = computed<TeamDetail | undefined>(() => teamsStore.details[teamId.value])
const loadErr = ref('')
/* 仅已加入团队 (或平台管理员) 才能进入 */
const canEnterTeam = computed(() =>
  !!teamId.value && (rbac.isPlatformAdmin || !!teamsStore.myRoleOf(teamId.value)),
)

async function loadTeam() {
  if (!teamId.value) return
  loadErr.value = ''
  try {
    await teamsStore.fetchTeam(teamId.value)
  } catch (e) {
    loadErr.value = apiError(e)
  }
}
onMounted(() => {
  if (!teamId.value) return
  if (canEnterTeam.value) teamsStore.setActiveTeam(Number(teamId.value))
  loadTeam()
})
watch(teamId, () => {
  if (!teamId.value) {
    loadErr.value = ''
    return
  }
  if (canEnterTeam.value) teamsStore.setActiveTeam(Number(teamId.value))
  loadTeam()
})

/* 未加入团队的申请入口 */
const gateErr = ref('')
const gateApplied = computed(() => !!team.value && teamsStore.appliedTeamIds.includes(team.value.id))
async function applyFromGate() {
  if (!team.value) return
  gateErr.value = ''
  try {
    await teamsStore.apply(team.value.id)
  } catch (e) {
    gateErr.value = apiError(e)
  }
}

/* 团队内权限（按团队成员角色） */
function can(perm: string): boolean {
  return team.value ? teamsStore.can(team.value.id, perm) : false
}
const myRole = computed(() => (team.value ? teamsStore.myRoleOf(team.value.id) : undefined))
const isApprover = computed(() => !!team.value && teamsStore.isApproverOf(team.value.id))

const TAB_KEYS = ['members', 'rules', 'llm', 'usage', 'audit'] as const
type TabKey = (typeof TAB_KEYS)[number]
const tab = ref<TabKey>((TAB_KEYS as readonly string[]).includes(route.query.tab as string) ? (route.query.tab as TabKey) : 'members')

/* 页签权限：团队加载后将无权限的外部带参页签回落到成员 */
const TAB_PERM: Partial<Record<TabKey, string>> = {
  rules: 'rule:view',
  llm: 'llm:manage',
  usage: 'llm:usage',
  audit: 'audit:view',
}
watch([tab, team], () => {
  if (!team.value) return
  const perm = TAB_PERM[tab.value]
  if (perm && !can(perm)) tab.value = 'members'
})
/* 支持从其他页面通过 ?tab= 深链直接打开指定页签 */
watch(() => route.query.tab as string, (v) => {
  if ((TAB_KEYS as readonly string[]).includes(v)) tab.value = v as TabKey
})

/* ---------- 团队设置 ---------- */
const showEdit = ref(false)
const editForm = ref({ name: '', desc: '' })
const saveErr = ref('')
function openEdit() {
  if (!team.value) return
  editForm.value = { name: team.value.name, desc: team.value.desc }
  saveErr.value = ''
  showEdit.value = true
}
async function saveTeam() {
  if (!team.value || !editForm.value.name) return
  saveErr.value = ''
  try {
    await teamsStore.updateTeam(team.value.id, editForm.value.name, editForm.value.desc)
    await loadTeam()
    showEdit.value = false
  } catch (e) {
    saveErr.value = apiError(e)
  }
}

/* ---------- 成员与审批 ---------- */
const memberErr = ref('')
const pendingApplies = computed(() => team.value?.pendingApplies ?? [])
async function resolveApply(id: number, accept: boolean) {
  memberErr.value = ''
  try {
    await teamsStore.resolveNotice(id, accept)
    await Promise.all([loadTeam(), teamsStore.fetchNotices()])
  } catch (e) {
    memberErr.value = apiError(e)
  }
}

const showRoles = ref(false)
const showInvite = ref(false)
const inviteForm = ref({ userId: '' as number | '', roleId: 'member' })
const inviteErr = ref('')
const inviteCandidates = computed(() =>
  rbac.users.filter((m) => !team.value?.members.some((x) => x.userId === m.id)),
)
watch(showInvite, (v) => {
  if (v) {
    inviteErr.value = ''
    rbac.ensureUsers().catch(() => {})
  }
})
async function invite() {
  if (!inviteForm.value.userId || !team.value) return
  inviteErr.value = ''
  try {
    await teamsStore.invite(team.value.id, inviteForm.value.userId, inviteForm.value.roleId)
    inviteForm.value = { userId: '', roleId: 'member' }
    showInvite.value = false
  } catch (e) {
    inviteErr.value = apiError(e)
  }
}
async function changeRole(userId: number, roleId: string) {
  if (!team.value) return
  memberErr.value = ''
  try {
    await teamsStore.setMemberRole(team.value.id, userId, roleId)
    await loadTeam()
  } catch (e) {
    memberErr.value = apiError(e)
  }
}
async function revoke(userId: number, name: string) {
  if (!team.value) return
  if (!window.confirm(`确认将 ${name} 移出团队？`)) return
  memberErr.value = ''
  try {
    await teamsStore.removeMember(team.value.id, userId)
    await loadTeam()
  } catch (e) {
    memberErr.value = apiError(e)
  }
}
</script>

<template>
  <div v-if="!teamId" class="o-empty o-card">
    <div class="icon"><Icon name="users" :size="28" /></div>
    <div>尚未进入团队</div>
    <button class="o-btn o-btn--primary" @click="teamsStore.openTeamBrowser()">浏览团队</button>
  </div>
  <div v-else-if="loadErr" class="o-empty o-card">
    <div class="icon"><Icon name="alert" :size="28" /></div>
    <div>{{ loadErr }}</div>
  </div>
  <template v-else-if="team">
    <div v-if="!canEnterTeam" class="o-card join-gate">
      <div class="o-card__title">尚未加入「{{ team.name }}」</div>
      <div class="o-text-3" style="font-size: 13px; line-height: 1.7">{{ team.desc || '该团队暂未填写描述' }}</div>
      <div v-if="gateErr" class="o-badge o-badge--danger" style="margin-top: 10px">{{ gateErr }}</div>
      <div class="o-row" style="gap: 8px; margin-top: 14px">
        <button v-if="!gateApplied" class="o-btn o-btn--primary" @click="applyFromGate">+ 申请加入</button>
        <span v-else class="o-badge o-badge--outline">申请中...</span>
        <button class="o-btn" @click="teamsStore.openTeamBrowser()">浏览其他团队</button>
      </div>
    </div>

    <template v-else>
    <div class="team-head-bar">
      <div class="info">
        <h2>
          {{ team.name }}
          <span v-if="myRole" class="o-badge o-badge--brand">我 · {{ teamRoleById(myRole)?.name }}</span>
          <span class="o-badge o-badge--outline">{{ team.memberCount }} 成员</span>
        </h2>
        <div class="sub">{{ team.desc }} · 建于 {{ (team.createdAt || '').slice(0, 10) }}</div>
      </div>
      <button v-if="can('team:role')" class="o-btn" @click="openEdit">团队设置</button>
    </div>

    <nav class="o-page-nav">
      <button class="o-page-nav__item" :class="{ active: tab === 'members' }" @click="tab = 'members'">
        成员 <span class="count">{{ team.memberCount }}</span>
      </button>
      <button v-if="can('rule:view')" class="o-page-nav__item" :class="{ active: tab === 'rules' }" @click="tab = 'rules'">
        规则集
      </button>
      <button v-if="can('llm:manage')" class="o-page-nav__item" :class="{ active: tab === 'llm' }" @click="tab = 'llm'">
        大模型
      </button>
      <button v-if="can('llm:usage')" class="o-page-nav__item" :class="{ active: tab === 'usage' }" @click="tab = 'usage'">
        用量
      </button>
      <button v-if="can('audit:view')" class="o-page-nav__item" :class="{ active: tab === 'audit' }" @click="tab = 'audit'">
        审计
      </button>
    </nav>

    <!-- ====== 成员 Tab ====== -->
    <section v-if="tab === 'members'">
      <!-- 待审批申请 -->
      <div v-if="pendingApplies.length && isApprover" class="o-card apply-card">
        <div class="o-card__title">
          待审批申请
          <span class="hint">{{ pendingApplies.length }} 条 · 也可在右上角通知中心处理</span>
        </div>
        <div v-if="memberErr" class="o-badge o-badge--danger" style="margin-bottom: 8px">{{ memberErr }}</div>
        <div v-for="n in pendingApplies" :key="n.id" class="apply-row">
          <div class="apply-info">
            <b>{{ n.fromUserName }}</b>
            <span class="o-mono o-text-3">{{ n.role === 'team_admin' ? '申请为管理员' : '申请加入' }} · {{ fmtTime(n.createdAt) }}</span>
          </div>
          <div class="o-row" style="gap: 8px">
            <button class="o-btn o-btn--sm o-btn--primary" @click="resolveApply(n.id, true)">同意</button>
            <button class="o-btn o-btn--sm" @click="resolveApply(n.id, false)">拒绝</button>
          </div>
        </div>
      </div>

      <div class="o-operate-bar">
        <div class="o-operate-left">
          <span class="o-text-3" style="font-size: 13px">团队内固定两角色：管理员 / 普通用户</span>
          <button class="o-btn o-btn--sm o-btn--text" @click="showRoles = true">权限说明</button>
        </div>
        <div class="o-operate-right">
          <button v-if="can('team:invite')" class="o-btn o-btn--primary" @click="showInvite = true">+ 邀请成员</button>
        </div>
      </div>

      <div class="o-table-wrap">
        <table class="o-table o-table--sticky-action">
          <thead>
            <tr><th>成员</th><th>部门</th><th>邮箱</th><th>团队角色</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-if="!team.members.length">
              <td colspan="5"><div class="o-empty">团队暂无成员</div></td>
            </tr>
            <tr v-for="m in team.members" :key="m.userId">
              <td>
                <div class="o-row" style="gap: 8px">
                  <span class="avatar" :style="{ background: `hsl(${m.avatarHue ?? 210}, 60%, 48%)` }">
                    {{ m.name.slice(0, 1) }}
                  </span>
                  <b>{{ m.name }}</b>
                </div>
              </td>
              <td class="o-text-3">{{ m.dept || '—' }}</td>
              <td class="o-mono o-text-3">{{ m.email || '—' }}</td>
              <td>
                <select
                  v-if="can('team:role') && m.userId !== team.ownerUserId"
                  class="o-select role-select"
                  :value="m.role"
                  @change="changeRole(m.userId, ($event.target as HTMLSelectElement).value)"
                >
                  <option v-for="r in TEAM_ROLES" :key="r.id" :value="r.id">{{ r.name }}</option>
                </select>
                <span v-else class="o-badge" :class="m.role === 'team_admin' ? 'o-badge--primary' : 'o-badge--outline'">
                  {{ teamRoleById(m.role)?.name ?? m.role }}
                </span>
              </td>
              <td>
                <button
                  v-if="can('team:remove') && m.userId !== team.ownerUserId"
                  class="o-btn o-btn--sm o-btn--text o-btn--danger"
                  @click="revoke(m.userId, m.name)"
                >
                  移出团队
                </button>
                <span v-else class="o-mono o-text-3">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ====== 规则集 Tab ====== -->
    <section v-else-if="tab === 'rules'">
      <RulesView :team-id="team.id" embedded />
    </section>

    <!-- ====== 大模型 Tab ====== -->
    <section v-else-if="tab === 'llm'">
      <TeamLlmPanel :team-id="team.id" />
    </section>

    <!-- ====== 用量 Tab ====== -->
    <section v-else-if="tab === 'usage'">
      <TeamUsagePanel :team-id="team.id" :can-view-all="can('llm:usage')" />
    </section>

    <!-- ====== 审计 Tab ====== -->
    <section v-else-if="tab === 'audit'">
      <TeamAuditPanel :team-id="String(team.id)" />
    </section>

    <!-- 团队设置弹窗 -->
    <div v-if="showEdit" class="o-modal-mask" @click.self="showEdit = false">
      <div class="o-modal">
        <div class="o-modal__title">团队设置 — {{ team.name }}</div>
        <div class="o-form-item">
          <label class="o-form-item__label required">团队名称</label>
          <input v-model="editForm.name" class="o-input" />
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label">描述</label>
          <input v-model="editForm.desc" class="o-input" />
        </div>
        <div v-if="saveErr" class="o-badge o-badge--danger">{{ saveErr }}</div>
        <div class="o-modal__foot">
          <button class="o-btn" @click="showEdit = false">取消</button>
          <button class="o-btn o-btn--primary" @click="saveTeam">保存</button>
        </div>
      </div>
    </div>

    <!-- 角色权限说明弹窗（成员管理中按需查看） -->
    <div v-if="showRoles" class="o-modal-mask" @click.self="showRoles = false">
      <div class="o-modal role-modal">
        <div class="o-modal__title">团队角色与权限</div>
        <TeamRolesPanel />
        <div class="o-modal__foot">
          <button class="o-btn o-btn--primary" @click="showRoles = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 邀请成员弹窗（发出邀请通知，对方在右上角确认） -->
    <div v-if="showInvite" class="o-modal-mask" @click.self="showInvite = false">
      <div class="o-modal">
        <div class="o-modal__title">邀请成员加入 {{ team.name }}</div>
        <div class="o-form-item">
          <label class="o-form-item__label required">选择用户</label>
          <select v-model="inviteForm.userId" class="o-select">
            <option :value="''" disabled>请选择</option>
            <option v-for="m in inviteCandidates" :key="m.id" :value="m.id">{{ m.name }}（{{ m.dept }} · {{ m.email }}）</option>
          </select>
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label required">团队角色</label>
          <select v-model="inviteForm.roleId" class="o-select">
            <option v-for="r in TEAM_ROLES" :key="r.id" :value="r.id">{{ r.name }} — {{ r.desc }}</option>
          </select>
        </div>
        <div v-if="inviteErr" class="o-badge o-badge--danger">{{ inviteErr }}</div>
        <div class="o-mono o-text-3" style="font-size: 12px">对方将在右上角通知中心收到邀请，确认后加入</div>
        <div class="o-modal__foot">
          <button class="o-btn" @click="showInvite = false">取消</button>
          <button class="o-btn o-btn--primary" @click="invite">发送邀请</button>
        </div>
      </div>
    </div>
    </template>
  </template>
  <div v-else class="o-empty o-card">团队加载中…</div>
</template>

<style scoped>
.team-head-bar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.team-head-bar h2 {
  font-size: 20px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.team-head-bar .sub {
  font-size: 13px;
  color: var(--text-2);
  margin-top: 4px;
}
.apply-card {
  margin-bottom: 16px;
  border-left: 3px solid var(--warning);
}
.apply-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.apply-row:last-child {
  border-bottom: 0;
}
.apply-info {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  flex-wrap: wrap;
}
.avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  flex: none;
}
.role-select {
  width: 130px;
  height: 30px;
  font-size: 13px;
}
.role-modal {
  width: 860px;
}
</style>
