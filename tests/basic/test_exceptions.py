import httpx
import openai
import anthropic
import pytest

from aider.exceptions import ProviderExceptions


@pytest.mark.parametrize("sdk", [openai, anthropic])
@pytest.mark.parametrize("name,status,retry", [
    ("AuthenticationError",401,False), ("PermissionDeniedError",403,False),
    ("BadRequestError",400,False), ("RateLimitError",429,True),
    ("InternalServerError",500,True),
])
def test_native_errors(sdk,name,status,retry):
    response=httpx.Response(status,request=httpx.Request("POST","http://localhost"))
    error=getattr(sdk,name)("test",response=response,body=None)
    errors=ProviderExceptions()
    assert isinstance(error,errors.exceptions_tuple())
    assert errors.get_ex_info(error).retry is retry


def test_context_window_error():
    response=httpx.Response(400,request=httpx.Request("POST","http://localhost"))
    error=openai.BadRequestError("context_length_exceeded",response=response,body=None)
    info=ProviderExceptions().get_ex_info(error)
    assert info.name=="ContextWindowExceededError"
    assert not info.retry


def test_connection_error():
    error=openai.APIConnectionError(request=httpx.Request("POST","http://localhost"))
    assert ProviderExceptions().get_ex_info(error).retry
