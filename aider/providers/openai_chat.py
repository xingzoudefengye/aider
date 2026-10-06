"""OpenAI Chat Completions Provider"""

from typing import Any, Dict, List, Optional
from types import SimpleNamespace

from aider.llm import client_options

from .base import ModelProvider


class OpenAIChatProvider(ModelProvider):
    """OpenAI Chat Completions API 原生实现"""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        organization: Optional[str] = None,
        **kwargs
    ):
        super().__init__(model, api_key, api_base, **kwargs)
        self.organization = organization
        self._client = None

    @property
    def client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                **client_options(),
                organization=self.organization,
            )
        return self._client

    @property
    def protocol(self) -> str:
        return "openai-chat"

    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        tools: Optional[List] = None,
        tool_choice: Optional[Dict] = None,
        timeout: Optional[float] = None,
        extra_headers: Optional[Dict] = None,
        **kwargs
    ) -> Any:
        """创建 Chat Completion"""
        params = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
        if timeout is not None:
            params["timeout"] = timeout
        if extra_headers:
            params["extra_headers"] = extra_headers

        # 合并额外参数
        params.update(kwargs)
        # 中转和兼容供应商扩展字段放入请求体，不能直接作为 SDK 关键字参数。
        body = dict(params.get("extra_body") or {})
        for name in ("include_reasoning", "top_k", "top_a", "min_p", "repetition_penalty",
                     "transforms", "provider"):
            if name in params:
                body[name] = params.pop(name)
        if body:
            params["extra_body"] = body
        if stream:
            params.setdefault("stream_options", {"include_usage": True})
        if self.model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            params.pop("temperature", None)
            if "max_tokens" in params:
                params["max_completion_tokens"] = params.pop("max_tokens")
        if self.model.startswith(("gpt-4", "gpt-3")):
            params.pop("reasoning_effort", None)
        response = self.client.chat.completions.create(**params)
        if stream:
            return self._stream(response)
        for choice in response.choices:
            for call in choice.message.tool_calls or []:
                call.function = call.function.model_dump()
        return response

    @staticmethod
    def _stream(response):
        """保留末尾 usage，同时将 SDK 的工具增量转换为现有编辑接口。"""
        try:
            for chunk in response:
                for choice in chunk.choices:
                    delta = choice.delta
                    function = getattr(delta, "function_call", None)
                    if function is None and getattr(delta, "tool_calls", None):
                        function = delta.tool_calls[0].function
                    if function is not None:
                        choice.delta = SimpleNamespace(
                            content=delta.content,
                            reasoning_content=getattr(delta, "reasoning_content", None),
                            function_call=function.model_dump(exclude_none=True),
                        )
                yield chunk
        finally:
            close = getattr(response, "close", None)
            if close:
                close()

    def create_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ):
        """创建流式 Chat Completion"""
        return self.create_completion(messages, stream=True, **kwargs)
