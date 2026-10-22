<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { router } from '@/router'
import { useRbacStore } from '@/stores/rbac'
import { useTeamsStore } from '@/stores/teams'
import { teamRoleById } from '@/rbac/permissions'
import { fmtTime } from '@/parser/types'
import { apiError } from '@/api/http'
import Icon from '@/components/Icon.vue'

const rr = useRouter()
const rbac = useRbacStore()
const teamsStore = useTeamsStore()

const menus = computed(() =>
  router
    .getRoutes()
    .filter((r) => r.meta?.menu && !r.meta?.hidden && r.meta?.perm && rbac.hasPerm(r.meta.perm as string))
    .map((r) => ({ path: r.path, title: r.meta.title as string })),
)

const user = computed(() => rbac.currentUser)
/* 我在任一团队中的管理员身份 (本地设计: 头像旁展示团队角色) */
const myTeamRole = computed(() => {
  for (const [tid, rid] of Object.entries(rbac.myTeamRoles)) {
    if (rid === 'team_admin' && teamsStore.teamById(tid)) return '管理员'
  }
  return ''
})
/* 我加入的团队数 (来自 /auth/me team_roles), 用于下拉入口计数 */
const myTeamCount = computed(() => Object.keys(rbac.myTeamRoles).length)

/* ---------- 头像下拉 + 通知中心 ---------- */
const menuOpen = ref(false)
const noticeOpen = ref(false)

const myNotices = computed(() => teamsStore.notices)
const noticeCount = computed(() => myNotices.value.length)
const resolveErr = ref('')

onMounted(() => {
  teamsStore.fetchNotices().catch(() => {})
})

async function resolve(n: { id: number; type: string; fromUserName: string }, accept: boolean) {
  resolveErr.value = ''
  try {
    await teamsStore.resolveNotice(n.id, accept)
    await teamsStore.fetchNotices()
  } catch (e) {
    resolveErr.value = apiError(e)
  }
}

async function logout() {
  menuOpen.value = false
  noticeOpen.value = false
  await rbac.logout()
  rr.push('/login')
}

/* 团队快捷入口: 头像下拉 */
function gotoTeams() {
  menuOpen.value = false
  rr.push('/teams')
}
</script>

<template>
  <div class="shell" @click="menuOpen = false; noticeOpen = false">
    <header class="topbar" @click.stop>
      <div class="left">
        <span class="logo" @click="rr.push('/dashboard')">log-insight</span>
        <nav class="nav">
          <router-link
            v-for="m in menus"
            :key="m.path"
            :to="m.path"
            class="nav-item"
            active-class="is-active"
          >
            {{ m.title }}
          </router-link>
        </nav>
      </div>
      <div class="right" @click.stop>
        <!-- 通知 -->
        <div class="bell-wrap">
          <button class="bell" :class="{ 'has': noticeCount }" title="申请与邀请" @click="noticeOpen = !noticeOpen; menuOpen = false">
            <Icon name="bell" :size="16" />
            <span v-if="noticeCount" class="dot">{{ noticeCount }}</span>
          </button>
          <div v-if="noticeOpen" class="notice-panel">
            <div class="notice-head">
              <b>申请与邀请</b>
              <span class="o-mono o-text-3">{{ noticeCount }} 条待处理</span>
            </div>
            <div v-if="resolveErr" class="notice-err"><Icon name="alert" :size="13" /> {{ resolveErr }}</div>
            <div v-if="!myNotices.length" class="o-empty" style="padding: 28px 0">暂无待处理的申请或邀请</div>
            <div v-for="n in myNotices" :key="n.id" class="notice-item">
              <div class="notice-body">
                <template v-if="n.type === 'invite'">
                  <b>{{ n.fromUserName }}</b> 邀请你加入
                  <b>{{ n.teamName }}</b>
                  <span class="o-badge o-badge--brand">{{ teamRoleById(n.role)?.name }}</span>
                </template>
                <template v-else>
                  <b>{{ n.fromUserName }}</b> 申请加入
                  <b>{{ n.teamName }}</b>
                </template>
                <div class="o-mono o-text-3">{{ fmtTime(n.createdAt) }}</div>
              </div>
              <div class="notice-actions">
                <button class="o-btn o-btn--sm o-btn--primary" @click="resolve(n, true)">同意</button>
                <button class="o-btn o-btn--sm" @click="resolve(n, false)">拒绝</button>
              </div>
            </div>
          </div>
        </div>

        <!-- 头像菜单 -->
        <div class="user-wrap">
          <button class="user-btn" @click="menuOpen = !menuOpen; noticeOpen = false">
            <span class="avatar" :style="{ background: `hsl(${user?.avatarHue ?? 210}, 65%, 50%)` }">{{
              user?.name.slice(0, 1)
            }}</span>
            <span class="name">{{ user?.name }}</span>
            <span v-if="myTeamRole" class="o-badge o-badge--brand">{{ myTeamRole }}</span>
            <span v-else-if="user?.dept" class="o-badge o-badge--outline">{{ user?.dept }}</span>
          </button>
          <div v-if="menuOpen" class="user-menu">
            <div class="user-info">
              <b>{{ user?.name }}</b>
              <span class="o-mono o-text-3">{{ user?.email }}</span>
              <span class="o-mono o-text-3">{{ user?.dept }}{{ user?.title ? ' · ' + user.title : '' }}</span>
            </div>
            <button class="menu-row" @click="gotoTeams">
              我的团队
              <span v-if="myTeamCount" class="o-badge o-badge--outline">{{ myTeamCount }}</span>
            </button>
            <button class="menu-row" @click="noticeOpen = true; menuOpen = false">
              申请与邀请 <span v-if="noticeCount" class="o-badge o-badge--danger">{{ noticeCount }}</span>
            </button>
            <button class="menu-row danger" @click="logout">退出登录</button>
          </div>
        </div>
      </div>
    </header>

    <main class="main" @click="noticeOpen = false; menuOpen = false">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
}
.topbar {
  height: var(--topbar-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
}
.left {
  display: flex;
  align-items: center;
  gap: 28px;
  min-width: 0;
}
.logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary);
  white-space: nowrap;
  cursor: pointer;
}
.nav {
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
}
.nav-item {
  padding: 6px 14px;
  border-radius: var(--radius);
  color: var(--text-2);
  font-size: 14px;
  white-space: nowrap;
}
.nav-item:hover {
  color: var(--primary);
  background: var(--primary-soft);
}
.nav-item.is-active {
  color: var(--primary);
  background: var(--primary-soft);
  font-weight: 600;
}
.right {
  display: flex;
  align-items: center;
  gap: 14px;
}
.bell-wrap, .user-wrap {
  position: relative;
}
.bell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
  position: relative;
  padding: 4px 6px;
  border-radius: var(--radius);
}
.bell:hover {
  background: var(--primary-soft);
}
.bell .dot {
  position: absolute;
  top: -2px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--danger);
  color: #fff;
  font: 600 10px/16px var(--mono);
}
.notice-panel {
  position: absolute;
  right: 0;
  top: calc(100% + 10px);
  width: 400px;
  max-width: calc(100vw - 32px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  z-index: 200;
  max-height: 460px;
  overflow: auto;
}
.notice-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  font-size: 14px;
}
.notice-err {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  font-size: 12.5px;
  color: var(--danger);
  border-bottom: 1px solid var(--border);
}
.notice-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
}
.notice-item:last-child {
  border-bottom: 0;
}
.notice-body {
  font-size: 13px;
  line-height: 1.7;
}
.notice-actions {
  display: flex;
  gap: 8px;
  flex: none;
}
.user-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius);
  font: inherit;
}
.user-btn:hover {
  background: var(--primary-soft);
}
.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
}
.name {
  font-size: 13px;
  font-weight: 600;
}
.user-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 10px);
  width: 240px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  z-index: 200;
  overflow: hidden;
}
.user-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.menu-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  border: none;
  background: transparent;
  padding: 12px 18px;
  font: inherit;
  font-size: 13.5px;
  cursor: pointer;
  text-align: left;
}
.menu-row:hover {
  background: var(--primary-soft);
  color: var(--primary);
}
.menu-row.danger:hover {
  color: var(--danger);
  background: var(--danger-soft);
}
.main {
  max-width: 1400px;
  margin: 0 auto;
  padding: 80px 24px 24px;
  min-height: calc(100vh - var(--topbar-h));
}
</style>
