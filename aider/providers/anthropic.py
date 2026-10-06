"""Anthropic Messages Provider"""

import json
import inspect
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from aider.llm import client_options

from .base import ModelProvider, ProviderError


class AnthropicMessagesProvider(ModelProvider):
    """Anthropic Messages API 原生实现"""

    BETA_HEADER = "prompt-caching-2024-07-31,pdfs-2024-09-25"

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs
    ):
        super().__init__(model, api_key, api_base, **kwargs)
        self.session_id = session_id
        self._client = None

    @property
    def client(self):
        """延迟初始化 Anthropic 客户端"""
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic(
                api_key=self.api_key,
                base_url=self.api_base,
                **client_options(),
            )
        return self._client

    @property
    def protocol(self) -> str:
        return "anthropic"

    def _split_messages(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """分离 system 消息和用户/助手消息"""
        system = []
        user_msgs = []
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = []
                for block in content:
                    block = dict(block)
                    if block.get("type") == "image_url":
                        url = block["image_url"]["url"]
                        if url.startswith("data:"):
                            header, data = url.split(",", 1)
                            source = {
                                "type": "base64",
                                "media_type": header[5:].split(";", 1)[0],
                                "data": data,
                            }
                        else:
                            source = {"type": "url", "url": url}
                        block = {"type": "image", "source": source,
                                 **({"cache_control": block["cache_control"]}
                                    if "cache_control" in block else {})}
                    blocks.append(block)
            # 保留文本块上的 cache_control，不修改原始消息。
            if message.get("role") == "system":
                system.extend(blocks)
            else:
                user_msgs.append({"role": message["role"], "content": blocks})
        return system, user_msgs

    @staticmethod
    def _finish_reason(reason):
        return {"max_tokens": "length", "tool_use": "tool_calls"}.get(reason, "stop")

    @staticmethod
    def _usage(usage):
        return SimpleNamespace(
            prompt_tokens=(usage.input_tokens + (getattr(usage, "cache_read_input_tokens", 0) or 0)
                           + (getattr(usage, "cache_creation_input_tokens", 0) or 0)),
            completion_tokens=usage.output_tokens,
            prompt_cache_hit_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def _normalize_response(self, response):
        # Aider 使用 choices 接口；HTTP 请求仍使用 Anthropic 原生协议。
        text = []
        thinking = []
        calls = []
        for block in response.content:
            if block.type == "text":
                text.append(block.text)
            elif block.type == "thinking":
                thinking.append(block.thinking)
            elif block.type == "tool_use":
                calls.append(SimpleNamespace(function={
                    "name": block.name, "arguments": json.dumps(block.input),
                }))
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content="".join(text), reasoning_content="".join(thinking),
                    tool_calls=calls,
                ),
                finish_reason=self._finish_reason(response.stop_reason),
            )],
            usage=self._usage(response.usage),
        )

    def create_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 4096,
        stream: bool = False,
        tools: Optional[List] = None,
        tool_choice: Optional[Dict] = None,
        timeout: Optional[float] = None,
        extra_headers: Optional[Dict] = None,
        **kwargs
    ) -> Any:
        """创建 Messages API 请求"""
        system, user_msgs = self._split_messages(messages)

        params = {
            "model": self.model,
            "messages": user_msgs,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            "stream": stream,
        }

        if system:
            params["system"] = system

        if temperature is not None:
            # 新版 anthropic SDK 不再接受 temperature；按 SDK 签名兼容不同版本。
            try:
                create_parameters = inspect.signature(self.client.messages.create).parameters
            except (TypeError, ValueError):
                create_parameters = {}
            supports_temperature = "temperature" in create_parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in create_parameters.values()
            )
            if supports_temperature:
                params["temperature"] = temperature

        # 用户标识只作为元数据，不保证缓存命中。
        if self.session_id:
            params["metadata"] = {"user_id": self.session_id}

        # Tools 支持
        if tools is not None:
            params["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "input_schema": tool["function"].get("parameters", {"type": "object"}),
                    **({"description": tool["function"]["description"]}
                       if "description" in tool["function"] else {}),
                } if tool.get("type") == "function" else dict(tool)
                for tool in tools
            ]
        if tool_choice is not None:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                tool_choice = {"type": "tool", "name": tool_choice["function"]["name"]}
            elif isinstance(tool_choice, str):
                tool_choice = {"type": "any" if tool_choice == "required" else tool_choice}
            params["tool_choice"] = tool_choice
        if timeout is not None:
            params["timeout"] = timeout

        # 保留调用方的 headers，缓存边界由消息上的 cache_control 指定。
        headers = dict(extra_headers or {})
        if "anthropic-beta" not in headers:
            headers["anthropic-beta"] = self.BETA_HEADER

        # 合并额外参数
        params.update(kwargs)
        effort = params.pop("reasoning_effort", None)
        params.pop("stream_options", None)
        params.pop("prompt_cache_key", None)
        if effort and self.model.startswith("claude-") and "thinking" not in params:
            # 新版 Claude 使用自适应思考，旧版使用预算；普通代理模型不强塞 Claude 参数。
            if any(version in self.model for version in ("4-6", "4.6", "4-7", "4.7")):
                params["thinking"] = {"type": "adaptive"}
                params.setdefault("output_config", {"effort": effort})
            elif any(version in self.model for version in ("3-7", "3.7", "-4", "4.")):
                budget = {"low": 1024, "medium": 4096, "high": 8192, "xhigh": 16384, "max": 16384}[effort]
                params["thinking"] = {"type": "enabled", "budget_tokens": budget}
                params["max_tokens"] = max(params["max_tokens"], budget + 4096)
            if "thinking" in params:
                params.pop("temperature", None)

        response = self.client.messages.create(
            **params,
            extra_headers=headers
        )
        if stream:
            return AnthropicCompletionStream(response, self)
        return self._normalize_response(response)

    def create_completion_stream(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ):
        """创建流式 Messages API 请求"""
        return self.create_completion(messages, stream=True, **kwargs)


class AnthropicCompletionStream:
    """把原生事件投影为 Aider 可消费的增量，并保存最终用量。"""

    def __init__(self, stream, provider):
        self.stream = stream
        self.provider = provider
        self.usage = None

    def __iter__(self):
        try:
            for event in self.stream:
                delta = SimpleNamespace(content=None, reasoning_content=None)
                finish_reason = None
                if event.type == "message_start":
                    self.usage = self.provider._usage(event.message.usage)
                    continue
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        delta.function_call = {"name": block.name, "arguments": ""}
                    elif block.type == "text":
                        delta.content = block.text
                    else:
                        continue
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        delta.content = event.delta.text
                    elif event.delta.type == "thinking_delta":
                        delta.reasoning_content = event.delta.thinking
                    elif event.delta.type == "input_json_delta":
                        delta.function_call = {"arguments": event.delta.partial_json}
                    else:
                        continue
                elif event.type == "message_delta":
                    if self.usage is not None:
                        self.usage.completion_tokens = event.usage.output_tokens
                    finish_reason = self.provider._finish_reason(event.delta.stop_reason)
                elif event.type == "error":
                    raise ProviderError(f"Anthropic stream error: {event.error}")
                else:
                    continue
                yield SimpleNamespace(choices=[SimpleNamespace(
                    delta=delta, finish_reason=finish_reason,
                )], usage=self.usage)
        finally:
            self.stream.close()
