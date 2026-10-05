"""OpenAI Responses Provider。"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from .base import ModelProvider, ProviderError


class OpenAIResponsesProvider(ModelProvider):
    """将 Responses API 的结果投影为 Aider 使用的 Chat 风格结果。"""

    def __init__(self, model: str, api_key: str, api_base: Optional[str] = None,
                 organization: Optional[str] = None, **kwargs):
        super().__init__(model, api_key, api_base, **kwargs)
        self.organization = organization
        self._client = None

    @property
    def client(self):
        """延迟初始化客户端，便于离线测试和启动 CLI。"""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.api_base,
                                  organization=self.organization)
        return self._client

    @property
    def protocol(self) -> str:
        return "openai-responses"

    @staticmethod
    def _input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for message in messages:
            role = message["role"]
            content = message["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            blocks = []
            for item in content:
                block = {k: v for k, v in item.items() if k != "cache_control"}
                if block.get("type") == "text":
                    block["type"] = "output_text" if role == "assistant" else "input_text"
                elif block.get("type") == "image_url":
                    image = block["image_url"]
                    block = {"type": "input_image", "image_url": image["url"],
                             "detail": image.get("detail", "auto")}
                blocks.append(block)
            result.append({"role": role, "content": blocks})
        return result

    @staticmethod
    def _tool(tool: Dict[str, Any]) -> Dict[str, Any]:
        if tool.get("type") != "function":
            return dict(tool)
        function = tool.get("function", {})
        return {"type": "function", "name": function.get("name"),
                "description": function.get("description"),
                "parameters": function.get("parameters", {"type": "object"})}

    @staticmethod
    def _usage(usage):
        if usage is None:
            return None
        details = getattr(usage, "input_tokens_details", None)
        return SimpleNamespace(
            prompt_tokens=getattr(usage, "input_tokens", 0),
            completion_tokens=getattr(usage, "output_tokens", 0),
            prompt_tokens_details=details,
        )

    @staticmethod
    def _finish_reason(response):
        incomplete = getattr(getattr(response, "incomplete_details", None), "reason", None)
        return "length" if incomplete in {"max_output_tokens", "content_filter"} else "stop"

    def _normalize_response(self, response):
        text = getattr(response, "output_text", "") or ""
        calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                calls.append(SimpleNamespace(
                    id=getattr(item, "call_id", None),
                    function={"name": getattr(item, "name", ""),
                              "arguments": getattr(item, "arguments", "{}")}))
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=calls),
                finish_reason=self._finish_reason(response))],
            usage=self._usage(getattr(response, "usage", None)),
        )

    def create_completion(self, messages: List[Dict[str, Any]],
                          temperature: Optional[float] = None,
                          max_tokens: Optional[int] = None, stream: bool = False,
                          tools: Optional[List] = None, tool_choice: Optional[Dict] = None,
                          timeout: Optional[float] = None,
                          extra_headers: Optional[Dict] = None,
                          prompt_cache_key: Optional[str] = None,
                          predicted_output: Optional[Dict[str, Any]] = None, **kwargs) -> Any:
        params = {"model": self.model, "input": self._input(messages), "stream": stream}
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_output_tokens"] = max_tokens
        if tools is not None:
            params["tools"] = [self._tool(tool) for tool in tools]
        if tool_choice is not None:
            params["tool_choice"] = tool_choice
        if timeout is not None:
            params["timeout"] = timeout
        if extra_headers:
            params["extra_headers"] = extra_headers
        if prompt_cache_key:
            params["prompt_cache_key"] = prompt_cache_key
        if predicted_output is not None:
            params["prediction"] = predicted_output
        params.update(kwargs)
        params.pop("stream_options", None)
        effort = params.pop("reasoning_effort", None)
        if effort and not self.model.startswith(("gpt-4", "gpt-3")):
            params.setdefault("reasoning", {"effort": effort})
        if self.model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            params.pop("temperature", None)
        if isinstance(params.get("tool_choice"), dict) and params["tool_choice"].get("type") == "function":
            params["tool_choice"] = {"type": "function", "name": params["tool_choice"]["function"]["name"]}
        response = self.client.responses.create(**params)
        return ResponsesCompletionStream(response, self) if stream else self._normalize_response(response)

    def create_completion_stream(self, messages: List[Dict[str, Any]], **kwargs):
        return self.create_completion(messages, stream=True, **kwargs)


class ResponsesCompletionStream:
    """把 Responses 事件转换成 Aider 编辑循环消费的增量 chunk。"""

    def __init__(self, stream, provider):
        self.stream = stream
        self.provider = provider
        self.usage = None

    def __iter__(self):
        try:
            for event in self.stream:
                event_type = getattr(event, "type", "")
                delta = SimpleNamespace(content=None, reasoning_content=None)
                finish_reason = None
                if event_type == "response.output_text.delta":
                    delta.content = getattr(event, "delta", "")
                elif event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if getattr(item, "type", None) == "function_call":
                        delta.function_call = {"name": getattr(item, "name", ""), "arguments": ""}
                    else:
                        continue
                elif event_type == "response.function_call_arguments.delta":
                    delta.function_call = {"arguments": getattr(event, "delta", "")}
                elif event_type in {"response.completed", "response.incomplete"}:
                    response = getattr(event, "response", None)
                    self.usage = self.provider._usage(getattr(response, "usage", None))
                    finish_reason = self.provider._finish_reason(response)
                elif event_type in {"response.failed", "error"}:
                    raise ProviderError(f"OpenAI Responses stream error: {getattr(event, 'error', None)}")
                else:
                    continue
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta,
                                                               finish_reason=finish_reason)], usage=self.usage)
        finally:
            close = getattr(self.stream, "close", None)
            if close:
                close()
