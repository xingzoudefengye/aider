"""Provider 基类"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ProviderError(RuntimeError):
    """原生流错误，交由 CLI 现有错误提示流程处理。"""


class ModelProvider(ABC):
    """模型供应商抽象基类"""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        **kwargs
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.extra_params = kwargs

    @abstractmethod
    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Any:
        """创建补全请求（同步）"""
        pass

    @abstractmethod
    def create_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ):
        """创建补全请求（流式）"""
        pass

    @property
    @abstractmethod
    def protocol(self) -> str:
        """协议名称: openai-chat / anthropic / openai-responses"""
        pass
