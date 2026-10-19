/* 本地文件系统浏览 API (后端 /api/fs): 目录树 / 列表 / 文件内容 / 原始二进制 / 变更监听 */
import { get, TOKEN_KEY } from '@/api/http'

export interface FsEntry {
  name: string
  path: string
  kind: 'dir' | 'file'
  size: number
  mtime: number
  /** 已加载的子项: 数组(可能为空)=已加载, undefined=未加载(展开时懒加载) */
  children?: FsEntry[]
}

export interface FsRoot {
  name: string
  path: string
}

export type FsOp = 'created' | 'modified' | 'deleted' | 'moved'

export interface FsChange {
  op: FsOp
  path: string
  kind: 'dir' | 'file'
  /** 受影响文件的父目录 */
  dir: string
  /** moved 时的原路径 */
  src?: string
  srcDir?: string
}

export interface FsReadResult {
  kind: 'text' | 'image'
  path: string
  size: number
  mtime: number
  lang?: string
  content?: string
}

/** 可浏览的根目录(用户主目录 + 系统盘符) */
export const fetchRoots = () => get<{ roots: FsRoot[]; home: string }>('/fs/roots')

/** 一次性嵌套加载目录树(超出阈值/预算的目录需再调 fetchList 懒加载) */
export const fetchTree = (path: string, depth?: number) =>
  get<{ root: FsEntry; truncated: boolean }>('/fs/tree', depth ? { path, depth } : { path })

/** 单层目录列表(懒加载) */
export const fetchList = (path: string) =>
  get<{ path: string; entries: FsEntry[]; truncated: boolean }>('/fs/list', { path })

/** 读取文件内容(图片只返回元信息, 前端走 rawUrl 取二进制) */
export const fetchFile = (path: string) => get<FsReadResult>('/fs/read', { path })

/** 带 token 的 URL: EventSource / <img> 无法携带 Header, 后端支持 ?token= 认证 */
function tokenUrl(base: string, path: string): string {
  const token = localStorage.getItem(TOKEN_KEY) ?? ''
  return `${base}?path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`
}

/** 原始二进制(图片预览)地址 */
export const rawUrl = (path: string) => tokenUrl('/api/fs/raw', path)

/** 目录变更监听 SSE 地址 */
export const watchUrl = (path: string) => tokenUrl('/api/fs/watch', path)
