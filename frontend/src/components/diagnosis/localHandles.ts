/* 本机文件句柄注册表
   浏览器安全模型下, 网页只能写回用户主动拖入/授权的文件: 拖拽或资源管理器选择时
   通过 FileSystem Access API 拿到 FileSystemFileHandle 并登记, 编辑保存时用它把
   内容写回客户端电脑上的真实文件 (而不是会话工作区里的副本)。
   句柄同时写入 IndexedDB, 刷新页面后仍能恢复编辑能力(权限在点击编辑时重新申请)。
   同时记录文件指纹(lastModified/size): 轮询发现外部进程改动, 保存前比对防止覆盖他人写入。 */
import { ref } from 'vue'

export interface LocalWritable {
  write(data: string | Blob | BufferSource): Promise<void>
  close(): Promise<void>
}

export interface LocalFileHandle {
  name: string
  kind: 'file'
  getFile(): Promise<File>
  createWritable(): Promise<LocalWritable>
  /** 部分浏览器(如 Safari)没有权限查询/申请接口, 按“已授权”处理并在写入时再弹窗 */
  queryPermission?(desc: { mode: 'read' | 'readwrite' }): Promise<PermissionState>
  requestPermission?(desc: { mode: 'read' | 'readwrite' }): Promise<PermissionState>
}

export interface LocalDirHandle {
  name: string
  kind: 'directory'
  values(): AsyncIterableIterator<LocalFileHandle | LocalDirHandle>
}

export interface LocalStamp {
  lastModified: number
  size: number
}

export interface LocalChange {
  /** 工作区内的绝对路径(保留原始大小写) */
  path: string
  file: File
}

interface LocalEntry {
  handle: LocalFileHandle
  path: string
  stamp: LocalStamp | null
}

const handles = new Map<string, LocalEntry>()

/** 注册表版本: 供界面响应式判断「当前文件能否编辑」, 句柄登记/失效时自增 */
export const handleVersion = ref(0)

/* ---------------- 句柄持久化(IndexedDB) ----------------
   FileSystemFileHandle 可被结构化克隆, 存入 IndexedDB 后刷新页面仍能在启动时恢复;
   写权限本身不持久, 点击编辑时由 ensureWritable 重新申请授权。 */
const DB_NAME = 'log-insight-local-handles'
const DB_STORE = 'handles'

interface StoredEntry extends LocalEntry {
  key: string
}

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null)
  if (!dbPromise) {
    dbPromise = new Promise((resolve) => {
      const req = indexedDB.open(DB_NAME, 1)
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE, { keyPath: 'key' })
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => resolve(null)
    })
  }
  return dbPromise
}

function persistEntry(entry: StoredEntry) {
  void openDb()
    .then((db) => {
      if (!db) return
      db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE).put(entry)
    })
    .catch(() => { /* 存储失败不影响本次会话内编辑 */ })
}

function dropEntries(keys: string[]) {
  if (!keys.length) return
  void openDb()
    .then((db) => {
      if (!db) return
      const store = db.transaction(DB_STORE, 'readwrite').objectStore(DB_STORE)
      for (const key of keys) store.delete(key)
    })
    .catch(() => {})
}

/* 启动恢复: 完成前界面按只读显示, 恢复后 handleVersion 自增, 编辑入口随之可用 */
void (async () => {
  const db = await openDb()
  if (!db) return
  try {
    const rows = await new Promise<StoredEntry[]>((resolve, reject) => {
      const req = db.transaction(DB_STORE, 'readonly').objectStore(DB_STORE).getAll()
      req.onsuccess = () => resolve(req.result as StoredEntry[])
      req.onerror = () => reject(req.error)
    })
    let changed = false
    for (const row of rows) {
      if (!row?.key || !row.handle || handles.has(row.key)) continue
      handles.set(row.key, { handle: row.handle, path: row.path, stamp: row.stamp ?? null })
      changed = true
    }
    if (changed) handleVersion.value++
  } catch {
    /* 旧数据损坏 / 浏览器不支持句柄序列化: 忽略, 保持内存态 */
  }
})()

/* 与树/阅读器使用的绝对路径一致: Windows 路径大小写不敏感, POSIX 区分大小写.
   不能统一转小写: Linux 下 README.md / Readme.md 会撞成同一个键, 导致写错本机文件 */
const keyOf = (path: string) => {
  const trimmed = path.replace(/[\\/]+$/, '')
  return trimmed.includes('\\') ? trimmed.replace(/\//g, '\\').toLowerCase() : trimmed
}

/** 会话工作区根 + 拖入时的相对路径 → 树/阅读器的绝对路径 */
export function joinWorkspace(root: string, rel: string) {
  const sep = root.includes('\\') ? '\\' : '/'
  const base = root.replace(/[\\/]+$/, '')
  const tail = rel.replace(/^[\\/]+/, '').replace(/[\\/]/g, sep)
  return `${base}${sep}${tail}`
}

export function rememberLocalHandle(path: string, handle: LocalFileHandle, stamp?: LocalStamp) {
  const key = keyOf(path)
  const entry = { handle, path, stamp: stamp ?? null }
  handles.set(key, entry)
  handleVersion.value++
  persistEntry({ key, ...entry })
}

export function getLocalHandle(path: string): LocalFileHandle | undefined {
  return handles.get(keyOf(path))?.handle
}

export function hasLocalHandles() {
  return handles.size > 0
}

/** 已登记的句柄数量: 轮询按数量退避, 避免导入大量文件时每轮全量 stat */
export function localHandleCount() {
  return handles.size
}

/** 删除导入项时丢弃对应句柄(避免把已删除路径当成可写文件);
    会话切换不需要调用: 键是完整工作区路径, 不同会话互不影响 */
export function forgetLocalHandles(root: string) {
  const r = keyOf(root)
  if (!r) return
  let changed = false
  const dropped: string[] = []
  for (const key of [...handles.keys()]) {
    if (key === r || key.startsWith(r + '\\') || key.startsWith(r + '/')) {
      handles.delete(key)
      dropped.push(key)
      changed = true
    }
  }
  if (changed) handleVersion.value++
  dropEntries(dropped)
}

/** 申请写权限: 必须在用户点击的调用栈里执行, 浏览器才会弹出授权框;
    已授权的句柄直接返回, 避免每次点编辑都触发一次无谓的授权询问 */
export async function ensureWritable(handle: LocalFileHandle): Promise<PermissionState> {
  if (handle.queryPermission) {
    try {
      if (await handle.queryPermission({ mode: 'readwrite' }) === 'granted') return 'granted'
    } catch {
      /* 查询失败则继续走 requestPermission */
    }
  }
  if (handle.requestPermission) return handle.requestPermission({ mode: 'readwrite' })
  return 'granted'
}

/** 读取当前指纹: 与编辑开始时的快照比对, 可发现其他进程是否改过文件 */
export async function localStamp(path: string): Promise<LocalStamp | null> {
  const entry = handles.get(keyOf(path))
  if (!entry) return null
  try {
    const f = await entry.handle.getFile()
    return { lastModified: f.lastModified, size: f.size }
  } catch {
    return null
  }
}

/** 轮询本机文件: 返回被其他进程改动过的文件.
    指纹由调用方在同步成功后通过 refreshLocalStamp 推进, 上传失败时下一轮仍能重新发现该改动 */
export async function pollLocalChanges(): Promise<LocalChange[]> {
  const out: LocalChange[] = []
  for (const [key, entry] of [...handles]) {
    /* 刷新/重开页面后句柄尚未授权时 getFile() 会抛 NotAllowedError,
       用 queryPermission 预检, 避免每轮空转报错; 授权后自动恢复轮询 */
    if (entry.handle.queryPermission) {
      try {
        if (await entry.handle.queryPermission({ mode: 'read' }) !== 'granted') continue
      } catch {
        continue
      }
    }
    try {
      const file = await entry.handle.getFile()
      const stamp: LocalStamp = { lastModified: file.lastModified, size: file.size }
      /* 首次登记只有句柄没有指纹时, 以此为准, 不算变更 */
      if (!entry.stamp) {
        entry.stamp = stamp
        persistEntry({ key, handle: entry.handle, path: entry.path, stamp })
        continue
      }
      if (stamp.lastModified === entry.stamp.lastModified && stamp.size === entry.stamp.size) continue
      out.push({ path: entry.path, file })
    } catch (e) {
      /* 原文件被移走 / 已删除才注销; 权限暂时失效等错误保留句柄, 下次再试 */
      if ((e as DOMException)?.name === 'NotFoundError') {
        handles.delete(key)
        handleVersion.value++
        dropEntries([key])
      }
    }
  }
  return out
}

/** 本机改动同步到工作区成功后推进指纹, 避免重复上报同一改动;
    stamp 传入本次实际上传的文件指纹, 同步期间若文件又被改, 下一轮仍能发现 */
export async function refreshLocalStamp(path: string, stamp?: LocalStamp) {
  const key = keyOf(path)
  const entry = handles.get(key)
  if (!entry) return
  try {
    if (!stamp) {
      const f = await entry.handle.getFile()
      stamp = { lastModified: f.lastModified, size: f.size }
    }
    entry.stamp = stamp
    persistEntry({ key, handle: entry.handle, path: entry.path, stamp })
  } catch {
    /* 暂时读不到就保持旧指纹, 下轮重试 */
  }
}

/** 把文本写回客户端电脑上的真实文件
   createWritable() 先写浏览器侧的临时副本, close() 时才整体替换目标文件,
   因此不会出现「写了一半」的残缺文件; 写完刷新指纹, 避免轮询把自己的写入当成外部变更。 */
export async function writeLocalText(path: string, handle: LocalFileHandle, text: string) {
  const stream = await handle.createWritable()
  await stream.write(text)
  await stream.close()
  const key = keyOf(path)
  const entry = handles.get(key)
  if (!entry) return
  try {
    const f = await handle.getFile()
    entry.stamp = { lastModified: f.lastModified, size: f.size }
    persistEntry({ key, handle, path: entry.path, stamp: entry.stamp })
  } catch {
    /* 刷新指纹失败不影响已完成的写入 */
  }
}
