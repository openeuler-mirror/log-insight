"""模型供应商注册表: 用户配置 -> DSH 路由 / 模型发现.

DSH 有两条模型通道:
- deepseek-official: 原生 DeepSeek 适配器, 接受 baseURL + messages/chat-completions 协议;
- pi-ai 路由: openai / anthropic / openrouter 等, 由 settings.yaml 的
  llm-pi-ai.providers 声明, 由 ACP 入口按 DSH_PROVIDER 选择.

模型 id 与显示名称都以供应商 /models 接口返回为准, 这里只保留默认值.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    route: str                 # DSH 提供方路由名
    native: str                # deepseek | pi-ai
    default_base: str          # 推理接口地址默认值 (空 = 适配器官方默认)
    auth: str                  # bearer | anthropic (模型发现用)
    protocols: tuple[str, ...]  # messages / chat-completions
    discovery_base: str = ""   # /models 发现接口根地址
    requires_base: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "defaultBase": self.default_base,
            "protocols": list(self.protocols),
            "requiresBase": self.requires_base,
            "note": self.note,
        }


PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        id="deepseek", label="DeepSeek 官方", route="deepseek-official", native="deepseek",
        default_base="", auth="bearer", protocols=("messages", "chat-completions"),
        discovery_base="https://api.deepseek.com",
        note="官方 API，也支持协议兼容的自建网关",
    ),
    "openai": ProviderSpec(
        id="openai", label="OpenAI", route="openai", native="pi-ai",
        default_base="https://api.openai.com/v1", auth="bearer",
        protocols=("chat-completions",), discovery_base="https://api.openai.com/v1",
        note="OpenAI 官方接口；兼容网关请选“自定义网关”",
    ),
    "anthropic": ProviderSpec(
        id="anthropic", label="Anthropic", route="anthropic", native="pi-ai",
        default_base="https://api.anthropic.com", auth="anthropic",
        protocols=("messages",), discovery_base="https://api.anthropic.com",
        note="Anthropic 官方 Messages 接口",
    ),
    "openrouter": ProviderSpec(
        id="openrouter", label="OpenRouter", route="openrouter", native="pi-ai",
        default_base="https://openrouter.ai/api/v1", auth="bearer",
        protocols=("chat-completions",), discovery_base="https://openrouter.ai/api/v1",
        note="聚合网关，一个 Key 可用多家模型",
    ),
    "google": ProviderSpec(
        id="google", label="Google Gemini", route="google", native="pi-ai",
        default_base="https://generativelanguage.googleapis.com/v1beta", auth="google",
        protocols=("chat-completions",),
        discovery_base="https://generativelanguage.googleapis.com/v1beta",
        note="Google AI Studio 的 Gemini 接口",
    ),
    "xai": ProviderSpec(
        id="xai", label="xAI Grok", route="xai", native="pi-ai",
        default_base="https://api.x.ai/v1", auth="bearer",
        protocols=("chat-completions",), discovery_base="https://api.x.ai/v1",
        note="xAI 官方接口",
    ),
    "groq": ProviderSpec(
        id="groq", label="Groq", route="groq", native="pi-ai",
        default_base="https://api.groq.com/openai/v1", auth="bearer",
        protocols=("chat-completions",), discovery_base="https://api.groq.com/openai/v1",
        note="高速推理托管，OpenAI 兼容",
    ),
    "mistral": ProviderSpec(
        id="mistral", label="Mistral", route="mistral", native="pi-ai",
        default_base="", auth="bearer", protocols=("chat-completions",),
        discovery_base="https://api.mistral.ai/v1", note="Mistral 官方接口",
    ),
    "moonshotai-cn": ProviderSpec(
        id="moonshotai-cn", label="Moonshot (Kimi) 国内", route="moonshotai-cn", native="pi-ai",
        default_base="https://api.moonshot.cn/v1", auth="bearer",
        protocols=("chat-completions",), discovery_base="https://api.moonshot.cn/v1",
        note="月之暗面 Kimi 国内接口",
    ),
    "zai": ProviderSpec(
        id="zai", label="Z.AI (GLM)", route="zai", native="pi-ai",
        default_base="https://api.z.ai/api/coding/paas/v4", auth="bearer",
        protocols=("chat-completions",),
        discovery_base="https://api.z.ai/api/coding/paas/v4",
        note="智谱 GLM 编码套餐接口",
    ),
    "custom": ProviderSpec(
        id="custom", label="自定义网关", route="custom", native="pi-ai",
        default_base="", auth="bearer", protocols=("chat-completions", "messages"),
        requires_base=True, note="OpenAI / Anthropic 兼容网关",
    ),
}

# pi-ai 协议名 (自定义路由必须显式声明)
PI_AI_API = {
    "chat-completions": "openai-completions",
    "messages": "anthropic-messages",
}

# 旧版本把 DeepSeek 的展示名写成了模型 id, 读到时纠正
LEGACY_MODELS = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-flash-latest": "deepseek-flash",
}

DEFAULT_MODELS = {
    "deepseek": ("deepseek-flash", "DeepSeek V4.1 Flash"),
    "openai": ("gpt-4.1", "GPT-4.1"),
    "anthropic": ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
    "openrouter": ("openai/gpt-4.1", "OpenAI: GPT-4.1"),
}


def spec_of(provider: str) -> ProviderSpec:
    return PROVIDERS.get((provider or "").strip(), PROVIDERS["deepseek"])


def default_model(provider: str) -> tuple[str, str]:
    return DEFAULT_MODELS.get(spec_of(provider).id, ("", ""))


def normalize_model(provider: str, model: str) -> str:
    value = (model or "").strip()[:128]
    if spec_of(provider).id == "deepseek":
        return LEGACY_MODELS.get(value, value)
    return value


def pretty_name(model: str) -> str:
    """没有显示名称时把模型 id 变成可读标签."""
    text = (model or "").strip()
    if not text:
        return ""
    text = text.split("/")[-1].split(":")[0]
    text = re.sub(r"[-_]+", " ", text)
    parts = []
    for token in text.split():
        low = token.lower()
        if low in ("gpt", "ai", "llm"):
            parts.append(token.upper())
        elif low.isdigit() or re.fullmatch(r"\d+(\.\d+)?", low):
            parts.append(token)
        else:
            parts.append(token[:1].upper() + token[1:])
    return " ".join(parts)


def display_name(provider: str, model: str, name: str = "") -> str:
    """优先用户填写的显示名称, 其次默认目录名, 最后从 id 推导."""
    value = (name or "").strip()[:128]
    if value:
        return value
    default_id, default_name = default_model(provider)
    if model and model == default_id:
        return default_name
    return pretty_name(model)
