import { del, get, patch, post } from './http'
import http from './http'

export interface KnowledgeBase {
  id: number
  teamId: number
  name: string
  description: string
  caseTemplate: string
  vectorEnabled: boolean
  embedModelId?: number | null
  embedDisplayName?: string
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
  /** 目录导入时的原始相对路径（POSIX 分隔），空=根目录 */
  relPath: string
  status: string
  enabled: boolean
  metadata?: Record<string, unknown>
  createdAt?: string
  updatedAt?: string
}

/** 知识库显式目录（用于持久化空目录） */
export interface KbFolder {
  id: number
  kbId: number
  /** POSIX 相对路径（相对知识库根） */
  path: string
  createdAt?: string
  updatedAt?: string
}

export function listKbs(teamId: number) {
  return get<KnowledgeBase[]>(`/teams/${teamId}/knowledge-bases`)
}

export function createKb(teamId: number, body: {
  name: string
  description?: string
  embedModelId?: number | null
  caseTemplate?: string
}) {
  return post<KnowledgeBase>(`/teams/${teamId}/knowledge-bases`, {
    name: body.name,
    description: body.description ?? '',
    embed_model_id: body.embedModelId ?? null,
    case_template: body.caseTemplate ?? '',
  })
}

export function updateKb(teamId: number, kbId: number, body: {
  name?: string
  description?: string
  caseTemplate?: string
  status?: string
}) {
  const payload: Record<string, unknown> = {}
  if (body.name !== undefined) payload.name = body.name
  if (body.description !== undefined) payload.description = body.description
  if (body.caseTemplate !== undefined) payload.case_template = body.caseTemplate
  if (body.status !== undefined) payload.status = body.status
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

export function listFolders(teamId: number, kbId: number) {
  return get<KbFolder[]>(`/teams/${teamId}/knowledge-bases/${kbId}/folders`)
}

export function createFolder(teamId: number, kbId: number, path: string) {
  return post<KbFolder>(`/teams/${teamId}/knowledge-bases/${kbId}/folders`, { path })
}

export function renameFolder(teamId: number, kbId: number, path: string, name: string) {
  return patch<{ ok: boolean; path: string }>(
    `/teams/${teamId}/knowledge-bases/${kbId}/folders`,
    { path, name },
  )
}

export function deleteFolder(teamId: number, kbId: number, path: string) {
  return del(`/teams/${teamId}/knowledge-bases/${kbId}/folders?path=${encodeURIComponent(path)}`)
}

function mapDoc(d: Record<string, unknown>): KbDocument {
  return {
    id: Number(d.id),
    kbId: Number(d.kb_id ?? d.kbId),
    title: String(d.title ?? ''),
    docType: String(d.doc_type ?? d.docType ?? ''),
    ingestMode: String(d.ingest_mode ?? d.ingestMode ?? ''),
    sourceFormat: String(d.source_format ?? d.sourceFormat ?? ''),
    relPath: String(d.rel_path ?? d.relPath ?? ''),
    status: String(d.status ?? ''),
    enabled: Boolean(d.enabled),
    metadata: (d.metadata as Record<string, unknown>) || {},
    createdAt: (d.created_at ?? d.createdAt) as string | undefined,
    updatedAt: (d.updated_at ?? d.updatedAt) as string | undefined,
  }
}

export async function uploadFile(
  teamId: number,
  kbId: number,
  file: File,
  mode: 'raw' | 'template' = 'raw',
  relPath = '',
): Promise<KbDocument> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('mode', mode)
  fd.append('rel_path', relPath)
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
  /** 目录导入时的原始相对路径（POSIX 分隔） */
  relPath: string
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
  relPaths: string[] = [],
): Promise<KbIngestBatch> {
  const fd = new FormData()
  fd.append('mode', 'template')
  for (const f of files) fd.append('files', f)
  fd.append('rel_paths', JSON.stringify(relPaths))
  // 多文件串行 LLM 转换：按文件数放大超时（单文件约 3 分钟上限，整批最多 20 分钟）
  const perFileMs = 180_000
  const timeout = Math.min(1_200_000, Math.max(300_000, files.length * perFileMs))
  const res = await http.post(
    `/teams/${teamId}/knowledge-bases/${kbId}/upload-batch`,
    fd,
    { timeout },
  )
  return mapBatch(res.data as Record<string, unknown>)
}

function mapBatch(d: Record<string, unknown>): KbIngestBatch {
  const items = ((d.items as Record<string, unknown>[]) || []).map((it) => ({
    id: Number(it.id),
    batchId: Number(it.batch_id ?? it.batchId),
    filename: String(it.filename ?? ''),
    relPath: String(it.rel_path ?? it.relPath ?? ''),
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
