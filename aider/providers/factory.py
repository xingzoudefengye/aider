"""Provider 工厂 - 根据模型自动选择 Provider"""

import os
from typing import Optional

from .anthropic import AnthropicMessagesProvider
from .base import ModelProvider
from .openai_chat import OpenAIChatProvider
from .openai_responses import OpenAIResponsesProvider

SUPPORTED_PROTOCOLS = {"openai-chat", "anthropic", "openai-responses"}


def get_provider(
    model: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    session_id: Optional[str] = None,
    protocol: Optional[str] = None,
    **kwargs
) -> ModelProvider:
    """
    根据模型名称自动选择 Provider

    Args:
        model: 模型名称
        api_key: API 密钥（可选，默认从环境变量读取）
        api_base: API Base URL（可选）
        session_id: 会话 ID（用于 Anthropic 会话亲和）
        **kwargs: 额外参数

    Returns:
        对应的 ModelProvider 实例
    """
    if protocol and protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            "不支持的 Provider 协议: "
            f"{protocol}；可选值为 {', '.join(sorted(SUPPORTED_PROTOCOLS))}"
        )

    # Web 配置可以显式指定协议；未指定时继续兼容模型名推断。
    if protocol == "anthropic" or (
        not protocol and any(x in model.lower() for x in ["claude", "anthropic"])
    ):
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        return AnthropicMessagesProvider(
            model=model,
            api_key=api_key,
            api_base=api_base,
            session_id=session_id,
            **kwargs
        )

    # Responses 需要显式开启，避免改变现有 Chat Completions 模型的行为。
    if protocol == "openai-responses" or kwargs.pop("use_responses_api", False):
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        organization = kwargs.pop("organization", None) or os.environ.get("OPENAI_ORGANIZATION")
        return OpenAIResponsesProvider(model=model, api_key=api_key, api_base=api_base,
                                       organization=organization, **kwargs)

    # 默认: OpenAI Chat Completions
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    organization = kwargs.pop("organization", None) or os.environ.get("OPENAI_ORGANIZATION")

    return OpenAIChatProvider(
        model=model,
        api_key=api_key,
        api_base=api_base,
        organization=organization,
        **kwargs
    )
