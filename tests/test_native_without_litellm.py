import builtins
from unittest.mock import MagicMock

import pytest

from aider.models import Model, ModelInfoManager
from aider.providers.base import ProviderError
from aider.providers.environment import resolve_environment


@pytest.mark.parametrize("name,protocol,wire_model,key", [
    ("gpt-4o", "openai-chat", "gpt-4o", "OPENAI_API_KEY"),
    ("openai/responses/gpt-6", "openai-responses", "gpt-6", "OPENAI_API_KEY"),
    ("anthropic/claude-sonnet-test", "anthropic", "claude-sonnet-test", "ANTHROPIC_API_KEY"),
    ("deepseek/deepseek-chat", "openai-chat", "deepseek-chat", "DEEPSEEK_API_KEY"),
])
def test_environment_completion_without_litellm(name, protocol, wire_model, key, monkeypatch):
    original_import = builtins.__import__

    def guarded_import(module, *args, **kwargs):
        if module == "litellm" or module.startswith("litellm."):
            raise AssertionError("原生流程不能导入 LiteLLM")
        return original_import(module, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv(key, "test-key")
    model = Model(name, weak_model=False, editor_model=False)
    provider = model.native_provider()
    assert provider.protocol == protocol
    assert provider.model == wire_model
    provider.create_completion = MagicMock(return_value="test-response")
    _, response = model.send_completion([{"role": "user", "content": "hello"}], None, False)
    assert response == "test-response"
    assert model.token_count("hello") > 0


def test_missing_provider_key_does_not_use_another_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    model = Model("deepseek/deepseek-chat", weak_model=False, editor_model=False)
    assert model.missing_keys == ["DEEPSEEK_API_KEY"]
    with pytest.raises(ProviderError, match="API Key"):
        model.native_provider()


def test_bundled_metadata_without_network(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: pytest.fail("不应联网拉取模型目录"))
    manager = ModelInfoManager()
    manager._cache_loaded = True
    manager.content = None
    assert manager.get_model_info("gpt-4")["max_input_tokens"] == 8192
    assert manager.get_model_info("no-such-model") == {}


def test_environment_base_url_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_BASE", "http://localhost:1234/v1")
    config, variable = resolve_environment("deepseek/deepseek-chat")
    assert config["api_base"] == "http://localhost:1234/v1"
    assert variable == "DEEPSEEK_API_KEY"
