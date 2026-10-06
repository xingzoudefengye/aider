"""将命令行环境配置解析为原生三协议连接。"""

import os

from .base import ProviderError


# 这里只登记使用三种原生协议的入口，不推测专用云服务的认证方式。
ENDPOINTS = {
    "openai": ("OPENAI_API_KEY", None),
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    "fireworks_ai": ("FIREWORKS_API_KEY", "https://api.fireworks.ai/inference/v1"),
    "together_ai": ("TOGETHERAI_API_KEY", "https://api.together.xyz/v1"),
    "xai": ("XAI_API_KEY", "https://api.x.ai/v1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    "ollama": (None, "http://localhost:11434/v1"),
    "ollama_chat": (None, "http://localhost:11434/v1"),
}


def resolve_environment(model):
    provider, separator, name = model.partition("/")
    if not separator:
        provider = "anthropic" if model.startswith("claude") else "openai"
        name = model
    protocol = "anthropic" if provider == "anthropic" else "openai-chat"
    if provider == "openai" and name.startswith("responses/"):
        protocol, name = "openai-responses", name[len("responses/"):]
    if provider not in ENDPOINTS:
        raise ProviderError(
            f"{provider} 尚无原生适配。请通过 aider admin 配置兼容的 Chat、Responses 或 Anthropic 接口。"
        )
    variable, default_base = ENDPOINTS[provider]
    base = os.environ.get(f"{provider.upper()}_API_BASE")
    if provider == "openai":
        base = base or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get(variable) if variable else "ollama"
    return {"model": name, "protocol": protocol, "api_key": key,
            "api_base": base or default_base}, variable
