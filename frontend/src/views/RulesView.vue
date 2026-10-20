<script setup lang="ts">
/* 规则管理: 平台通用规则 + 各团队专属规则 CRUD, 版本化热加载
   - 通用规则(team_id=NULL): 平台 rule:manage 权限维护
   - 团队规则(team_id=X): 团队 owner/team_admin 维护, 执行时同名覆盖通用规则 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post, put, del, apiError } from '@/api/http'
import { fmtTime } from '@/parser/types'
import { useRbacStore } from '@/stores/rbac'
import { useTeamsStore } from '@/stores/teams'

const rbac = useRbacStore()
const teamsStore = useTeamsStore()
const canManage = computed(() => rbac.hasPerm('rule:manage'))
const route = useRoute()
const router = useRouter()

interface RuleRow {
  id: number
  name: string
  description: string
  matchType: 'keyword' | 'regex' | 'threshold' | 'script'
  pattern: string
  threshold: Record<string, any>
  scriptPath: string
  scriptParams: Record<string, any>
  scriptTimeout: number
  severity: 'info' | 'warn' | 'error' | 'critical'
  logType: string
  serverType: string
  enabled: boolean
  priority: number
  version: number
  isBuiltin: boolean
  teamId: number | null
  createdAt: string
}

const MATCH_LABEL: Record<RuleRow['matchType'], string> = {
  keyword: '关键字', regex: '正则', threshold: '阈值', script: '脚本',
}
const MATCH_BADGE: Record<RuleRow['matchType'], string> = {
  keyword: 'o-badge--outline', regex: 'o-badge--primary', threshold: 'o-badge--warning',
  script: 'o-badge--brand',
}
const SEV_LABEL: Record<RuleRow['severity'], string> = { info: '提示', warn: '警告', error: '错误', critical: '严重' }
const SEV_BADGE: Record<RuleRow['severity'], string> = {
  info: 'o-badge--grey', warn: 'o-badge--warning', error: 'o-badge--danger', critical: 'o-badge--danger',
}
const OPS = ['>', '>=', '<', '<=', '==', '!=']

const rules = ref<RuleRow[]>([])
const loading = ref(false)
const loadErr = ref('')
const scriptOptions = ref<{ path: string; doc: string }[]>([])

/* ---------- 作用域: null=平台通用规则; 数字=某团队 ---------- */
const scopeTeamId = ref<number | null>(null)
const isTeamScope = computed(() => scopeTeamId.value != null)
/* 可选团队: 平台管理员全部可见, 否则仅我所属团队 */
const myTeams = computed(() => teamsStore.teams.filter(
  t => rbac.isPlatformAdmin || teamsStore.myRoleOf(t.id)))
/* 当前作用域是否具备团队规则写权限 (owner/team_admin 或平台管理员) */
const canManageTeam = computed(() => isTeamScope.value &&
  (rbac.isPlatformAdmin || ['owner', 'team_admin'].includes(teamsStore.myRoleOf(scopeTeamId.value!) ?? '')))
const canCreate = computed(() => (isTeamScope.value ? canManageTeam.value : canManage.value))
/* 团队作用域不开放脚本类型 */
const MATCH_TYPES = computed(() => {
  const all = Object.entries(MATCH_LABEL) as [RuleRow['matchType'], string][]
  return isTeamScope.value ? all.filter(([k]) => k !== 'script') : all
})

function teamName(id: number | null): string {
  return id == null ? '通用' : (teamsStore.teamById(id)?.name ?? `团队#${id}`)
}
/* 行可编辑: 通用规则仅在平台作用域可编辑; 团队规则需本团队管理权限 */
function canEditRow(row: RuleRow): boolean {
  if (row.teamId == null) return !isTeamScope.value && canManage.value
  return canManageTeam.value && row.teamId === scopeTeamId.value
}
/* 当前作用域下的规则接口前缀 */
function ruleBase(): string {
  return isTeamScope.value ? `/teams/${scopeTeamId.value}/rules` : '/rules'
}

async function load() {
  loading.value = true
  loadErr.value = ''
  try {
    const q = scopeTeamId.value != null ? `?team_id=${scopeTeamId.value}` : ''
    rules.value = await get<RuleRow[]>(`/rules${q}`)
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    loading.value = false
  }
}
watch(scopeTeamId, () => load())
onMounted(() => {
  load()
  teamsStore.fetchTeams().catch(() => { /* 团队列表不可用时仅展示通用规则 */ })
  if (canManage.value) {
    get<{ path: string; doc: string }[]>('/rules/scripts')
      .then(l => { scriptOptions.value = l })
      .catch(() => { /* 脚本目录为空或不可用时保持空建议 */ })
  }
  applyPrefill()
})

/* ---------- 编辑弹窗 ---------- */
const showEdit = ref(false)
const editingId = ref<number | null>(null)
const saveErr = ref('')
const saving = ref(false)

const emptyForm = () => ({
  name: '',
  description: '',
  match_type: 'keyword' as RuleRow['matchType'],
  pattern: '',
  threshold: { metric: '', op: '>', value: 0 },
  script_path: '',
  script_params: '',
  script_timeout: 5,
  severity: 'warn' as RuleRow['severity'],
  log_type: '',
  server_type: '',
  enabled: true,
  priority: 100,
})
const form = reactive(emptyForm())

function openEdit(row?: RuleRow) {
  saveErr.value = ''
  editingId.value = row?.id ?? null
  Object.assign(form, emptyForm(), row ? {
    name: row.name, description: row.description, match_type: row.matchType,
    pattern: row.pattern, threshold: { ...row.threshold }, severity: row.severity,
    log_type: row.logType, server_type: row.serverType, enabled: row.enabled,
    priority: row.priority,
    script_path: row.scriptPath || '',
    script_params: row.scriptParams && Object.keys(row.scriptParams).length
      ? JSON.stringify(row.scriptParams, null, 2) : '',
    script_timeout: row.scriptTimeout ?? 5,
  } : {})
  showEdit.value = true
}

function pickMatch(t: RuleRow['matchType']) {
  form.match_type = t
}

/* 从「智能巡检规则推荐」接纳跳转而来: 打开新增弹窗并预填推荐内容 */
function applyPrefill() {
  const q = route.query
  if (q.prefill !== '1' || !canManage.value) return
  const severity = String(q.severity ?? 'warn')
  scopeTeamId.value = null   // 规则推荐接纳 → 落到平台通用规则
  saveErr.value = ''
  editingId.value = null
  Object.assign(form, emptyForm(), {
    name: typeof q.name === 'string' ? q.name : '',
    description: typeof q.description === 'string' ? q.description : '',
    match_type: (q.match_type === 'regex' ? 'regex' : 'keyword') as RuleRow['matchType'],
    pattern: typeof q.pattern === 'string' ? q.pattern : '',
    severity: (['info', 'warn', 'error', 'critical'].includes(severity) ? severity : 'warn') as RuleRow['severity'],
  })
  showEdit.value = true
  router.replace({ path: '/rules' })   // 清除 query, 避免刷新重复弹窗
}

async function save() {
  if (!form.name.trim()) {
    saveErr.value = '请填写规则名称'
    return
  }
  if (form.match_type === 'script') {
    if (!form.script_path.trim()) {
      saveErr.value = '请填写脚本路径'
      return
    }
    if (!form.script_path.trim().endsWith('.py')) {
      saveErr.value = '脚本路径必须以 .py 结尾'
      return
    }
  }
  saveErr.value = ''
  saving.value = true
  try {
    const body: Record<string, any> = {
      name: form.name.trim(), description: form.description, match_type: form.match_type,
      severity: form.severity, log_type: form.log_type, server_type: form.server_type,
      enabled: form.enabled, priority: Number(form.priority) || 1,
    }
    if (form.match_type === 'threshold') {
      body.pattern = ''
      body.threshold = { metric: form.threshold.metric, op: form.threshold.op, value: Number(form.threshold.value) }
    } else if (form.match_type === 'script') {
      body.pattern = ''
      body.threshold = {}
      body.script_path = form.script_path.trim()
      body.script_timeout = Number(form.script_timeout) || 0
      body.script_params = {}
      if (form.script_params.trim()) {
        try {
          const parsed = JSON.parse(form.script_params)
          if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
            throw new Error('not object')
          }
          body.script_params = parsed
        } catch {
          saveErr.value = '参数需为合法 JSON 对象，如 {"threshold": 3}'
          return
        }
      }
    } else {
      body.pattern = form.pattern
      body.threshold = {}
    }
    if (editingId.value) await put(`${ruleBase()}/${editingId.value}`, body)
    else await post(ruleBase(), body)
    showEdit.value = false
    await load()
  } catch (e) {
    saveErr.value = apiError(e)
  } finally {
    saving.value = false
  }
}

/* 快捷启用/停用: 不必进编辑弹窗; 接口为整体更新, 故按当前行回填全部字段 */
const togglingId = ref<number | null>(null)
async function toggleEnabled(row: RuleRow) {
  togglingId.value = row.id
  loadErr.value = ''
  try {
    await put(`${ruleBase()}/${row.id}`, {
      name: row.name, description: row.description, match_type: row.matchType,
      pattern: row.pattern, threshold: row.threshold ?? {},
      script_path: row.scriptPath || '', script_params: row.scriptParams ?? {},
      script_timeout: row.scriptTimeout ?? 5,
      severity: row.severity, log_type: row.logType, server_type: row.serverType,
      enabled: !row.enabled, priority: row.priority,
    })
    await load()
  } catch (e) {
    loadErr.value = apiError(e)
  } finally {
    togglingId.value = null
  }
}

async function remove(row: RuleRow) {
  const tip = row.isBuiltin ? '\n（内置规则，删除后如需恢复需手动重建）' : ''
  if (!window.confirm(`确认删除规则「${row.name}」？${tip}`)) return
  loadErr.value = ''
  try {
    await del(`${ruleBase()}/${row.id}`)
    await load()
  } catch (e) {
    loadErr.value = apiError(e)
  }
}

/* ---------- 规则详情弹窗 (点击匹配内容查看完整信息; 脚本类型拉取源码) ---------- */
const showDetail = ref(false)
const detailRule = ref<RuleRow | null>(null)
const scriptContent = ref('')
const scriptLoading = ref(false)
const scriptErr = ref('')

async function openDetail(row: RuleRow) {
  detailRule.value = row
  scriptContent.value = ''
  scriptErr.value = ''
  showDetail.value = true
  if (row.matchType === 'script' && row.scriptPath) {
    scriptLoading.value = true
    try {
      const r = await get<{ path: string; content: string }>(
        `/rules/scripts/content?path=${encodeURIComponent(row.scriptPath)}`)
      scriptContent.value = r.content
    } catch (e) {
      scriptErr.value = apiError(e)
    } finally {
      scriptLoading.value = false
    }
  }
}

function editFromDetail() {
  const r = detailRule.value
  if (!r) return
  showDetail.value = false
  openEdit(r)
}
</script>

<template>
  <div class="o-operate-bar">
    <div class="o-operate-left">
      <h2>巡检 / 诊断规则</h2>
      <span class="o-mono o-text-3">
        {{ isTeamScope ? '通用规则(只读) + 本团队专属规则；执行时团队规则同名覆盖通用' : '平台通用规则，全体团队共享，版本化热加载无需重启' }}
      </span>
    </div>
    <div class="o-operate-right">
      <select v-model="scopeTeamId" class="o-select" style="width: 200px">
        <option :value="null">通用规则（平台）</option>
        <option v-for="t in myTeams" :key="t.id" :value="t.id">{{ t.name }}</option>
      </select>
      <button v-if="canCreate" class="o-btn o-btn--primary" @click="openEdit()">＋ 新增规则</button>
    </div>
  </div>

  <div v-if="loadErr" class="o-mono" style="color: var(--danger); margin-bottom: 12px">⚠ {{ loadErr }}</div>

  <div class="o-card">
    <div class="o-table-wrap">
      <table class="o-table">
        <thead>
          <tr>
            <th>名称</th><th>归属</th><th>匹配类型</th><th>匹配内容</th><th>级别</th>
            <th>优先级</th><th>版本</th><th>状态</th><th>更新时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rules.length && !loading">
            <td colspan="10"><div class="o-empty" style="padding: 48px 0">📭 暂无规则</div></td>
          </tr>
          <tr v-for="r in rules" :key="r.id">
            <td>
              <b>{{ r.name }}</b>
              <span v-if="r.isBuiltin" class="o-badge o-badge--brand" style="margin-left: 6px">内置</span>
              <div v-if="r.description" class="o-text-3 desc">{{ r.description }}</div>
            </td>
            <td>
              <span class="o-badge" :class="r.teamId == null ? 'o-badge--outline' : 'o-badge--brand'">
                {{ teamName(r.teamId) }}
              </span>
            </td>
            <td><span class="o-badge" :class="MATCH_BADGE[r.matchType]">{{ MATCH_LABEL[r.matchType] }}</span></td>
            <td class="pattern-cell" title="点击查看完整规则信息" @click="openDetail(r)">
              <span class="o-mono pattern pattern-link">
                <template v-if="r.matchType === 'keyword' || r.matchType === 'regex'">{{ r.pattern || '—' }}</template>
                <template v-else-if="r.matchType === 'threshold'">
                  {{ r.threshold?.metric }} {{ r.threshold?.op }} {{ r.threshold?.value }}
                </template>
                <template v-else>{{ r.scriptPath || '—' }}</template>
              </span>
            </td>
            <td><span class="o-badge" :class="SEV_BADGE[r.severity]">{{ SEV_LABEL[r.severity] }}</span></td>
            <td class="o-mono">{{ r.priority }}</td>
            <td class="o-mono">v{{ r.version }}</td>
            <td>
              <button
                v-if="canEditRow(r)"
                type="button"
                class="o-badge status-toggle"
                :class="r.enabled ? 'o-badge--success' : 'o-badge--grey'"
                :disabled="togglingId === r.id"
                :title="r.enabled ? '点击停用 · 停用后不再参与匹配，可随时重新启用' : '点击启用 · 启用后立即参与匹配'"
                @click="toggleEnabled(r)"
              >{{ togglingId === r.id ? '…' : (r.enabled ? '启用' : '停用') }}</button>
              <span v-else class="o-badge" :class="r.enabled ? 'o-badge--success' : 'o-badge--grey'">
                {{ r.enabled ? '启用' : '停用' }}
              </span>
            </td>
            <td class="o-mono o-text-3">{{ fmtTime(r.createdAt) }}</td>
            <td class="actions">
              <template v-if="canEditRow(r)">
                <button class="o-btn o-btn--text o-btn--sm" @click="openEdit(r)">编辑</button>
                <button
                  class="o-btn o-btn--text o-btn--sm danger"
                  :title="r.isBuiltin ? '内置规则，删除后如需恢复需手动重建' : '删除该规则'"
                  @click="remove(r)"
                >删除</button>
              </template>
              <span v-else class="o-text-3">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 新增/编辑弹窗 -->
  <div v-if="showEdit" class="o-modal-mask" @click.self="showEdit = false">
    <div class="o-modal">
      <div class="o-modal__title">
        {{ editingId ? '编辑规则' : '新增规则' }}
        <span class="o-badge" :class="isTeamScope ? 'o-badge--brand' : 'o-badge--outline'" style="margin-left: 6px">
          {{ isTeamScope ? teamName(scopeTeamId) : '平台通用' }}
        </span>
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label required">名称</label>
        <input v-model="form.name" class="o-input" placeholder="规则名称，如：OOM 错误检测" />
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label">描述</label>
        <textarea v-model="form.description" class="o-textarea" rows="2" placeholder="规则用途说明（可选）"></textarea>
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label required">匹配类型</label>
        <div class="match-chips">
          <button
            v-for="[key, label] in MATCH_TYPES"
            :key="key"
            class="match-chip"
            :class="{ active: form.match_type === key }"
            @click="pickMatch(key)"
          >{{ label }}</button>
        </div>
      </div>
      <div v-if="form.match_type === 'keyword' || form.match_type === 'regex'" class="o-form-item">
        <label class="o-form-item__label required">匹配内容</label>
        <input
          v-model="form.pattern" class="o-input mono-input"
          :placeholder="form.match_type === 'regex' ? '正则表达式，如 ERROR|PANIC' : '关键字，如 Out of memory'"
        />
      </div>
      <template v-else-if="form.match_type === 'threshold'">
        <div class="o-form-item">
          <label class="o-form-item__label required">指标名</label>
          <input v-model="form.threshold.metric" class="o-input mono-input" placeholder="如 cpu.usage" />
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label required">比较符</label>
          <select v-model="form.threshold.op" class="o-select" style="width: 120px">
            <option v-for="op in OPS" :key="op" :value="op">{{ op }}</option>
          </select>
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label required">阈值</label>
          <input v-model.number="form.threshold.value" type="number" class="o-input" style="width: 160px" />
        </div>
      </template>
      <template v-else>
        <div class="o-form-item">
          <label class="o-form-item__label required">脚本路径</label>
          <input
            v-model="form.script_path" class="o-input mono-input" list="rule-script-list"
            placeholder="如 oom_burst.py（相对 rules_scripts/ 目录）"
          />
          <datalist id="rule-script-list">
            <option v-for="s in scriptOptions" :key="s.path" :value="s.path">{{ s.doc }}</option>
          </datalist>
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label">脚本参数</label>
          <textarea
            v-model="form.script_params" class="o-textarea mono-input" rows="3"
            placeholder='JSON 对象，如 {"window_seconds": 60, "threshold": 3}（可选）'
          ></textarea>
        </div>
        <div class="o-form-item">
          <label class="o-form-item__label">超时</label>
          <input v-model.number="form.script_timeout" type="number" min="0" class="o-input" style="width: 120px" />
          <span class="o-mono o-text-3" style="align-self: center; margin-left: 8px">秒，0 表示用全局默认</span>
        </div>
        <div class="o-form-item">
          <span class="o-text-3" style="font-size: 12.5px; line-height: 1.7">
            脚本需定义 <code class="o-mono">check(ctx)</code> 入口函数，返回
            <code class="o-mono">{{ '{' }}findings: [...]{{ '}' }}</code>；由 Worker 以独立子进程执行。
          </span>
        </div>
      </template>
      <div class="o-form-item">
        <label class="o-form-item__label">级别</label>
        <select v-model="form.severity" class="o-select" style="width: 140px">
          <option v-for="(label, key) in SEV_LABEL" :key="key" :value="key">{{ label }}</option>
        </select>
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label">优先级</label>
        <input v-model.number="form.priority" type="number" min="1" max="999" class="o-input" style="width: 120px" />
        <span class="o-mono o-text-3" style="align-self: center; margin-left: 8px">数值越小越先匹配</span>
      </div>
      <div class="o-form-item">
        <label class="o-form-item__label">启用</label>
        <label class="switch">
          <input v-model="form.enabled" type="checkbox" />
          <span>{{ form.enabled ? '启用中' : '已停用' }}</span>
        </label>
      </div>
      <div v-if="saveErr" class="o-mono" style="color: var(--danger); font-size: 12.5px">⚠ {{ saveErr }}</div>
      <div class="o-modal__foot">
        <button class="o-btn" @click="showEdit = false">取消</button>
        <button class="o-btn o-btn--primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>
  </div>

  <!-- 规则详情弹窗 -->
  <div v-if="showDetail && detailRule" class="o-modal-mask" @click.self="showDetail = false">
    <div class="o-modal detail-modal">
      <div class="o-modal__title">
        规则详情
        <span class="o-badge" :class="MATCH_BADGE[detailRule.matchType]" style="margin-left: 6px">
          {{ MATCH_LABEL[detailRule.matchType] }}
        </span>
      </div>

      <div class="detail-grid">
        <div class="dt-k">名称</div>
        <div class="dt-v">
          <b>{{ detailRule.name }}</b>
          <span v-if="detailRule.isBuiltin" class="o-badge o-badge--brand" style="margin-left: 6px">内置</span>
        </div>
        <div class="dt-k">描述</div>
        <div class="dt-v">{{ detailRule.description || '—' }}</div>
        <div class="dt-k">级别</div>
        <div class="dt-v">
          <span class="o-badge" :class="SEV_BADGE[detailRule.severity]">{{ SEV_LABEL[detailRule.severity] }}</span>
        </div>
        <div class="dt-k">优先级</div>
        <div class="dt-v o-mono">{{ detailRule.priority }}</div>
        <div class="dt-k">版本</div>
        <div class="dt-v o-mono">v{{ detailRule.version }}</div>
        <div class="dt-k">状态</div>
        <div class="dt-v">
          <span class="o-badge" :class="detailRule.enabled ? 'o-badge--success' : 'o-badge--grey'">
            {{ detailRule.enabled ? '启用' : '停用' }}
          </span>
        </div>
        <div class="dt-k">日志类型</div>
        <div class="dt-v o-mono">{{ detailRule.logType || '不限' }}</div>
        <div class="dt-k">服务器类型</div>
        <div class="dt-v o-mono">{{ detailRule.serverType || '不限' }}</div>
        <div class="dt-k">创建时间</div>
        <div class="dt-v o-mono o-text-3">{{ fmtTime(detailRule.createdAt) }}</div>
      </div>

      <div class="dt-section-title">匹配内容</div>
      <template v-if="detailRule.matchType === 'keyword' || detailRule.matchType === 'regex'">
        <pre class="code-block">{{ detailRule.pattern || '（空）' }}</pre>
      </template>
      <template v-else-if="detailRule.matchType === 'threshold'">
        <pre class="code-block">{{ detailRule.threshold?.metric }} {{ detailRule.threshold?.op }} {{ detailRule.threshold?.value }}</pre>
      </template>
      <template v-else>
        <div class="dt-script-meta">
          <span class="o-text-3">脚本路径</span>
          <span class="o-mono">{{ detailRule.scriptPath || '—' }}</span>
          <span class="o-text-3" style="margin-left: 12px">超时</span>
          <span class="o-mono">{{ detailRule.scriptTimeout ? detailRule.scriptTimeout + 's' : '默认' }}</span>
        </div>
        <div
          v-if="detailRule.scriptParams && Object.keys(detailRule.scriptParams).length"
          class="dt-script-meta"
        >
          <span class="o-text-3">参数</span>
          <span class="o-mono">{{ JSON.stringify(detailRule.scriptParams) }}</span>
        </div>
        <div v-if="scriptLoading" class="o-text-3" style="padding: 12px 0; font-size: 13px">脚本内容加载中…</div>
        <div v-else-if="scriptErr" class="o-mono" style="color: var(--danger); font-size: 12.5px">⚠ {{ scriptErr }}</div>
        <pre v-else class="code-block code-scroll">{{ scriptContent || '（脚本内容为空）' }}</pre>
      </template>

      <div class="o-modal__foot">
        <button v-if="canEditRow(detailRule)" class="o-btn" @click="editFromDetail">编辑</button>
        <button class="o-btn o-btn--primary" @click="showDetail = false">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.desc {
  font-size: 12px;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pattern {
  display: inline-block;
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  font-size: 12px;
}
.actions {
  white-space: nowrap;
  text-align: right;
}
.actions .danger {
  color: var(--danger);
}
/* 状态列即开关: 徽标样式可点击切换启用/停用 */
.status-toggle {
  font-family: inherit;
  cursor: pointer;
  transition: box-shadow 0.15s;
}
.status-toggle:hover:not(:disabled) {
  box-shadow: 0 0 0 3px var(--primary-soft);
}
.status-toggle:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.status-toggle:disabled {
  opacity: 0.5;
  cursor: default;
}
.mono-input {
  font-family: var(--mono);
}
.match-chips {
  display: flex;
  gap: 8px;
}
.match-chip {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: var(--radius);
  padding: 7px 18px;
  font: inherit;
  font-size: 13.5px;
  cursor: pointer;
  color: var(--text-2);
}
.match-chip:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.match-chip.active {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: var(--text-2);
  cursor: pointer;
}
.pattern-link {
  cursor: pointer;
  border-bottom: 1px dashed var(--border);
}
.pattern-link:hover {
  color: var(--primary);
  border-bottom-color: var(--primary);
}
.pattern-cell {
  cursor: pointer;
}
.detail-modal {
  width: 760px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 96px 1fr;
  row-gap: 9px;
  column-gap: 12px;
  font-size: 13px;
}
.dt-k {
  color: var(--text-3);
}
.dt-v {
  color: var(--text-2);
  word-break: break-all;
}
.dt-section-title {
  font-weight: 600;
  font-size: 13.5px;
  margin: 16px 0 10px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.dt-script-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12.5px;
  margin-bottom: 8px;
}
.code-block {
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-2);
  background: #fbfcfe;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
.code-scroll {
  max-height: 420px;
  overflow: auto;
}
</style>
