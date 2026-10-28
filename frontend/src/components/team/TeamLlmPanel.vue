<script setup lang="ts">
/* 团队大模型: 供应商连接 + 启用模型清单 */
import { computed, onMounted, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import {
  deleteTeamProvider,
  getTeamRerank,
  listTeamModels,
  listTeamProviders,
  saveTeamProvider,
  saveTeamRerank,
  type TeamModel,
  type TeamProvider,
  type TeamRerankConfig,
} from '@/api/teamLlm'
import TeamModelDialog from './TeamModelDialog.vue'

const props = defineProps<{ teamId: number }>()

const providers = ref<TeamProvider[]>([])
const provider = ref('')
const apiKey = ref('')
const apiBase = ref('')
const protocol = ref('messages')
const busy = ref(false)
const message = ref('')
const failed = ref(false)
const models = ref<TeamModel[]>([])
const dialogOpen = ref(false)

const rerank = ref<TeamRerankConfig | null>(null)
const rrEnabled = ref(false)
const rrModel = ref('')
const rrBase = ref('')
const rrKey = ref('')
const rrBusy = ref(false)
const rrMsg = ref('')
const rrFailed = ref(false)

const current = computed(() => providers.value.find((p) => p.id === provider.value))
const connected = computed(() => !!current.value?.connected)
const protocols = computed(() => current.value?.protocols ?? ['messages'])
const requiresBase = computed(() => !!current.value?.requiresBase)
const enabledModels = computed(() => models.value.filter((m) => m.enabled))
const keyHint = computed(() => {
  const p = current.value
  if (p?.source === 'team') return `已保存 ${p.keyHint}；留空保持不变`
  if (p?.source === 'platform') return '当前使用平台默认 Key，填写后改用团队 Key'
  return '密钥按团队加密保存，仅在该团队内使用'
})

async function loadProviders() {
  providers.value = await listTeamProviders(props.teamId)
  if (!provider.value || !providers.value.some((p) => p.id === provider.value)) {
    provider.value = providers.value[0]?.id ?? ''
  }
  await loadModels()
}

async function loadModels() {
  if (!provider.value) {
    models.value = []
    return
  }
  try {
    models.value = await listTeamModels(props.teamId, provider.value)
  } catch {
    models.value = []
  }
}

function applyFields() {
  apiBase.value = current.value?.apiBase || current.value?.defaultBase || ''
  protocol.value = current.value?.protocol || protocols.value[0]
  apiKey.value = ''
}

watch(provider, () => {
  applyFields()
  message.value = ''
  failed.value = false
  void loadModels()
})
async function loadRerank() {
  try {
    rerank.value = await getTeamRerank(props.teamId)
    rrEnabled.value = !!rerank.value.enabled
    rrModel.value = rerank.value.model || ''
    rrBase.value = rerank.value.apiBase || ''
    rrKey.value = ''
    rrMsg.value = ''
  } catch {
    rerank.value = null
  }
}

async function saveRerank() {
  rrBusy.value = true
  rrMsg.value = ''
  rrFailed.value = false
  try {
    rerank.value = await saveTeamRerank(props.teamId, {
      enabled: rrEnabled.value,
      model: rrModel.value.trim(),
      apiBase: rrBase.value.trim(),
      apiKey: rrKey.value.trim() || undefined,
    })
    rrKey.value = ''
    rrMsg.value = 'Rerank 配置已保存'
  } catch (e) {
    rrMsg.value = apiError(e)
    rrFailed.value = true
  } finally {
    rrBusy.value = false
  }
}

watch(() => props.teamId, () => {
  void loadProviders()
  void loadRerank()
})
onMounted(async () => {
  try {
    await loadProviders()
    applyFields()
    await loadRerank()
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  }
})

async function connect() {
  const key = apiKey.value.trim()
  if (!key && !connected.value) {
    message.value = '请填写 API Key'
    failed.value = true
    return
  }
  busy.value = true
  message.value = ''
  failed.value = false
  try {
    const next = await saveTeamProvider(props.teamId, provider.value, {
      apiKey: key || undefined,
      apiBase: apiBase.value.trim(),
      protocol: protocol.value,
      enabled: true,
    })
    providers.value = providers.value.map((p) => (p.id === next.id ? { ...p, ...next } : p))
    apiKey.value = ''
    message.value = next.source === 'team'
      ? `已连接 ${next.keyHint}`
      : '已连接（平台默认 Key）'
    await loadModels()
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  if (!window.confirm('断开该供应商会同时停用其下所有模型，确认继续？')) return
  busy.value = true
  message.value = ''
  try {
    const next = await deleteTeamProvider(props.teamId, provider.value, true)
    providers.value = providers.value.map((p) => (p.id === next.id ? { ...p, ...next } : p))
    models.value = []
    message.value = '已断开连接'
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    busy.value = false
  }
}

function onModelsChanged() {
  void loadModels()
  void loadProviders().catch(() => { /* 保持当前列表 */ })
}
</script>

<template>
  <div class="llm">
    <div class="llm-head">
      <div>
        <h3>团队大模型</h3>
        <p>团队统一维护供应商连接与启用模型；成员在智能诊断输入框中选择已启用模型。</p>
      </div>
      <button
        class="o-btn o-btn--primary"
        :disabled="!connected"
        :title="connected ? '从供应商读取模型并勾选' : '请先连接供应商'"
        @click="dialogOpen = true"
      >
        选择模型
      </button>
    </div>

    <div class="llm-card">
      <div class="form-row">
        <label>供应商</label>
        <div class="row">
          <select v-model="provider" class="o-select">
            <option v-for="p in providers" :key="p.id" :value="p.id">
              {{ p.label }}{{ p.connected ? ' · 已连接' : '' }}{{ p.modelCount ? ` · ${p.modelCount} 个模型` : '' }}
            </option>
          </select>
          <span class="status">
            <span class="dot" :class="{ on: connected }"></span>
            {{ connected ? (current?.source === 'platform' ? '平台默认 Key' : `已连接 ${current?.keyHint}`) : '未连接' }}
          </span>
          <button
            v-if="current?.source === 'team'"
            class="link danger"
            :disabled="busy"
            @click="disconnect"
          >
            断开
          </button>
        </div>
        <span class="hint">{{ current?.note }}</span>
      </div>

      <div class="form-row">
        <label>API Key</label>
        <div class="row">
          <input
            v-model="apiKey"
            class="o-input"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :placeholder="connected ? '留空保持不变' : 'sk-...'"
          />
          <button class="o-btn" :disabled="busy" @click="connect">
            {{ busy ? '保存中…' : connected ? '保存连接' : '连接' }}
          </button>
        </div>
        <span class="hint">{{ keyHint }}</span>
      </div>

      <div class="form-row">
        <label>接口地址</label>
        <input v-model="apiBase" class="o-input" :placeholder="current?.defaultBase || 'https://gateway/v1'" />
        <span class="hint">{{ requiresBase ? '自定义网关必填' : '留空使用供应商默认地址' }}</span>
      </div>

      <div v-if="protocols.length > 1" class="form-row">
        <label>接口协议</label>
        <select v-model="protocol" class="o-select">
          <option value="messages">Messages（Anthropic / DeepSeek）</option>
          <option value="chat-completions">Chat Completions（OpenAI 兼容）</option>
        </select>
        <span class="hint">保存连接时生效</span>
      </div>

      <div class="form-row">
        <label>已启用模型</label>
        <div v-if="enabledModels.length" class="chips">
          <span v-for="m in enabledModels" :key="m.id" class="chip" :class="{ def: m.isDefault }">
            {{ m.displayName || m.modelId }}
            <em v-if="m.isDefault">默认</em>
          </span>
        </div>
        <span v-else class="hint">尚未选择模型；连接供应商后点击右上角「选择模型」。</span>
      </div>

      <div v-if="message" class="msg" :class="{ err: failed }">{{ message }}</div>
    </div>

    <div class="llm-card">
      <div class="form-row">
        <label>知识库 Rerank</label>
        <p class="hint" style="margin:0 0 8px">各知识库召回合并后统一打分；未配置时使用融合分。权限同大模型管理。</p>
        <label class="row" style="gap:8px;margin-bottom:8px">
          <input v-model="rrEnabled" type="checkbox" />
          <span>启用团队 Rerank</span>
        </label>
        <div class="form-row" style="margin:0">
          <label>模型 ID</label>
          <input v-model="rrModel" class="o-input" placeholder="如 rerank-v3 / bge-reranker" />
        </div>
        <div class="form-row" style="margin:8px 0 0">
          <label>接口地址</label>
          <input v-model="rrBase" class="o-input" placeholder="https://api.example.com/v1" />
        </div>
        <div class="form-row" style="margin:8px 0 0">
          <label>API Key</label>
          <div class="row">
            <input
              v-model="rrKey"
              class="o-input"
              type="password"
              autocomplete="off"
              :placeholder="rerank?.hasKey ? `留空保持 ${rerank.keyHint}` : 'sk-...'"
            />
            <button class="o-btn" :disabled="rrBusy" @click="saveRerank">
              {{ rrBusy ? '保存中…' : '保存 Rerank' }}
            </button>
          </div>
        </div>
        <div v-if="rrMsg" class="msg" :class="{ err: rrFailed }">{{ rrMsg }}</div>
      </div>
    </div>

    <TeamModelDialog
      :open="dialogOpen"
      :team-id="teamId"
      :provider="provider"
      :provider-label="current?.label || ''"
      @close="dialogOpen = false"
      @changed="onModelsChanged"
    />
  </div>
</template>

<style scoped>
.llm {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.llm-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.llm-head h3 {
  font-size: 15px;
}
.llm-head p {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-3);
}
.llm-card {
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-row label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-2);
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row .o-select,
.row .o-input {
  flex: 1;
  min-width: 0;
}
.hint {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-4);
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-3);
  flex: none;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-4);
}
.dot.on {
  background: var(--success);
}
.link {
  border: none;
  background: transparent;
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  color: var(--primary);
}
.link.danger {
  color: var(--danger);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-2);
}
.chip.def {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-soft);
}
.chip em {
  font-style: normal;
  font-size: 10.5px;
  opacity: 0.8;
}
.msg {
  font-size: 12px;
  color: var(--success);
}
.msg.err {
  color: var(--danger);
}
</style>
