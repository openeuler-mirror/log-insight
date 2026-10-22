<script setup lang="ts">
/* 模型选择: 只负责从已连接的供应商中选模型 / 显示名称 / 推理强度 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiError } from '@/api/http'
import {
  fetchAgentModels,
  fetchAgentReasoning,
  listProviders,
  saveAgentConfig,
  type AgentModelOption,
  type AgentProvider,
  type AgentReasoningOption,
  type AgentStatus,
} from '@/api/diagnosis'
import Icon from '@/components/Icon.vue'

const props = defineProps<{
  open: boolean
  status: AgentStatus | null
}>()
const emit = defineEmits<{ close: []; saved: [status: AgentStatus]; manage: [] }>()

const providers = ref<AgentProvider[]>([])
const provider = ref('')
const model = ref('')
const modelName = ref('')
const reasoning = ref('')
const reasoningLevels = ref<AgentReasoningOption[]>([])
const reasoningLoading = ref(false)
const models = ref<AgentModelOption[]>([])
const loadingModels = ref(false)
const manualModel = ref(false)
const saving = ref(false)
const message = ref('')
const failed = ref(false)
const modelHint = ref('')
let reasoningSeq = 0
let modelsSeq = 0

const connectedProviders = computed(() => providers.value.filter((p) => p.connected))
const current = computed(() => connectedProviders.value.find((p) => p.id === provider.value))

const REASONING_LABELS: Record<string, string> = {
  off: '关闭',
  minimal: '极低',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '很高',
  max: '最高',
}
const reasoningLabel = (item: AgentReasoningOption) =>
  REASONING_LABELS[item.id] || item.name || item.id

/* 每个供应商的选择: 弹窗内的未保存草稿 + 后端记住的上次保存值 */
interface Draft {
  model: string
  name: string
  reasoning: string
}
const drafts = ref<Record<string, Draft>>({})
let lastProvider = ''

function snapshot(): Draft {
  return { model: model.value, name: modelName.value, reasoning: reasoning.value }
}

function restore(id: string, useStatus = false) {
  const saved = connectedProviders.value.find((p) => p.id === id)
  const base =
    useStatus && props.status?.provider === id
      ? {
          model: props.status.model || '',
          name: props.status.modelName || '',
          reasoning: props.status.reasoning || '',
        }
      : { model: saved?.model || '', name: saved?.modelName || '', reasoning: saved?.reasoning || '' }
  /* 当前生效的供应商以已保存状态为准; 其他供应商优先用本次未保存的草稿 */
  const draft = useStatus && props.status?.provider === id ? base : drafts.value[id] || base
  model.value = draft.model
  modelName.value = draft.name
  reasoning.value = draft.reasoning
  manualModel.value = false
  modelHint.value = ''
  models.value = model.value ? [{ id: model.value, name: modelName.value || model.value }] : []
  reasoningLevels.value = []
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    await ensureProviders()
    const list = connectedProviders.value
    const active = props.status?.provider || ''
    provider.value = list.some((p) => p.id === active) ? active : list[0]?.id || ''
    lastProvider = provider.value
    restore(provider.value, true)
    resetMessage()
    if (provider.value) {
      void loadModels()
      if (model.value) void loadReasoning()
    }
  },
  { immediate: true },
)

async function ensureProviders() {
  try {
    providers.value = await listProviders()
  } catch {
    providers.value = []
  }
}

function resetMessage() {
  message.value = ''
  failed.value = false
}

function onProviderChange() {
  /* 切换前把当前供应商的编辑存进草稿, 切回来时原样恢复 */
  if (lastProvider && lastProvider !== provider.value) {
    drafts.value[lastProvider] = snapshot()
  }
  lastProvider = provider.value
  resetMessage()
  restore(provider.value)
  if (provider.value) {
    void loadModels()
    if (model.value) void loadReasoning()
  }
}

async function loadModels() {
  if (!current.value) return
  const seq = ++modelsSeq
  const target = provider.value
  loadingModels.value = true
  modelHint.value = ''
  try {
    const res = await fetchAgentModels({
      provider: target,
      api_base: current.value.apiBase || '',
      protocol: current.value.protocol,
    })
    if (seq !== modelsSeq) return
    if (res.models?.length) {
      const seen = new Set<string>()
      const merged: AgentModelOption[] = []
      for (const item of res.models) {
        if (!seen.has(item.id)) {
          seen.add(item.id)
          merged.push(item)
        }
      }
      if (model.value && !seen.has(model.value)) {
        merged.unshift({ id: model.value, name: modelName.value || model.value })
      }
      models.value = merged
      if (!model.value) selectModel(merged[0].id)
    }
    modelHint.value = res.message
    if (!res.ok && !models.value.length) {
      message.value = res.message
      failed.value = true
    }
  } catch (e) {
    if (seq === modelsSeq) modelHint.value = apiError(e)
  } finally {
    if (seq === modelsSeq) loadingModels.value = false
  }
}

function selectModel(id: string) {
  model.value = id
  const hit = models.value.find((m) => m.id === id)
  if (hit) modelName.value = hit.name
  void loadReasoning()
}

function onManualModel() {
  const id = model.value.trim()
  const hit = models.value.find((m) => m.id === id)
  modelName.value = hit ? hit.name : id
  void loadReasoning()
}

async function loadReasoning() {
  const seq = ++reasoningSeq
  reasoningLevels.value = []
  if (!model.value.trim()) return
  reasoningLoading.value = true
  try {
    const res = await fetchAgentReasoning({
      provider: provider.value,
      model: model.value.trim(),
    })
    if (seq !== reasoningSeq) return
    reasoningLevels.value = res.levels ?? []
    if (reasoning.value && !reasoningLevels.value.some((l) => l.id === reasoning.value)) {
      reasoning.value = res.default || ''
    }
  } catch {
    if (seq === reasoningSeq) reasoningLevels.value = []
  } finally {
    if (seq === reasoningSeq) reasoningLoading.value = false
  }
}

async function save() {
  resetMessage()
  if (!provider.value) {
    message.value = '请先连接供应商'
    failed.value = true
    return
  }
  if (!model.value.trim()) {
    message.value = manualModel.value ? '请填写模型 ID' : '请选择模型'
    failed.value = true
    return
  }
  saving.value = true
  try {
    const next = await saveAgentConfig({
      provider: provider.value,
      model: model.value.trim(),
      model_name: modelName.value.trim(),
      reasoning: reasoning.value,
    })
    const savedDraft = {
      model: next.model || model.value.trim(),
      name: next.modelName || modelName.value.trim(),
      reasoning: next.reasoning || '',
    }
    drafts.value[provider.value] = savedDraft
    providers.value = providers.value.map((p) =>
      p.id === provider.value ? { ...p, ...savedDraft } : p,
    )
    emit('saved', next)
  } catch (e) {
    message.value = apiError(e)
    failed.value = true
  } finally {
    saving.value = false
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
      <div class="dialog" role="dialog" aria-modal="true" aria-label="选择模型">
        <div class="head">
          <div>
            <b>选择模型</b>
            <p>从已连接的供应商中选择模型；供应商连接在「供应商连接」中管理。</p>
          </div>
          <button class="x" title="关闭" @click="emit('close')"><Icon name="close" :size="14" /></button>
        </div>

        <div class="body">
          <div v-if="!connectedProviders.length" class="empty">
            <p>还没有连接供应商。</p>
            <button class="o-btn o-btn--primary" @click="emit('manage')">配置供应商连接</button>
          </div>

          <template v-else>
            <div class="field">
              <span class="label">供应商</span>
              <div class="row">
                <select v-model="provider" class="o-select" @change="onProviderChange">
                  <option v-for="p in connectedProviders" :key="p.id" :value="p.id">
                    {{ p.label }}
                  </option>
                </select>
                <button class="link" type="button" @click="emit('manage')">供应商连接</button>
              </div>
              <span class="hint">{{ current?.note || '已连接的供应商可以随时切换，不需要重新配置密钥' }}</span>
            </div>

            <div class="field">
              <span class="label">模型</span>
              <div class="row">
                <select
                  v-if="!manualModel"
                  class="o-select"
                  :value="model"
                  :disabled="loadingModels"
                  @change="selectModel(($event.target as HTMLSelectElement).value)"
                >
                  <option v-if="!models.length" value="" disabled>
                    {{ loadingModels ? '正在读取模型…' : '点击刷新读取模型' }}
                  </option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
                    {{ m.name && m.name !== m.id ? `${m.name} · ${m.id}` : m.id }}
                  </option>
                </select>
                <input
                  v-else
                  v-model="model"
                  class="o-input"
                  placeholder="模型 ID"
                  @change="onManualModel"
                />
                <button
                  class="o-btn o-btn--sm"
                  type="button"
                  :disabled="loadingModels"
                  title="从供应商 /models 接口读取"
                  @click="loadModels"
                >
                  {{ loadingModels ? '读取中…' : '刷新' }}
                </button>
              </div>
              <span class="model-foot">
                <span class="hint">{{ modelHint || '列表来自供应商 /models 接口，读取不到时可手动输入' }}</span>
                <button class="link" type="button" @click="manualModel = !manualModel">
                  {{ manualModel ? '从列表选择' : '手动输入' }}
                </button>
              </span>
            </div>

            <label class="field">
              <span class="label">显示名称</span>
              <input v-model="modelName" class="o-input" :placeholder="model || '留空按模型 ID 生成'" />
              <span class="hint">诊断页顶部显示的模型名；选模型时自动填入，可随意修改。</span>
            </label>

            <div v-if="reasoningLevels.length" class="field">
              <span class="label">推理强度</span>
              <div class="seg">
                <button type="button" :class="{ on: reasoning === '' }" @click="reasoning = ''">
                  默认
                </button>
                <button
                  v-for="item in reasoningLevels.filter((l) => l.id)"
                  :key="item.id"
                  type="button"
                  :class="{ on: reasoning === item.id }"
                  @click="reasoning = item.id"
                >
                  {{ reasoningLabel(item) }}
                </button>
              </div>
              <span class="hint">来自 DSH 的模型能力；只对支持推理的模型显示。</span>
            </div>
            <div v-else-if="reasoningLoading" class="field">
              <span class="label">推理强度</span>
              <span class="hint">正在读取模型的推理能力…</span>
            </div>
          </template>
        </div>

        <div class="foot">
          <span class="msg" :class="{ 'is-err': failed }">{{ message }}</span>
          <button class="o-btn o-btn--primary" :disabled="saving || !provider" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
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
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 28px 0;
  color: var(--text-3);
  font-size: 13px;
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
.model-foot {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.model-foot .hint {
  flex: 1;
  min-width: 0;
}
.seg {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.seg button {
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-2);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.seg button:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.seg button.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
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
