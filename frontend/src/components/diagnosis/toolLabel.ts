/* 工具调用名称的中文展示: 屏蔽 pwsh / glob / read 等实现细节 */
const TOOL_LABELS: Record<string, string> = {
  pwsh: '终端命令',
  powershell: '终端命令',
  bash: '终端命令',
  shell: '终端命令',
  sh: '终端命令',
  glob: '查找文件',
  grep: '搜索内容',
  read: '读取文件',
  write: '写入文件',
  edit: '编辑文件',
  multi_edit: '批量编辑',
  ls: '浏览目录',
  list: '浏览目录',
  webfetch: '抓取网页',
  websearch: '搜索网页',
  task: '子任务',
  todowrite: '更新任务',
  todo: '任务清单',
  delete: '删除文件',
  move: '移动文件',
  copy: '复制文件',
}

export function toolLabel(name?: string | null): string {
  if (!name) return '工具调用'
  return TOOL_LABELS[name.toLowerCase()] ?? '工具调用'
}
