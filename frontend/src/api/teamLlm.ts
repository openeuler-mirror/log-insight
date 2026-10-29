/* 团队级大模型: 供应商连接 / 启用模型清单 / 用量 */
import { del, get, patch, post, put } from '@/api/http'

export interface TeamProvider {
  id: string
  label: string
  defaultBase: string
  protocols: string[]
  requiresBase: boolean
  note: string
  connected: boolean
  source: 'team' | 'platform' | ''
  keyHint: string
  apiBase: string
  protocol: string
  enabled: boolean
  modelCount: number
}

export interface TeamModel {
  id: number
  teamId: number
  provider: string
  modelId: string
  displayName: string
  enabled: boolean
  isDefault: boolean
  sortOrder: number
}

export interface DiscoverResult {
  ok: boolean
  message: string
  models: { id: string; name: string }[]
  selectedIds: string[]
}

export interface ModelDraft {
  modelId: string
  displayName: string
  enabled: boolean
  sortOrder: number
}

export interface UsageRow {
  userId?: number
  userName?: string
  provider?: string
  model?: string
  modelName?: string
  users?: number
  day?: string
  requests: number
  inputTokens: number
  outputTokens: number
  cachedReadTokens: number
  cachedWriteTokens: number
  thoughtTokens: number
  totalTokens: number
}

export interface UsageResult {
  groupBy: 'user' | 'model' | 'day'
  rows: UsageRow[]
}

const base = (teamId: number | string) => `/teams/${teamId}/llm`

export const listTeamProviders = (teamId: number | string) =>
  get<TeamProvider[]>(`${base(teamId)}/providers`)

export const saveTeamProvider = (
  teamId: number | string,
  provider: string,
  body: { apiKey?: string; apiBase?: string; protocol?: string; enabled?: boolean },
) => put<TeamProvider>(`${base(teamId)}/providers/${provider}`, {
  api_key: body.apiKey,
  api_base: body.apiBase,
  protocol: body.protocol,
  enabled: body.enabled,
})

export const deleteTeamProvider = (teamId: number | string, provider: string, force = false) =>
  del<TeamProvider>(`${base(teamId)}/providers/${provider}${force ? '?force=true' : ''}`)

export const discoverTeamModels = (
  teamId: number | string,
  provider: string,
  body: { apiKey?: string; apiBase?: string; protocol?: string } = {},
) => post<DiscoverResult>(`${base(teamId)}/providers/${provider}/discover`, {
  api_key: body.apiKey,
  api_base: body.apiBase,
  protocol: body.protocol,
})

export const listTeamModels = (teamId: number | string, provider: string) =>
  get<TeamModel[]>(`${base(teamId)}/providers/${provider}/models`)

export const saveTeamModels = (teamId: number | string, provider: string, models: ModelDraft[]) =>
  put<TeamModel[]>(`${base(teamId)}/providers/${provider}/models`, {
    models: models.map((m) => ({
      model_id: m.modelId,
      display_name: m.displayName,
      enabled: m.enabled,
      sort_order: m.sortOrder,
    })),
  })

export const patchTeamModel = (
  teamId: number | string,
  rowId: number,
  body: { displayName?: string; enabled?: boolean; sortOrder?: number },
) => patch<TeamModel>(`${base(teamId)}/models/${rowId}`, {
  display_name: body.displayName,
  enabled: body.enabled,
  sort_order: body.sortOrder,
})

export const setDefaultTeamModel = (teamId: number | string, rowId: number) =>
  put<TeamModel>(`${base(teamId)}/models/${rowId}/default`)

export const deleteTeamModel = (teamId: number | string, rowId: number) =>
  del(`${base(teamId)}/models/${rowId}`)

export const fetchTeamModelReasoning = (teamId: number | string, rowId: number) =>
  post<{ levels: { id: string; name: string }[]; default: string }>(
    `${base(teamId)}/models/${rowId}/reasoning`,
  )

export const fetchTeamUsage = (
  teamId: number | string,
  params: { start?: string; end?: string; groupBy?: 'user' | 'model' | 'day' },
) => get<UsageResult>(`${base(teamId)}/usage`, {
  start: params.start,
  end: params.end,
  group_by: params.groupBy,
})

export const fetchMyTeamUsage = (
  teamId: number | string,
  params: { start?: string; end?: string },
) => get<UsageResult>(`${base(teamId)}/usage/me`, params)

export interface TeamRerankConfig {
  enabled: boolean
  model: string
  apiBase: string
  hasKey: boolean
  keyHint: string
}

export const getTeamRerank = (teamId: number | string) =>
  get<TeamRerankConfig>(`${base(teamId)}/rerank`)

export const saveTeamRerank = (
  teamId: number | string,
  body: { enabled: boolean; model: string; apiBase?: string; apiKey?: string },
) => put<TeamRerankConfig>(`${base(teamId)}/rerank`, {
  enabled: body.enabled,
  model: body.model,
  api_base: body.apiBase ?? '',
  api_key: body.apiKey,
})
