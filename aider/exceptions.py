from dataclasses import dataclass

from aider.dump import dump  # noqa: F401


@dataclass
class ExInfo:
    name: str
    retry: bool
    description: str


EXCEPTIONS = [
    ExInfo("APIConnectionError", True, None),
    ExInfo("APIError", True, None),
    ExInfo("APIResponseValidationError", True, None),
    ExInfo(
        "AuthenticationError",
        False,
        "The API provider is not able to authenticate you. Check your API key.",
    ),
    ExInfo("AzureOpenAIError", True, None),
    ExInfo("BadGatewayError", True, "The API provider's servers are down or overloaded."),
    ExInfo("BadRequestError", False, None),
    ExInfo("BudgetExceededError", True, None),
    ExInfo(
        "ContentPolicyViolationError",
        True,
        "The API provider has refused the request due to a safety policy about the content.",
    ),
    ExInfo("ContextWindowExceededError", False, None),  # special case handled in base_coder
    ExInfo("ImageFetchError", False, "The API provider was unable to fetch one or more images."),
    ExInfo("InternalServerError", True, "The API provider's servers are down or overloaded."),
    ExInfo("InvalidRequestError", True, None),
    ExInfo("JSONSchemaValidationError", True, None),
    ExInfo("NotFoundError", False, None),
    ExInfo(
        "PermissionDeniedError",
        False,
        "Permission was denied. Check your API key and/or credentials.",
    ),
    ExInfo("OpenAIError", True, None),
    ExInfo(
        "RateLimitError",
        True,
        "The API provider has rate limited you. Try again later or check your quotas.",
    ),
    ExInfo("RouterRateLimitError", True, None),
    ExInfo("ServiceUnavailableError", True, "The API provider's servers are down or overloaded."),
    ExInfo("UnprocessableEntityError", True, None),
    ExInfo("UnsupportedParamsError", True, None),
    ExInfo(
        "Timeout",
        True,
        "The API provider timed out without returning a response. They may be down or overloaded.",
    ),
]


class ProviderExceptions:
    exceptions = dict()
    exception_info = {exi.name: exi for exi in EXCEPTIONS}

    def __init__(self):
        import openai
        import anthropic
        from aider.providers.base import ProviderError

        self.exceptions = {}
        for sdk in (openai, anthropic):
            for name in ("APIConnectionError", "APITimeoutError", "AuthenticationError",
                         "BadRequestError", "NotFoundError", "PermissionDeniedError",
                         "RateLimitError", "InternalServerError", "APIStatusError",
                         "APIResponseValidationError"):
                error = getattr(sdk, name, None)
                if error:
                    info = self.exception_info.get(name, ExInfo(name, name == "APITimeoutError", None))
                    self.exceptions[error] = info
        self.exceptions[ProviderError] = ExInfo("ProviderError", False, None)

    def exceptions_tuple(self):
        return tuple(self.exceptions)

    def get_ex_info(self, ex):
        """Return the ExInfo for a given exception instance"""
        if any(marker in str(ex).lower() for marker in ("context_length_exceeded", "prompt is too long")):
            return self.exception_info["ContextWindowExceededError"]
        # SDK 基类也可能承载代理返回的非标准状态码，按 HTTP 状态决定是否重试。
        status = getattr(ex, "status_code", None)
        if status is not None:
            retry = status in (408, 409, 429) or status >= 500
            info = self.exceptions.get(type(ex), ExInfo(type(ex).__name__, retry, None))
            return ExInfo(info.name, retry, info.description)
        return self.exceptions.get(type(ex), ExInfo(type(ex).__name__, False, None))
