/* 智能诊断接口: 会话 / 文件 / DSH 流式对话 */
import { del, get, patch, post, put, TOKEN_KEY } from '@/api/http'

export interface AgentSkill {
  name: string
  description: string
}

export interface AgentStatus {
  enabled: boolean
  ready: boolean
  reason: string
  teamId: number
  teamName: string
  provider: string
  model: string
  modelName: string
  reasoning: string
  apiBase: string
  protocol: string
  hasKey: boolean
  userKey: boolean
  providerConnected: boolean
  keySource: 'user' | 'platform' | ''
  keyHint: string
  permission: string
  models: string[]
  skills: AgentSkill[]
}

export interface AgentProvider {
  id: string
  label: string
  defaultBase: string
  protocols: string[]
  requiresBase: boolean
  note: string
  connected: boolean
  source: 'user' | 'platform' | ''
  keyHint: string
  apiBase: string
  protocol: string
  /* 该供应商下最后一次的模型选择 */
  model: string
  modelName: string
  reasoning: string
}

export interface AgentModelOption {
  id: string
  name: string
}

export interface AgentReasoningOption {
  id: string
  name: string
}

export interface AgentReasoningResult {
  levels: AgentReasoningOption[]
  default: string
}

/** 模型选择 (连接凭据按供应商单独保存) */
export interface AgentConfigPayload {
  provider: string
  model: string
  model_name?: string
  reasoning?: string
}

export interface ProviderConnectionPayload {
  api_key?: string
  api_base: string
  protocol: string
}

export interface ModelQueryPayload {
  provider: string
  api_key?: string
  api_base: string
  protocol: string
}

export interface AgentConfigTestResult {
  ok: boolean
  message: string
  models?: AgentModelOption[]
}

export interface SessionInfo {
  id: string
  title: string
  model: string
  teamId: number | null
  provider: string
  modelName: string
  reasoning: string
  userId: number
  ownerName: string
  teamName: string
  canManage: boolean
  messageCount: number
  createdAt: string
  updatedAt: string
}

/** 团队可用模型 (按团队分组) */
export interface CatalogModel {
  rowId: number
  modelId: string
  name: string
  provider: string
  providerLabel: string
  isDefault: boolean
}

export interface CatalogTeam {
  id: number
  name: string
  selection: { modelRowId: number; reasoning: string } | null
  models: CatalogModel[]
}

export interface CatalogResult {
  teams: CatalogTeam[]
}

export type ToolBlock = {
  kind: 'tool'
  id: string
  name: string
  title: string
  toolKind?: string
  status: 'running' | 'done' | 'failed'
  input: string
  output: string
}

export type PlanBlock = {
  kind: 'plan'
  entries: { content: string; status: 'pending' | 'in_progress' | 'completed'; priority: string }[]
}

export type MessageBlock =
  | { kind: 'text'; text: string }
  | { kind: 'thought'; text: string }
  | { kind: 'file'; name: string; taskId?: string }
  | ToolBlock
  | PlanBlock

export interface ChatMessage {
  id: number | string
  role: 'user' | 'assistant'
  blocks: MessageBlock[]
  usage?: Record<string, unknown>
  error?: string
  createdAt?: string
}

export interface FileNode {
  id: string
  name: string
  meta?: string
  kind: 'dir' | 'file'
  path?: string
  size?: number
  children?: FileNode[]
}

export interface FileContent {
  kind: 'text' | 'image' | 'notice'
  content: string
  lang?: string
  size?: number
  truncated?: boolean
}

export interface FileRef {
  taskId?: string
  path?: string
  name: string
}

/* ---------- 基础 ---------- */
export const agentStatus = (teamId?: number) =>
  get<AgentStatus>('/diagnosis/status', teamId ? { team_id: teamId } : undefined)
export const fetchCatalog = () => get<CatalogResult>('/diagnosis/catalog')
export const saveTeamSelection = (body: { team_id: number; model_row_id: number; reasoning?: string }) =>
  put<AgentStatus>('/diagnosis/selection', body)
export const listProviders = () => get<AgentProvider[]>('/diagnosis/providers')
export const saveProviderConnection = (provider: string, body: ProviderConnectionPayload) =>
  put<AgentProvider>(`/diagnosis/providers/${provider}`, body)
export const deleteProviderConnection = (provider: string) =>
  del<AgentProvider>(`/diagnosis/providers/${provider}`)
export const saveAgentConfig = (body: AgentConfigPayload) =>
  put<AgentStatus>('/diagnosis/config', body)
export const fetchAgentModels = (body: ModelQueryPayload) =>
  post<AgentConfigTestResult>('/diagnosis/config/models', body)
export const fetchAgentReasoning = (body: { provider: string; model: string }) =>
  post<AgentReasoningResult>('/diagnosis/config/reasoning', body)
export const listSessions = (teamId?: number) =>
  get<SessionInfo[]>('/diagnosis/sessions', teamId ? { team_id: teamId } : undefined)
export const createSession = (title = '', teamId?: number) =>
  post<SessionInfo>('/diagnosis/sessions', { title, team_id: teamId })
export const renameSession = (id: string, title: string) =>
  patch<SessionInfo>(`/diagnosis/sessions/${id}`, { title })
export const deleteSession = (id: string) => del(`/diagnosis/sessions/${id}`)
export const listMessages = (id: string) => get<ChatMessage[]>(`/diagnosis/sessions/${id}/messages`)
export const listFiles = () => get<FileNode[]>('/diagnosis/files')
export const readFile = (fileId: string) =>
  get<FileContent>('/diagnosis/files/content', { file_id: fileId })

/* ---------- 流式发送 ---------- */
export interface StreamHandlers {
  onEvent: (event: string, data: any) => void
}

/** POST 流式读取 (SSE), 返回后本次对话结束 */
export async function sendMessage(
  sessionId: string,
  text: string,
  files: FileRef[],
  handlers: StreamHandlers,
  signal?: AbortSignal,
  selection?: { modelRowId?: number; reasoning?: string },
): Promise<void> {
  const token = localStorage.getItem(TOKEN_KEY) ?? ''
  const resp = await fetch(`/api/diagnosis/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      text,
      files: files.map((f) => (f.taskId
        ? { task_id: f.taskId, name: f.name }
        : { path: f.path ?? '', name: f.name })),
      model_row_id: selection?.modelRowId,
      reasoning: selection?.reasoning || '',
    }),
    signal,
  })

  if (!resp.ok || !resp.body) {
    let detail = `请求失败 (${resp.status})`
    try {
      const body = await resp.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatch = (raw: string) => {
    const lines = raw.split('\n')
    let event = 'message'
    const data: string[] = []
    for (const line of lines) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (!data.length) return
    const payload = data.join('\n')
    if (!payload) return
    try {
      handlers.onEvent(event, JSON.parse(payload))
    } catch {
      handlers.onEvent(event, payload)
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    let index = buffer.indexOf('\n\n')
    while (index >= 0) {
      dispatch(buffer.slice(0, index))
      buffer = buffer.slice(index + 2)
      index = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) dispatch(buffer)
}
