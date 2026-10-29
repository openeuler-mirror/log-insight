"""按案例模板将解析正文转换为结构化 Markdown 预览."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 多文件串行转换时单次 LLM 容易偏慢，给足读超时并允许一次重试
_CONVERT_TIMEOUT = 180.0
_CONVERT_RETRIES = 1


def load_default_template() -> str:
    path = Path(__file__).with_name("default_case_template.md")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "# {title}\n\n## 现象\n\n## 根因\n\n## 解决方案\n"


async def convert_case(
    *,
    content: str,
    title: str,
    template: str,
    chat: dict | None,
) -> str:
    tpl = (template or "").strip() or load_default_template()
    tpl = tpl.replace("{title}", title or "未命名案例")
    if not chat or not chat.get("api_key") or not chat.get("model"):
        raise RuntimeError("团队未配置默认 Chat 模型，无法进行模板转换")

    from app.modules.kb._chat import chat_completion
    # 多文件批次时适当截断，降低单次生成耗时
    body = content[:8000]
    prompt = (
        "你是运维案例整理助手。请把下方「原始材料」改写成符合「案例模板」的 Markdown。\n"
        "要求：只输出改写后的 Markdown；保留关键错误码、日志关键字；不要编造未给出的事实。\n\n"
        f"【案例模板】\n{tpl[:4000]}\n\n"
        f"【原始材料】\n标题：{title}\n{body}\n"
    )
    last_err: Exception | None = None
    for attempt in range(_CONVERT_RETRIES + 1):
        try:
            return await chat_completion(
                api_base=str(chat.get("api_base") or ""),
                api_key=str(chat["api_key"]),
                model=str(chat["model"]),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2500,
                timeout=_CONVERT_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            msg = str(exc).lower()
            retryable = "超时" in str(exc) or "timed out" in msg or "timeout" in msg
            if not retryable or attempt >= _CONVERT_RETRIES:
                break
            logger.warning("case_convert timeout, retry %s: %s", attempt + 1, title)
            await asyncio.sleep(1.5)
    assert last_err is not None
    raise last_err
