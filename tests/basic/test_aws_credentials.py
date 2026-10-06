import pytest
from aider.providers.environment import resolve_environment
from aider.providers.base import ProviderError


@pytest.mark.parametrize("model", ["bedrock/anthropic.claude-v2", "vertex_ai/gemini", "azure/gpt-4"])
def test_special_authentication_requires_explicit_compatible_endpoint(model,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","test-key")
    monkeypatch.setenv("AWS_PROFILE","test-profile")
    with pytest.raises(ProviderError,match="aider admin"):
        resolve_environment(model)
