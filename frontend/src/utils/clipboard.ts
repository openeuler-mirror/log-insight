/* 剪贴板工具: 优先 Clipboard API, 非 HTTPS / 受限环境降级 execCommand */
export async function copyText(content: string): Promise<boolean> {
  if (!content) return false
  try {
    await navigator.clipboard.writeText(content)
    return true
  } catch {
    /* 降级: 临时 textarea + execCommand */
    const ta = document.createElement('textarea')
    ta.value = content
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    document.body.removeChild(ta)
    return ok
  }
}
