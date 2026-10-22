/* 智能诊断共享类型 */

export interface Attach {
  id: string
  name: string
  path: string
  size?: string
}

export type FileKind = 'text' | 'image' | 'notice'

/* 阅读器标签页 */
export interface ReaderTab extends Attach {
  kind: FileKind
  content: string
  original: string
  lang?: string
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}
