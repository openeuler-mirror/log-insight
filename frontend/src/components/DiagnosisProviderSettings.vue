<script setup lang="ts">
/* 供应商连接: 只管理每个供应商的 API Key / 地址 / 协议, 不涉及模型选择 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import {
  deleteProviderConnection,
  listProviders,
  saveProviderConnection,
  type AgentProvider,
} from '@/api/diagnosis'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  open: boolean
  focusProvider?: string
}>()
const emit = defineEmits<{ close: []; changed: [] }>()

const providers = ref<AgentProvider[]>([])
const provider = ref('')
const apiKey = ref('')
const apiBase = ref('')
const protocol = ref('messages')
const busy = ref(false)
const message = ref('')
const failed = ref(false)

const current = computed(() => providers.value.find((p) => p.id === provider.value))
const connected = computed(() => !!current.value?.connected)
const protocols = computed(() => current.value?.protocols ?? ['messages'])
const requiresBase = computed(() => !!current.value?.requiresBase)
const keyHint = computed(() => {
  const p = current.value
  if (p?.source === 'user') return `已保存 ${p.keyHint}；留空保持不变`
  if (p?.source === 'platform') return '当前使用平台默认 Key，填写后改用你的 Key'
  return '密钥按供应商加密保存，仅在服务器使用'
})
const basePlaceholder = computed(() =>
  provider.value === 'deepseek'
    ? '留空使用官方地址，也可填兼容网关'
    : current.value?.defaultBase || 'https://your-gateway/v1',
)

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    await loadProviders()
    const focus = props.focusProvider || ''
    provider.value = providers.value.some((p) => p.id === focus)
      ? focus
      : providers.value[0]?.id || ''
    applyFields()
    apiKey.value = ''
    resetMessage()
  },
  { immediate: true },
)

async function loadProviders() {
  try {
    providers.value = await listProviders()
  } catch {
    providers.value = []
  }
}

function applyFields() {
  apiBase.value = current.value?.apiBase || current.value?.defaultBase || ''
  protocol.value = current.value?.protocol || protocols.value[0]
}

function resetMessage() {
  message.value = ''
  failed.value = false
}

function onProviderChange() {
  apiKey.value = ''
  applyFields()
  resetMessage()
}

function replaceProvider(next: AgentProvider) {
  providers.value = providers.value.map((p) => (p.id === next.id ? { ...p, ...next } : p))
}

async function connect() {
  const p = current.value
  const key = apiKey.value.trim()
  if (!key && !p?.connected) {
    message.value = '请填写 API Key'
    failed.value = true
    return
  }
  busy.value = true
  resetMessage()
  try {
    const next = await saveProviderConnection(p?.id || provider.value, {
      api_key: key || undefined,
      api_base: apiBase.value.trim(),
      protocol: protocol.value,
    })
    replaceProvider(next)
    apiKey.value = ''
    message.value = next.source === 'user'
      ? `已连接 ${next.keyHint}`
      : '已连接（平台默认 Key）'
    emit('changed')
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  busy.value = true
  resetMessage()
  try {
    replaceProvider(await deleteProviderConnection(provider.value))
    apiKey.value = ''
    message.value = '已断开连接'
    emit('changed')
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    busy.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) emit('close')
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="mask" @click.self="emit('close')">
      <div class="dialog" role="dialog" aria-modal="true" aria-label="供应商连接">
        <div class="head">
          <div>
            <b>供应商连接</b>
            <p>每个供应商单独保存凭据；连接一次后，切换模型不需要重新填写。</p>
          </div>
          <button class="x" title="关闭" @click="emit('close')"><Icon name="close" :size="14" /></button>
        </div>

        <div class="body">
          <div class="field">
            <span class="label">供应商</span>
            <select v-model="provider" class="o-select" @change="onProviderChange">
              <option v-for="p in providers" :key="p.id" :value="p.id">
                {{ p.label }}{{ p.connected ? ' · 已连接' : '' }}
              </option>
            </select>
            <span class="hint">{{ current?.note || '模型请求走 DSH 的提供方路由' }}</span>
          </div>

          <div class="conn">
            <span class="dot" :class="{ 'is-on': connected }"></span>
            <span class="conn-text">
              {{ connected ? (current?.source === 'platform' ? '平台默认 Key 可用' : `已连接 ${current?.keyHint}`) : '未连接' }}
            </span>
            <button
              v-if="current?.source === 'user'"
              class="link danger"
              type="button"
              :disabled="busy"
              @click="disconnect"
            >
              断开
            </button>
          </div>

          <label class="field">
            <span class="label">API Key</span>
            <div class="row">
              <input
                v-model="apiKey"
                class="o-input"
                type="password"
                autocomplete="off"
                spellcheck="false"
                :placeholder="connected ? '留空保持不变' : 'sk-...'"
              />
              <button class="o-btn o-btn--sm" type="button" :disabled="busy" @click="connect">
                {{ busy ? '连接中…' : connected ? '保存连接' : '连接' }}
              </button>
            </div>
            <span class="hint">{{ keyHint }}</span>
          </label>

          <label class="field">
            <span class="label">接口地址</span>
            <input v-model="apiBase" class="o-input" :placeholder="basePlaceholder" />
            <span class="hint">{{ requiresBase ? '自定义网关必填，需兼容所选协议。' : '留空使用供应商默认地址，也可填代理地址。' }}</span>
          </label>

          <label v-if="protocols.length > 1" class="field">
            <span class="label">接口协议</span>
            <select v-model="protocol" class="o-select">
              <option value="messages">Messages（Anthropic / DeepSeek）</option>
              <option value="chat-completions">Chat Completions（OpenAI 兼容）</option>
            </select>
            <span class="hint">保存连接时生效。</span>
          </label>
        </div>

        <div class="foot">
          <span class="msg" :class="{ 'is-err': failed }">{{ message }}</span>
          <button class="o-btn" @click="emit('close')">关闭</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 1001;
  background: rgba(18, 28, 45, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.dialog {
  width: 520px;
  max-width: 100%;
  max-height: calc(100vh - 40px);
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px 12px;
  border-bottom: 1px solid var(--border);
}
.head b {
  font-size: 15px;
}
.head p {
  margin-top: 4px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-3);
}
.x {
  flex: none;
  width: 26px;
  height: 26px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  cursor: pointer;
}
.x:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-2);
}
.hint {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-4);
}
.conn {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-3);
  margin-top: -4px;
}
.conn .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-4);
}
.conn .dot.is-on {
  background: var(--success);
}
.conn-text {
  flex: 1;
  min-width: 0;
}
.row {
  display: flex;
  gap: 8px;
}
.row .o-input {
  flex: 1;
  min-width: 0;
}
.link {
  flex: none;
  border: none;
  background: transparent;
  color: var(--primary);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  padding: 0;
}
.link.danger {
  color: var(--danger);
}
.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px 16px;
  border-top: 1px solid var(--border);
}
.msg {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--success);
  line-height: 1.5;
}
.msg.is-err {
  color: var(--danger);
}
</style>
