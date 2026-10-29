import { del, get, patch, post } from './http'
import http from './http'

export interface KnowledgeBase {
  id: number
  teamId: number
  name: string
  description: string
  caseTemplate: string
  vectorEnabled: boolean
  embeddingConfig: {
    model?: string
    apiBase?: string
    provider?: string
    hasKey?: boolean
    keyHint?: string
  }
  status: string
  vectorHint?: string | null
  createdAt?: string
  updatedAt?: string
}

export interface KbDocument {
  id: number
  kbId: number
  title: string
  docType: string
  ingestMode: string
  sourceFormat: string
  status: string
  enabled: boolean
  metadata?: Record<string, unknown>
  createdAt?: string
}

export function listKbs(teamId: number) {
  return get<KnowledgeBase[]>(`/teams/${teamId}/knowledge-bases`)
}

export function createKb(teamId: number, body: {
  name: string
  description?: string
  vectorEnabled?: boolean
  embeddingConfig?: Record<string, unknown>
  caseTemplate?: string
}) {
  return post<KnowledgeBase>(`/teams/${teamId}/knowledge-bases`, {
    name: body.name,
    description: body.description ?? '',
    vector_enabled: body.vectorEnabled ?? false,
    embedding_config: body.embeddingConfig ?? {},
    case_template: body.caseTemplate ?? '',
  })
}

export function updateKb(teamId: number, kbId: number, body: {
  name?: string
  description?: string
  caseTemplate?: string
  status?: string
  embeddingApiKey?: string
  embeddingApiBase?: string
}) {
  const payload: Record<string, unknown> = {}
  if (body.name !== undefined) payload.name = body.name
  if (body.description !== undefined) payload.description = body.description
  if (body.caseTemplate !== undefined) payload.case_template = body.caseTemplate
  if (body.status !== undefined) payload.status = body.status
  if (body.embeddingApiKey !== undefined) payload.embedding_api_key = body.embeddingApiKey
  if (body.embeddingApiBase !== undefined) payload.embedding_api_base = body.embeddingApiBase
  return patch<KnowledgeBase>(`/teams/${teamId}/knowledge-bases/${kbId}`, payload)
}

export function deleteKb(teamId: number, kbId: number) {
  return del(`/teams/${teamId}/knowledge-bases/${kbId}`)
}

export function listDocs(teamId: number, kbId: number) {
  return get<KbDocument[]>(`/teams/${teamId}/knowledge-bases/${kbId}/documents`)
}

export function getDoc(teamId: number, kbId: number, docId: number) {
  return get<KbDocument & { content: string }>(
    `/teams/${teamId}/knowledge-bases/${kbId}/documents/${docId}`,
  )
}

export function getKb(teamId: number, kbId: number) {
  return get<KnowledgeBase>(`/teams/${teamId}/knowledge-bases/${kbId}`)
}

export function deleteDoc(teamId: number, kbId: number, docId: number) {
  return del(`/teams/${teamId}/knowledge-bases/${kbId}/documents/${docId}`)
}

function mapDoc(d: Record<string, unknown>): KbDocument {
  return {
    id: Number(d.id),
    kbId: Number(d.kb_id ?? d.kbId),
    title: String(d.title ?? ''),
    docType: String(d.doc_type ?? d.docType ?? ''),
    ingestMode: String(d.ingest_mode ?? d.ingestMode ?? ''),
    sourceFormat: String(d.source_format ?? d.sourceFormat ?? ''),
    status: String(d.status ?? ''),
    enabled: Boolean(d.enabled),
    metadata: (d.metadata as Record<string, unknown>) || {},
    createdAt: (d.created_at ?? d.createdAt) as string | undefined,
  }
}

export async function uploadFile(
  teamId: number,
  kbId: number,
  file: File,
  mode: 'raw' | 'template' = 'raw',
): Promise<KbDocument> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('mode', mode)
  const res = await http.post(`/teams/${teamId}/knowledge-bases/${kbId}/upload`, fd, {
    timeout: 120000,
  })
  return mapDoc(res.data as Record<string, unknown>)
}

/** @deprecated 使用 uploadFile */
export function uploadRaw(teamId: number, kbId: number, file: File) {
  return uploadFile(teamId, kbId, file, 'raw')
}

export interface KbIngestItem {
  id: number
  batchId: number
  filename: string
  parseStatus: string
  convertStatus: string
  previewMarkdown: string
  errorMessage: string
  selected: boolean
  documentId: number | null
}

export interface KbIngestBatch {
  id: number
  teamId: number
  kbId: number
  mode: string
  status: string
  items: KbIngestItem[]
}

export async function uploadTemplateBatch(
  teamId: number,
  kbId: number,
  files: File[],
): Promise<KbIngestBatch> {
  const fd = new FormData()
  fd.append('mode', 'template')
  for (const f of files) fd.append('files', f)
  // 多文件串行 LLM 转换：按文件数放大超时（单文件约 3 分钟上限，整批最多 20 分钟）
  const perFileMs = 180_000
  const timeout = Math.min(1_200_000, Math.max(300_000, files.length * perFileMs))
  const res = await http.post(
    `/teams/${teamId}/knowledge-bases/${kbId}/upload-batch`,
    fd,
    { timeout },
  )
  const d = res.data as Record<string, unknown>
  const items = ((d.items as Record<string, unknown>[]) || []).map((it) => ({
    id: Number(it.id),
    batchId: Number(it.batch_id ?? it.batchId),
    filename: String(it.filename ?? ''),
    parseStatus: String(it.parse_status ?? it.parseStatus ?? ''),
    convertStatus: String(it.convert_status ?? it.convertStatus ?? ''),
    previewMarkdown: String(it.preview_markdown ?? it.previewMarkdown ?? ''),
    errorMessage: String(it.error_message ?? it.errorMessage ?? ''),
    selected: Boolean(it.selected),
    documentId: (it.document_id ?? it.documentId ?? null) as number | null,
  }))
  return {
    id: Number(d.id),
    teamId: Number(d.team_id ?? d.teamId),
    kbId: Number(d.kb_id ?? d.kbId),
    mode: String(d.mode ?? ''),
    status: String(d.status ?? ''),
    items,
  }
}

export function commitTemplateBatch(teamId: number, batchId: number, itemIds: number[]) {
  return post<{ ok: boolean; count: number; documents: KbDocument[] }>(
    `/teams/${teamId}/kb/batches/${batchId}/commit`,
    { item_ids: itemIds },
  )
}

export function searchKb(teamId: number, body: {
  kbIds: number[]
  queries: string[]
  topK?: number
  mode?: string
}) {
  return post(`/teams/${teamId}/kb/search`, {
    kb_ids: body.kbIds,
    queries: body.queries,
    top_k: body.topK,
    mode: body.mode ?? 'auto',
  })
}
