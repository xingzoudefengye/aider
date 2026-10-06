from unittest.mock import patch
import pytest
from aider import llm
from aider.providers import get_provider


@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses", "anthropic"])
def test_native_client_ssl_setting(protocol,monkeypatch):
    monkeypatch.setattr(llm,"VERIFY_SSL",False)
    provider=get_provider("test-model",api_key="test-key",protocol=protocol)
    with patch("httpx.Client") as client, patch("openai.OpenAI"), patch("anthropic.Anthropic"):
        provider.client
        client.assert_called_once_with(verify=False)


def test_default_ssl_verification(monkeypatch):
    monkeypatch.setattr(llm,"VERIFY_SSL",True)
    assert llm.client_options()=={}
